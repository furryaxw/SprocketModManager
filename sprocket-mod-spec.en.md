# Sprocket Mod Registry Specification v1

[中文](sprocket-mod-spec.md) | **English**

The Registry is hosted on GitHub Pages and stores only package-level metadata.
Versions, tags, Release assets, and binaries always come from each mod's own GitHub
repository; an entry that declares an external download source carries versions and
assets inside the entry itself.

## Data Flow

```text
Pages index.json
  -> mods/<id>/sprocket-mod.json
  -> GitHub API /repos/<owner>/<repo>/releases (an external source uses the entry's own releases)
  -> select a compatible tag and Release assets
  -> download
  -> modfile: classify through install.files (a DLL that matches no rule is classified from PE metadata), then type -> the directory declared by the package that supplies it
  -> loader kinds: lay install.payload out through its target, subpath, and layout, or install by type through install.files
```

For entries using the default GitHub source, Pages does not store:

- current or historical version numbers;
- tag lists;
- Release download URLs;
- Release asset digests;
- mod binaries.

An external source has no API to query, so it is the entry that supplies this data
instead; see "External Download Sources".

## Base Metadata

```jsonc
{
  "schema_version": 2,
  "id": "furryaxw.sprocket-laser-rangefinder",
  "name": "SprocketLaserRangefinder",
  "authors": ["furryAxw"],
  "repository": "furryaxw/SprocketLaserRangefinder",
  "license": "GPL-3.0-only",
  "kind": "modfile",

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
    "files": [
      {
        "match": "*.dll",
        "type": "melonloader:mod"
      }
    ],
    "scan_dlls": true,
    "exclude": []
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

## Package Kinds

`kind` is a top-level string and defaults to `modfile`:

- `modfile`: an ordinary mod, installed through `install.files` (type-driven).
- `modloader`: a base runtime such as MelonLoader or BepInEx Bleeding Edge.
- `loaderbridge`: a loader that runs another loader's modules on a different base.
- `translateloader`: a translation framework loader such as XUnity AutoTranslator.
- `patch`: a patch applied on top of a loader, such as Sprocket-Mod-Loader.

A `modfile` must use `install.files`. A `modloader`, `loaderbridge`, `translateloader`, or
`patch` may use `install.payload`, and may also use `install.files` when its content
maps onto supply types; a package uses one line or the other, never both, and a `modfile`
never uses `install.payload`. `install.mode` (`standard`, `patch`) works with either
install line. Only a `modloader` may declare an external
`release.source`, and only a `modloader` is listed on the client's loader page and can act
there as a compatibility provider. A `loaderbridge`, `translateloader`, or `patch` is
a directly installable ordinary entry that carries only a kind badge. The top-level
`provides` declares which compatibility capabilities the package supplies; see
"Compatibility".

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
A dependency id names either a registered package or a capability (see
"Compatibility"); Registry CI rejects any other id, and static dependency cycles among
real packages.

Every type statically written into an install rule must have a supplier and is itself an
implicit dependency. A mod declaring `melonloader:mod` needs a package supplying that
type, so resolution puts it into the same install plan, and removal consults the same
dependency graph. A wildcard type resolves through the suppliers in its namespace. A
type a package supplies itself is not one of its dependencies.

## Compatibility

Compatibility is just a package's dependency ranges over capabilities. A capability is
a versioned name written like a package id; one compatibility axis is one capability
id. Capabilities have two sources:

- A **local capability** is supplied by the machine itself, and there is exactly one
  today: the game, `hamish.sprocket`, whose version is the local game version.
- A **provider capability** is declared by a loader package through the top-level
  `provides`. A provider capability may differ from the package's own id: the bridge
  loader `1499501762.bepinex-melonloader-loader` has release version `2.3.9` while the
  `lavagang.melonloader` it supplies is `0.7.3`.

`provides` is an optional top-level object mapping a capability id (written like a
package id) to a version string; the literal `"{version}"` means this release's version.
A loader-kind package with no `provides` supplies its own package id at its release
version. A mod's package id is also something others can depend on as a package, but it is
not a capability axis: mods are linked by package dependencies, and the capability axes
describe only what this machine can run.

The client builds a `{capability id: version}` map for installed packages: the detected
Sprocket version sits under the game capability id, and each installed loader package's
`provides` contributes one entry, with `"{version}"` replaced by the installed release
version.

A release declares the ranges it falls into in its body:

```html
<!-- sp-compat {"hamish.sprocket": [">=0.2.55.5"], "lavagang.melonloader": [">=0.7.0"]} -->
```

Keys are capability ids; values are version-range strings or lists of them. The build
canonicalizes them into `>=a <=b` (multiple clauses joined with `||`) before writing
them into the index, and each becomes a dependency on that capability id. The game
capability axis writes four version segments (`0.2.55.5`, `0.2.55.x`) and provider
capability axes write three (`0.7.0`) for exact values and wildcards, while comparison
bounds may omit trailing segments (`<0.2.54`). A release without a usable declaration
inherits the range of the nearest older release that has one; a malformed declaration
leaves that release undeclared.

The index writes the game capability through a top-level `game`:

```json
{
  "game": { "id": "hamish.sprocket", "name": "Sprocket" }
}
```

`providers.json` in the Registry root records which game range each version range of a
loader-kind package supports, with rows written as the `loader`, `version`, and
`sprocket` ranges, where `loader` is the id of a registered loader-kind package; the
index carries the table under `providers`, with `providers_warnings` alongside:

```json
{
  "schema_version": 2,
  "entries": [
    {
      "loader": "lavagang.melonloader",
      "version": ">=0.7.0 <0.8.0",
      "sprocket": "<0.2.54"
    },
    {
      "loader": "bepinex.bepinex-be",
      "version": ">=6.0.0-be.785",
      "sprocket": ">=0.2.54"
    },
    {
      "loader": "1499501762.bepinex-melonloader-loader",
      "version": ">=2.3.0",
      "sprocket": ">=0.2.54"
    }
  ]
}
```

`0.2.53.x` and older are carried by the official MelonLoader; from `0.2.54` on it is
BepInEx Bleeding Edge plus the BepInEx/MelonLoader bridge: the bridge supplies
`lavagang.melonloader` at `0.7.3`, so a mod that declares a dependency on that
capability stays compatible from `0.2.54` on. A loader-kind package with no row is "this
layer does not know" — it counts as neither compatible nor conflicting. `version` is a
standard SemVer range and may carry a prerelease segment (`6.0.0-be.785`).

The client judges a release as follows: a failing axis means incompatible; no
evaluable axis means unknown; when a loader is installed and the table says that
loader does not support the local game version, or no loader is installed and no row
covers the local game version, the environment contradicts itself, so the verdict is
unknown. Translation packages do not judge their own compatibility declarations, while
the packages they depend on are judged normally.

## Translation Packages

Translation packages are `patch` packages with `"category": "translation"`: they take over
the supply directory of `xunity:translation` through `install.replace`, and their file
rules use that type. The provider of `xunity:translation` is the XUnity framework, so the
type's implicit dependency already puts the framework into the same install plan as the
translation package. Backing up, clearing, and restoring the whole directory are described
under "Patch Packages".

```json
{
  "kind": "patch",
  "category": "translation",
  "install": {
    "mode": "patch",
    "files": [
      {
        "match": "**",
        "type": "xunity:translation",
        "layout": "tree"
      }
    ],
    "replace": ["xunity:translation"],
    "scan_dlls": false,
    "exclude": []
  }
}
```

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

## File Types and Supply

A file type is written `<loader>:<kind>`, for example `melonloader:core`,
`melonloader:mod`, `melonloader:plugin`, `melonloader:userlib`, `bepinex:core`,
`bepinex:plugin`, `bepinex:patchers`, or `xunity:translation`. A type may also be written `<loader>:*` (for example
`melonloader:*`): its concrete type comes from the DLL's PE metadata once the package is
downloaded. The registry build validates that a rule's type has a supplier: a concrete
type needs at least one supply entry, while a wildcard only needs a supplier somewhere in
its namespace. A DLL matching no rule is classified from its PE metadata while
`scan_dlls` is on.

A loader declares through the top-level `supply` which types it provides to other
packages and where each is installed under the game root (`{Sprocket}`):

```json
"supply": {
  "melonloader:core": "{Sprocket}/MelonLoader",
  "melonloader:mod": "{Sprocket}/Mods",
  "melonloader:plugin": "{Sprocket}/Plugins",
  "melonloader:userlib": "{Sprocket}/UserLibs"
}
```

A type may have several suppliers: for example MelonLoader itself, and a BepInEx bridge
loader that re-homes MelonLoader's mod folders under `MLLoader`; both supply the same
`melonloader` types. Every type statically written into an install rule must have at
least one supplier, and the registry build enforces that.

### Several suppliers

When a type has several suppliers, the client uses only the one **already installed in
the target game directory**: its `supply` decides the directory (native MelonLoader
lands in `{Sprocket}/Mods`, the BepInEx bridge in `{Sprocket}/MLLoader/Mods`), and at
most one supplier may be installed per type. With several candidates and none of them
installed, the client **refuses to guess**: it reports the type and the candidate
packages and asks the user to install one of them from the modloader page. A type with a
single supplier is still pulled in automatically as an implicit dependency.

The first matching rule in `install.files` decides a file's type, and the type's
supplier then decides the directory:

```json
"install": {
  "files": [
    {
      "match": "assets/*.bundle",
      "type": "bepinex:plugin",
      "subpath": "Bundles"
    },
    {
      "match": "**",
      "type": "bepinex:core",
      "layout": "tree"
    }
  ],
  "scan_dlls": false,
  "exclude": ["**/*.pdb"]
}
```

- `match` tests both the ZIP entry path and its file name, case-insensitively.
- `subpath` narrows the location below the supplied directory.
- `layout` is `file` (the default, taking only the file name into the type's
  directory) or `tree` (keeping the archive-relative path). Both install lines share
  the same values.
- A file matched by `exclude` is not installed.
- A DLL matching no rule is classified from metadata while `scan_dlls` is on; with
  `scan_dlls` off, a DLL not covered by a rule fails the scan for that package.

Classification reads PE/.NET metadata only. It does not use `Assembly.Load` and does
not execute downloaded content:

1. Inheriting `MelonLoader.MelonMod` -> `melonloader:mod`.
2. Inheriting `MelonLoader.MelonPlugin` -> `melonloader:plugin`.
3. Inheriting `BepInEx.BaseUnityPlugin` or `BepInEx.BasePlugin` -> `bepinex:plugin`.
4. Any other managed assembly follows what it references: `BepInEx*` ->
   `bepinex:plugin` (BepInEx loads from `BepInEx/plugins`), otherwise ->
   `melonloader:userlib`.
5. Native and unparsable DLLs have no automatic classification and require an install
   rule that gives them a type.

The target path is the supplied directory plus `subpath` plus the file name
(`layout: "file"`) or the archive-relative path (`layout: "tree"`). Absolute paths,
`..`, and Windows device paths are invalid.

## Loader Packages

A `modloader`, `loaderbridge`, `translateloader`, or `patch` may install through
`install.payload`, and may also use `install.files` when its content maps onto supply
types; a package uses one line or the other, never both. A `payload` line is written:

```json
"install": {
  "payload": [
    {
      "match": "**",
      "target": "{Sprocket}",
      "layout": "tree"
    }
  ],
  "exclude": []
}
```

`payload[].target` is a location in the game directory, written like a `supply` value
(`{Sprocket}`, `{Sprocket}/BepInEx/core`). The target path is the `target` directory
plus an optional `subpath` plus either the file name (`layout` `file` or absent) or the
archive-relative path (`tree`). With `install.files` the loader installs by type, and the
type's supplier decides the target directory.

A package whose `kind` is `modloader` is a base runtime, such as MelonLoader or
BepInEx Bleeding Edge. It must declare at least one type through `supply`, which names
the types it provides to other packages and where each is installed under `{Sprocket}`;
its own `payload` lands it in the game root, and `provides` declares its compatibility
capabilities:

```json
{
  "kind": "modloader",
  "supply": {
    "melonloader:core": "{Sprocket}/MelonLoader",
    "melonloader:mod": "{Sprocket}/Mods",
    "melonloader:plugin": "{Sprocket}/Plugins",
    "melonloader:userlib": "{Sprocket}/UserLibs"
  },
  "install": {
    "payload": [
      {
        "match": "**",
        "target": "{Sprocket}",
        "layout": "tree"
      }
    ],
    "exclude": []
  }
}
```

A modloader with no `provides` supplies its own package id. A provided capability may
differ from the package's own id: `bepinex.bepinex-be` has its own package version as
its release version, and uses `"provides": {"bepinex.bepinex": "{version}"}` to supply
the `bepinex.bepinex` capability.

A loader has no special path: it goes through the same resolution (including version
ranges), publisher digest verification, ZIP limits, and transactional installation as any
mod. A package whose `kind` is `modloader` keeps no per-file list: its install record holds
the version, the release assets, and the top-level entries it installed into (the
`install.payload` trees and the proxy files in the game root, the latter with their install
digest), and removal hands back the loader's own tree and the proxy DLLs through that list.
It is listed on the modloader page; a `loaderbridge`, `translateloader`, or `patch` is not,
and appears only as an ordinary entry.

Supplying a type does not make a package a modloader: the translation framework also
supplies a type (`xunity:translation`) while being a `translateloader`.

## Patch Packages

A package whose `kind` is `patch` uses `"install.mode": "patch"` and `install.files`, and
declares no `supply`. There are two shapes:

- A **per-file patch** omits `replace`. The first matching rule decides a file's type, the
  type's supplier decides the target directory, and `subpath` narrows the location below
  it.
- A **whole-directory takeover** writes `"replace": ["<type>"]`, and `install.files` must
  use every replaced type. Installation archives the type's whole supply directory (each
  type keeps its five newest archives), clears the directory, and installs this package's
  files into it; uninstallation restores the whole directory from the archive. `replace`
  only appears together with `"install.mode": "patch"`.

Archives live under `<game>/SprocketModManager/backup/replaced/<type with ':' replaced by
'-'>/`, so `xunity:translation` lands in `replaced/xunity-translation/`. Installing a new
takeover package displaces the installed package that previously took over the same type;
it is refused when another installed package still depends on the displaced one.

The per-file example is the Sprocket compatibility pack
`hans21223.sprocket-mod-loader`: it depends on `bepinex.bepinex-be` and lands
`Patch/BepInEx/core/*.dll` from its archive in the supply directory of `bepinex:core`;
`bepinex:core` names `BepInEx/core`, so no `subpath` is needed.

```json
{
  "kind": "patch",
  "install": {
    "mode": "patch",
    "files": [
      {
        "match": "Patch/BepInEx/core/*.dll",
        "type": "bepinex:core"
      }
    ],
    "scan_dlls": false,
    "exclude": []
  }
}
```

For a per-file patch, when the patch and the package it replaces are in the same install
plan, the patch's content wins for a shared path, and the displaced content (the other
package's file from the plan, or the file already on disk) is copied to
`<game>/SprocketModManager/backup/patched/<relative path>`, keeping only the first
original per path. A patch is exempt from the "target already exists with different
content" and "managed file was modified outside the manager" conflict checks; inconsistent
content between non-patch files is still a conflict in the plan itself.

Removing a per-file patch puts the archived original back wherever the archive holds one
and the file on disk still matches what the patch installed (the file keeps its original
owners, or becomes unmanaged again if it had none); files the patch itself added are
deleted with it; a hand-modified file is kept and reported as a warning. Archive entries
are removed once no patch package references them.

## External Download Sources

`release.source` selects where binaries come from, defaulting to the package's own
GitHub Releases:

```json
"release": {
  "source": {
    "type": "external",
    "hosts": ["example.org"]
  }
}
```

An external source has no API to query. Only a modloader may declare one; a mod always
takes its binaries from its own GitHub Releases. Such an entry must also carry its own
top-level `releases` array whose `download_url` values sit on one of the listed hosts.
Those hosts are a download allowlist: the client downloads assets only from them, and
publisher digests are still verified as described under SHA-256. When a tool or an
upstream document fixes one exact build, such an entry can pin that build's name, URL,
and digest directly.

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
- the repository contains a LICENSE/COPYING file, and the license is the SPDX identifier
  the entry declares;
- the repository contains source or project files rather than DLLs only;
- a Release tag is parseable and at least one scannable asset exists;
- every dependency id is a registered package or a capability, and real packages contain no cycles;
- every statically declared install type has a supplier, and supplied directories and
  `subpath` values stay inside the game directory.

Validation must not build or execute third-party code. Public source does not prove
that a Release binary was built from that source. Projects built by public CI or
carrying an Artifact Attestation may be shown separately as Verified Build.
