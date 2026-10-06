"""证书被拒时，用一次不校验的握手把对端那张证书记下来。

`urllib` 失败时只给一句 unable to get local issuer certificate，看不出对端递过来的到底是谁签的；
而用户上传的日志是唯一的证据。所以这里只做记录：一次 TLS 握手、不发任何 HTTP 请求、不跟随跳转，
也不改变校验结果 —— 校验失败照旧失败。

OpenSSL 不会把整条链交给 Python，所以只有叶子证书：签发者名字已经足够说明「是谁替换了证书」。
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import urlparse

# 失败路径上的补充信息：等太久不如干脆没有。
PEER_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class PeerCertificate:
    """对端在握手时递过来的那张证书。"""

    subject: str
    issuer: str
    not_after: str


def certificate_verification_error(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """异常链里的证书校验错误。

    `urlopen` 把它包在 `URLError.reason` 里，包装还可能有第二层，所以按广度走一遍整条链，
    遇到环就停。
    """
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return current
        for nested in (getattr(current, "reason", None), current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return None


def describe_der_certificate(der: bytes) -> PeerCertificate | None:
    """DER 证书 → 主题 / 签发者 / 到期日；读不出来返回 None。"""
    try:
        from cryptography import x509
    except ImportError:  # 缺它只影响这条补充信息，不影响下载本身
        return None
    try:
        certificate = x509.load_der_x509_certificate(der)
    except ValueError:
        return None
    return PeerCertificate(
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        not_after=certificate.not_valid_after_utc.date().isoformat(),
    )


def peer_certificate(
        host: str,
        port: int = 443,
        *,
        timeout: int = PEER_TIMEOUT_SECONDS,
) -> PeerCertificate | None:
    """不校验证书地握手一次，取对端那张证书；连不上或解不出来返回 None。"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as handshake:
                der = handshake.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError, ValueError):
        return None
    if not der:
        return None
    return describe_der_certificate(der)


def peer_certificate_note(url: str, *, timeout: int = PEER_TIMEOUT_SECONDS) -> str:
    """一行日志用的描述 `issuer=… subject=… not_after=…`；读不出来就是 `unavailable`。

    这里不抛：调用方正在上报一个更要紧的失败，补充信息不许把它顶掉。
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host:
            return "unavailable"
        certificate = peer_certificate(host, parsed.port or 443, timeout=timeout)
    except Exception:  # 端口写坏、地址解析不了之类：只值一句「读不出来」
        return "unavailable"
    if certificate is None:
        return "unavailable"
    return (
        f"issuer={certificate.issuer} subject={certificate.subject}"
        f" not_after={certificate.not_after}"
    )
