# Sprocket Mod Registry Specification v1

[中文](sprocket-mod-spec.md) | **English**

The Registry is hosted on GitHub Pages and stores only package-level metadata.
Versions, tags, Release assets, and binaries always come from each mod's own GitHub
repository.

## Data Flow

```text
Pages index.json
  -> mods/<id>/sprocket-mod.json
  -> GitHub API /repos/<owner>/<repo>/releases
  -> select a compatible tag and Release assets
  -> download and statically inspect DLLs
  -> Mods / Plugins / UserLibs
```

Pages does not store:

- current or historical version numbers;
- tag lists;
- Release download URLs;
- Release asset digests;
- mod binaries.

## Base Metadata

```jsonc
{
  "schema_version": 1,
  "id": "furryaxw.sprocket-laser-rangefinder",
  "name": "SprocketLaserRangefinder",
  "authors": ["furryAxw"],
  "repository": "furryaxw/SprocketLaserRangefinder",
  "license": "GPL-3.0-only",

  "display_name": {
    "en": "Sprocket Laser Rangefinder",
    "zh-Hans": "Sprocket 激光测距仪"
  },
  "description": {
    "en": "Laser rangefinder and ballistic sight."
  },

  "release": {
    "include_prerelease": false,
    "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
    "assets": {
      "include": ["*.dll", "*.zip"],
      "exclude": ["*debug*", "*symbols*", "*source*"]
    }
  },

  "dependencies": [
    {
      "id": "furryaxw.sprocket-depth",
      "version": ">=0.1.0 <1.0.0",
      "when": "*"
    }
  ],
  "recommendations": ["furryaxw.sprocket-jitter-fix"],
  "featured": true,

  "install": {
    "scan_dlls": true,
    "exclude": [],
    "overrides": []
  },

  "category": "utility",
  "tags": ["optics", "rangefinder"]
}
```

`display_name` is required but needs only one language. The entire `description`
field is optional. When present, it must contain at least one non-empty translation,
and its languages do not need to match those in `display_name`.

Localization keys use open-ended BCP 47-style language tags such as `en`, `ja`,
`zh-Hans`, `zh-Hant`, `pt-BR`, or the private-use tag `x-example`. The Registry does
not maintain a fixed list, so adding languages in the future requires no schema or
client change. The client tries the full UI language, a translation from the same
language family, English, and finally the first available translation. It falls
back to the assembly name only when no display name exists at all.

The first capture group in `version_pattern` must be SemVer. The client ignores
drafts. `include_prerelease` controls whether GitHub prereleases and versions with a
prerelease suffix are allowed.

## Dependencies

`version` constrains the dependency's version, while `when` constrains the current
package's version:

```json
{
  "id": "example.shared-library",
  "version": ">=2.0.0 <3.0.0",
  "when": ">=1.5.0"
}
```

Constraints support `*`, exact versions, comparison operators, `^`, and `~`.
Multiple dependency rules may target different versions of the current package.
Registry CI rejects missing dependencies and static dependency cycles.

## Recommended Mods

`recommendations` is an optional list of registered package IDs. Entries must be
unique, cannot refer to the current package, and do not participate in dependency
resolution. The client presents them as unchecked boxes in the install confirmation;
only recommendations explicitly selected by the user become independent install roots
and resolve their own dependencies.

## New-install Recommendations

`featured` is an optional boolean and defaults to `false`. When it is `true`, the
Registry site shows a star. The client shows the star, pins the package above regular
results, and labels its detail view only while `Mods` contains no DLL. Once any mod
exists, the client returns to its normal presentation and sorting. This never opens a
prompt, selects, or installs the mod.

## DLL Classification

The client reads PE/.NET metadata only. It does not use `Assembly.Load` or execute
downloaded content.

1. Explicit rules in `install.overrides` take priority.
2. When a ZIP already has top-level `Mods`, `Plugins`, or `UserLibs` directories,
   their intent is preserved.
3. Assemblies inheriting `MelonLoader.MelonMod` go to `Mods`.
4. Assemblies inheriting `MelonLoader.MelonPlugin` go to `Plugins`.
5. Other managed assemblies go to `UserLibs`.
6. Native DLLs and non-DLL files are not installed by default and require an
   override.

Example override:

```json
{
  "match": "assets/*.bundle",
  "target": "UserData/MyMod/assets"
}
```

Targets must be inside `Mods`, `Plugins`, `UserLibs`, or `UserData`. Absolute paths,
`..`, and Windows device paths are invalid.

## SHA-256

The client tries these sources in order:

1. `digest` from the GitHub Release Asset API;
2. `<asset>.sha256`;
3. `SHA256SUMS`;
4. `checksums.txt`.

Installation is allowed without a publisher digest, but the UI must show "Not
verified by publisher." The client still computes a local SHA-256 digest for asset
change detection, safe uninstall, and file ownership records.

## Open-Source Admission

Submissions are made by Pull Request to the Registry repository. CI verifies at
least that:

- the GitHub repository is public;
- the repository contains an SPDX open-source license and a LICENSE file;
- the repository contains source or project files rather than DLLs only;
- a Release tag is parseable and at least one scannable asset exists;
- dependencies exist and contain no cycles;
- installation rules do not escape the allowed directories.

Validation must not build or execute third-party code. Public source does not prove
that a Release binary was built from that source. Projects built by public CI or
carrying an Artifact Attestation may be shown separately as Verified Build.
