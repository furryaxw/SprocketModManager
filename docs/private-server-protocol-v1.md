# Private Developer Server Protocol v1

This document freezes the Manager-facing contract for the production server
rewrite. The current Access Server is a technical-validation implementation;
its database, admin UI, and deployment model are not part of this contract.

## Transport

- HTTPS is required for non-loopback servers. HTTP is allowed only for loopback
  development endpoints.
- JSON endpoints use `Accept: application/json` and JSON request bodies.
- Binary downloads use `Accept: application/octet-stream`.
- Responses must stay within the Manager response and archive limits.
- `protocol_version` must equal `1`; unknown versions are rejected.

## Error Contract

Every non-2xx JSON response must contain:

```json
{
  "code": "invalid_session",
  "message": "session is invalid or expired"
}
```

`code` is the stable machine contract. `message` is diagnostic text and may be
localized or changed. The Manager must not branch on message text.

Required authentication codes include `invalid_session`, `github_identity_rejected`,
and `authorization_expired`. Download authorization failures must use a stable
code even when the response is `404` to avoid leaking package existence.

## Server Identity

`GET /v1/server-info` returns `protocol_version`, `server_id`, `name`, trust
negotiation policy, and the Ed25519 signing identity. A changed identity must
carry a verifiable rotation declaration. The Manager requires explicit
fingerprint confirmation on first use.

## Authentication

- `POST /v1/auth/github/exchange` accepts a GitHub access token over TLS.
- The server verifies the token against GitHub and derives the numeric user ID.
- The response creates a server-scoped session token with expiry.
- The OAuth token is never persisted by the server as a reusable Manager
  session credential.
- `POST /v1/auth/session/revoke` revokes the authenticated server session.

The Manager sends a Bearer session token for authenticated requests. Numeric
GitHub IDs supplied by the client are accepted only for the unauthenticated
technical-validation path and must never override an authenticated session.

## Keys and Entitlements

- `POST /v1/keys/redeem` is idempotent when supplied with `Idempotency-Key`.
- Key redemption creates or extends independent user grants.
- `GET /v1/entitlements` returns active permissions and grant states.
- Expired or revoked grants must immediately disappear from active permissions.

## Packages and Downloads

- `GET /v1/packages` returns only packages for which the current identity has
  an active permission.
- Manifests and release metadata are signed with the advertised Ed25519 key.
- `GET /v1/packages/{id}/download` rechecks session validity and permission at
  request time; cached manifests are not authorization.
- The production server should issue a short-lived download authorization
  token or URL. It must be bound to package/version, user/session, and expiry,
  and must not grant access after revocation.
- The Manager validates origin, size, digest, archive contents, and signature
  before installation.

## Caching and Offline Behavior

The Manager may use a signed catalog cache while a server is offline. It must
not use cached permissions or cached download URLs to bypass strict key-status,
session, expiry, or revocation checks. A missing, expired, malformed, or
revoked signing status blocks offline installation.

## Required Server Tests

The production rewrite must test error codes, concurrent redemption, session
expiry/revocation, grant expiry/revocation, short-lived download authorization,
signature rotation, archive limits, and replayed idempotency keys.
