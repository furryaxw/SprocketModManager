"""HTTPS 校验用的信任根：系统根库，加上随包带的一份公共根。

Windows 上 Python 只信系统证书库（`create_default_context()` 的 `load_default_certs()`），库里少了
一个公共根就整条链验不过，而根库靠 Windows 的自动根证书更新补齐 —— 那一步在有些网络下到不了。
所以这里再叠一份 certifi 的公共根（Mozilla 那份）：两边取并集，企业自建根照旧被信任，缺公共根的
机器也能验过。certifi 不在时（从源码跑又没装依赖）退回只有系统根库，与不做这件事时一样。
"""

from __future__ import annotations

import logging
import ssl
from functools import lru_cache
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def bundled_roots() -> Path | None:
    """随包带的那份公共根；没有、或数据文件没打进去时返回 None。"""
    try:
        import certifi
    except ImportError:
        return None
    try:
        path = Path(certifi.where())
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return path if path.is_file() else None


@lru_cache(maxsize=1)
def default_ssl_context() -> ssl.SSLContext:
    """校验用的上下文：系统根库 ∪ 内置公共根，证书与主机名照常校验。

    只建一次：加载那份证书包有成本，信任根在一个进程里不会变。用哪几个根落在日志里，
    这样用户传上来的日志能说明当时生效的是哪一种。
    """
    context = ssl.create_default_context()
    roots = bundled_roots()
    if roots is None:
        LOGGER.info("TLS trust roots: system store only (no bundled public roots)")
        return context
    try:
        context.load_verify_locations(cafile=str(roots))
    except (OSError, ssl.SSLError) as exc:  # 内置那份坏了也照常干活
        LOGGER.warning("TLS trust roots: system store only (bundled roots unusable: %s)", exc)
        return context
    LOGGER.info("TLS trust roots: system store + bundled public roots (%s)", roots.name)
    return context
