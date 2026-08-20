# Private Server Signatures and Trust

This document describes the client-side contract for private developer servers. The current Access Server is a
technical-validation implementation; it is not the production backend.

## Signed payloads

The client signs and verifies UTF-8 canonical JSON. Canonical JSON uses sorted object keys, compact separators,
`ensure_ascii=false`, and rejects NaN or Infinity. A detached signature envelope has these fields:

```json
{
  "format": "detached-canonical-json",
  "algorithm": "ed25519",
  "encoding": "base64url",
  "key_id": "server-key-1",
  "signature": "..."
}
```

Package manifests are verified before they are shown or installed. Archive and installed-file SHA-256 digests are
verified separately. Missing signatures, an unexpected key id, invalid signatures, and modified content are rejected.

## Trust negotiation

`server-info` advertises an allowlist and optional preference for trust methods (`dnssec`, `https`, `manual`, or
`tofu`), manual transports (`text`, `file`, `qr`, `local_network`, or `fingerprint`), and key encodings
(`base64url`, `base64`, `hex`, or `multibase`). The client chooses from its own conservative allowlist and preference;
the server cannot force a client downgrade or introduce a built-in project key.

On first use, the client displays the server name, negotiated method, and SHA-256 public-key fingerprint. The identity
is persisted only after the user confirms the same fingerprint.

## Key status and offline behavior

`/v1/key-status` must be signed by the currently trusted key and must match the server id and active key id. The
snapshot has `issued_at` and `expires_at`, and its lifetime may not exceed 24 hours. The validator rejects expired,
missing, malformed, and revoked status. The client persists the signed snapshot with the private catalog and rechecks
its signature and expiry before exposing cached packages or starting a private download/install.

## Key rotation

A rotation declaration is signed by both the previous and next Ed25519 keys. It contains the server id, both key ids,
the next public key, a future `effective_at`, and `previous_key_expires_at`. The previous key may overlap for at most
seven days. If the previous trusted key is unavailable, the client does not silently accept the new key; a new
first-use fingerprint confirmation is required. After a valid declaration becomes effective, the client updates its
locally persisted identity and fingerprint. The identity is never synchronized as a private key or embedded project key.

## Validation scope

The repository has unit and deterministic integration coverage for signatures, trust negotiation, first-use identity,
key-status validation and persistence, rotation declarations and persistence, and manifest tampering. Real WebView2
rendering, deployed-server behavior, and production Access Server acceptance remain separate gates.
