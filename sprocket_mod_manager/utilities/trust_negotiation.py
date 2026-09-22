"""Decentralized server identity and signature capability negotiation."""

from __future__ import annotations

from typing import Iterable

TRUST_METHODS = ("https", "dnssec", "manual", "tofu", "web_of_trust")
ENCODINGS = ("base64url", "base64", "multibase", "hex")
MANUAL_TRANSPORTS = ("text", "file", "qr", "local_network", "fingerprint")

# Client defaults are deliberately conservative. Server advertisements are only
# an allowlist and ordering hint; they cannot enable a method the client rejects.
DEFAULT_CLIENT_TRUST_METHODS = frozenset({"https", "dnssec", "manual", "tofu"})
DEFAULT_CLIENT_TRUST_PREFERENCE = ("dnssec", "https", "manual", "tofu")
DEFAULT_SERVER_TRUST_METHODS = ("https", "manual", "tofu")
DEFAULT_CLIENT_ENCODINGS = frozenset(ENCODINGS)
DEFAULT_SERVER_ENCODINGS = ("base64url", "base64", "hex")
DEFAULT_SERVER_MANUAL_TRANSPORTS = ("text", "file", "qr", "local_network", "fingerprint")
DEFAULT_CLIENT_MANUAL_TRANSPORTS = frozenset(MANUAL_TRANSPORTS)


def _ordered(values: Iterable[object], allowed: set[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = str(value).strip().casefold()
        if item in allowed and item not in result:
            result.append(item)
    return tuple(result)


def negotiate_trust_method(
    server_methods: Iterable[object] | None,
    *,
    server_preferred: object = "",
    client_allowed: Iterable[object] = DEFAULT_CLIENT_TRUST_METHODS,
    client_preference: Iterable[object] = DEFAULT_CLIENT_TRUST_PREFERENCE,
) -> str:
    """Return the safest available method, honoring server restrictions.

    The server list is an allowlist. ``server_preferred`` is only considered
    after the client's own preference order, so a server cannot force a weaker
    client policy.
    """
    allowed = {str(item).strip().casefold() for item in client_allowed}
    advertised = _ordered(
        server_methods if server_methods is not None else DEFAULT_SERVER_TRUST_METHODS,
        set(TRUST_METHODS),
    )
    candidates = set(advertised) & allowed
    if not candidates:
        raise ValueError("server and client have no compatible trust method")
    preference = _ordered(client_preference, set(TRUST_METHODS))
    for item in preference:
        if item in candidates:
            return item
    preferred = str(server_preferred).strip().casefold()
    if preferred in candidates:
        return preferred
    return next(item for item in advertised if item in candidates)


def negotiate_manual_transport(
    server_transports: Iterable[object] | None,
    *,
    server_preferred: object = "",
    client_allowed: Iterable[object] = DEFAULT_CLIENT_MANUAL_TRANSPORTS,
    client_preference: Iterable[object] = MANUAL_TRANSPORTS,
) -> str:
    advertised = _ordered(
        server_transports if server_transports is not None else DEFAULT_SERVER_MANUAL_TRANSPORTS,
        set(MANUAL_TRANSPORTS),
    )
    candidates = set(advertised) & {str(item).strip().casefold() for item in client_allowed}
    if not candidates:
        raise ValueError("server and client have no compatible manual transport")
    for item in _ordered(client_preference, set(MANUAL_TRANSPORTS)):
        if item in candidates:
            return item
    preferred = str(server_preferred).strip().casefold()
    if preferred in candidates:
        return preferred
    return next(item for item in advertised if item in candidates)


def negotiate_encoding(
    server_encodings: Iterable[object] | None,
    *,
    server_preferred: object = "",
    client_allowed: Iterable[object] = DEFAULT_CLIENT_ENCODINGS,
    client_preference: Iterable[object] = ENCODINGS,
) -> str:
    allowed = {str(item).strip().casefold() for item in client_allowed}
    advertised = _ordered(
        server_encodings if server_encodings is not None else DEFAULT_SERVER_ENCODINGS,
        set(ENCODINGS),
    )
    candidates = set(advertised) & allowed
    if not candidates:
        raise ValueError("server and client have no compatible encoding")
    preferred = str(server_preferred).strip().casefold()
    if preferred in candidates:
        return preferred
    for item in _ordered(client_preference, set(ENCODINGS)):
        if item in candidates:
            return item
    return next(item for item in advertised if item in candidates)
