# Private Server Recovery Gist

The private Gist is only a recovery index for registered developer servers. It
is not an authentication store and is never used as a package or permission
source.

Fixed filename: `sprocket-mod-manager-servers.json`

```json
{
  "schema_version": 1,
  "servers": [
    {
      "server_id": "local-test-server",
      "url": "https://mods.example.invalid",
      "name": "Example distribution",
      "public_key_fingerprint": "sha256:..."
    }
  ]
}
```

Only `server_id`, normalized `url`, display `name`, and the optional public-key
fingerprint may be synchronized. The Gist must never contain GitHub access
tokens, server session tokens, Keys, package manifests, download URLs, grant
data, or local installation state.

The Manager merges entries by `server_id`. A URL, name, or fingerprint change
is treated as a candidate update and requires local confirmation before it can
replace an existing trusted entry. A missing entry is not deleted locally
without an explicit user action.
