# Submit a Mod

[中文](CONTRIBUTING.md) | **English**

The Registry accepts only public, auditable, open-source Sprocket mods. An entry is a
hand-written metadata file added to this repository through a Pull Request; you do not
need to modify an existing Release or copy binaries into this repository.

## The Entry File

An entry lives at `mods/<package-id>/sprocket-mod.json`, and the directory name must
match the entry's `id` exactly. `$schema` at the top of the file points back at the
repository's schema, written as `../../schemas/sprocket-mod.schema.json` from
`mods/<package-id>/`.

`id` uses lowercase letters and digits separated by `.` or `-`, with at least two
segments; `name` is the assembly name.

A minimal entry you can use as it stands (with `kind` absent, meaning the default
`modfile`):

```json
{
  "$schema": "../../schemas/sprocket-mod.schema.json",
  "schema_version": 2,
  "id": "example.sprocket-mod",
  "name": "ExampleSprocketMod",
  "authors": ["ExampleAuthor"],
  "repository": "ExampleAuthor/ExampleSprocketMod",
  "license": "MIT",
  "display_name": {
    "en": "Example Sprocket Mod",
    "zh": "示例 Sprocket 模组"
  },
  "description": {
    "en": "An example Sprocket mod."
  },
  "release": {
    "include_prerelease": false,
    "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
    "assets": {
      "include": ["*.dll", "*.zip"],
      "exclude": ["*debug*", "*symbols*", "*source*"]
    }
  },
  "dependencies": [],
  "recommendations": [],
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
  "tags": ["example"]
}
```

A DLL's concrete kind can also be left to its own metadata after download, by writing
the type as `<loader>:*`:

```json
{
  "$schema": "../../schemas/sprocket-mod.schema.json",
  "schema_version": 2,
  "id": "example.sprocket-metadata-mod",
  "name": "ExampleSprocketMetadataMod",
  "authors": ["ExampleAuthor"],
  "repository": "ExampleAuthor/ExampleSprocketMetadataMod",
  "license": "MIT",
  "display_name": {
    "en": "Example Sprocket Metadata Mod"
  },
  "release": {
    "include_prerelease": false,
    "version_pattern": "^v?([0-9]+\\.[0-9]+\\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$",
    "assets": {
      "include": ["*.dll"],
      "exclude": []
    }
  },
  "dependencies": [],
  "install": {
    "files": [
      {
        "match": "*.dll",
        "type": "melonloader:*"
      }
    ],
    "scan_dlls": true,
    "exclude": []
  },
  "category": "utility",
  "tags": ["example"]
}
```

## Required and Optional Fields

Required: `schema_version` (currently `2`), `id`, `name`, `authors`, `repository`,
`license`, `display_name`, `release`, `dependencies`, `install`, `category`, and `tags`.
Every other field is optional.

- `authors`: a non-empty, duplicate-free list of strings.
- `repository`: `owner/repo`, and the repository must be public.
- `license`: an SPDX identifier such as `MIT` or `GPL-3.0-only`.
- `display_name`: at least one language. The whole `description` field is optional; when
  present it needs at least one non-empty translation, and its languages do not have to
  match those of `display_name`. Language keys use open-ended tags such as `en`,
  `zh-Hans`, `pt-BR`, or `x-example`.
- `release`: `include_prerelease` decides whether prereleases are accepted; the first
  capture group of `version_pattern` must be SemVer; `assets.include` needs at least one
  pattern and `assets.exclude` may be empty. Only patterns belong here — an entry never
  carries a version number or a download URL (`version`, `latest_version`,
  `download_url`, and `tag` are all rejected).
- `dependencies`: an array whose items are exactly `{id, version, when}`; `version` is
  the range the dependency must satisfy, `when` is the range of this package's own
  version, and `*` means "any". Exact versions, comparison operators, `^`, and `~` are
  supported.
- `recommendations`: a duplicate-free list of registered package ids that cannot name
  the current package; they take no part in dependency resolution and are never installed
  automatically — the client lists them unchecked in the install confirmation.
- `featured`: a boolean, `false` when omitted; when `true` the Registry site shows a star.
- `category`: `gameplay`, `utility`, `library`, `visual`, `audio`, `translation`, or `other`.
- `tags`: lowercase letters, digits, and hyphens, without duplicates.

## Install Rule Types

Each `install.files` line is `{match, type}` with optional `subpath` and `layout`; using
this install line also requires `scan_dlls` and `exclude`.

`type` is a **file type** written `<loader>:<kind>`, for example `melonloader:mod`,
`melonloader:plugin`, `melonloader:userlib`, `bepinex:plugin`, `bepinex:core`, or
`xunity:translation`. It may also be written `<loader>:*` (for example `melonloader:*`):
the concrete type comes from the DLL's PE metadata once the package is downloaded.

Classification reads PE/.NET metadata only. It does not use `Assembly.Load` and does not
execute downloaded content: inheriting `MelonLoader.MelonMod` gives `melonloader:mod`,
`MelonLoader.MelonPlugin` gives `melonloader:plugin`, `BepInEx.BaseUnityPlugin` or
`BepInEx.BasePlugin` gives `bepinex:plugin`, any other managed assembly that references
`BepInEx*` gives `bepinex:plugin` and otherwise `melonloader:userlib`; native and
unparsable DLLs need a rule that gives them a type.

The install directory comes from the **supply table of the loader that provides that
type**: a loader declares through the top-level `supply` which types it provides and
where each is installed under `{Sprocket}`. A type may have several suppliers — native
MelonLoader provides `melonloader:mod` at `{Sprocket}/Mods` while the BepInEx bridge
provides it at `{Sprocket}/MLLoader/Mods` — and the client uses only the one **already
installed in the target game directory**; with several candidates and none installed it
reports an error and asks the user to install a loader first. So a rule only needs the
right type, not a directory.

`match` tests both the ZIP entry path and its file name, case-insensitively, and the
first matching rule decides the type. `subpath` narrows the location below the supplied
directory. `layout` is `file` (the default, taking only the file name) or `tree` (keeping
the archive-relative path). A file matched by `exclude` is not installed. While
`scan_dlls` is on, a DLL matching no rule is classified from metadata; with it off, a DLL
not covered by a rule fails the scan for that package.

## Package Kinds, Loaders, and Install Lines

`kind` may be omitted and defaults to `modfile`:

- `modfile`: an ordinary mod, using `install.files` only.
- `modloader`: a base runtime such as MelonLoader or BepInEx Bleeding Edge.
- `loaderbridge`: a loader that runs another loader's modules on a different base.
- `translateloader`: a translation framework loader such as XUnity AutoTranslator.
- `patch`: a patch applied on top of a loader, such as Sprocket-Mod-Loader.

The four kinds other than `modfile` may use `install.payload`, and may also use
`install.files` when their content maps onto supply types; a package uses one install
line or the other. An `install.payload` line is `{match, target}` with optional `subpath`
and `layout`, and also requires `exclude`; `target` is a location in the game directory,
written like a `supply` value (`{Sprocket}`, `{Sprocket}/BepInEx/core`).

A loader declares through `supply` which types it provides to other packages and where
each is installed under `{Sprocket}`; a `kind` of `modloader` must declare at least one
type. `provides` is an optional capability table — capability id to version string —
where the literal `"{version}"` means this release's own version. A loader-kind package
with no `provides` supplies its own package id, and a capability id may differ from the
package id: `bepinex.bepinex-be` supplies the `bepinex.bepinex` capability with
`"provides": {"bepinex.bepinex": "{version}"}`.

Only a package whose `kind` is `modloader` may declare an external `release.source`: a
mod takes its binaries from its own GitHub Releases alone, and the version, tag, and
publisher digest all come from that repository. An external source has no API to query,
so such an entry must carry its own top-level `releases` array, whose `download_url`
values must sit on one of the hosts listed in `release.source.hosts`.

## Dependencies and Capabilities

A dependency id names either a registered package or a **capability** (see the next
section). Registry CI rejects any other id, and static dependency cycles among real
packages. A type statically written into an install rule is itself an implicit
dependency: the type needs a supplier, and resolution puts that loader into the same
install plan.

## The Compatibility Declaration

A release declares the version ranges it falls into with a comment block in its body:

```html
<!-- sp-compat {"hamish.sprocket": ">=0.2.55.5"} -->
```

- Keys are capability ids. The game capability is `hamish.sprocket`; the rest are
  declared by loader-kind packages through `provides`, such as MelonLoader's
  `lavagang.melonloader` or the `bepinex.bepinex` supplied by `bepinex.bepinex-be`. An
  unknown capability in the block is ignored with a warning.
- Values are version ranges, either one string or a list of strings; each list item is
  one range, and the build merges them and canonicalizes them into `>=a <=b` (multiple
  clauses joined with `||`).
- The game capability axis writes four segments: exact values and wildcards in full
  (`0.2.55.5`, `0.2.55.x`), while comparison bounds may omit trailing segments
  (`<0.2.54`). A loader-provided capability axis writes three (`0.7.0`, `0.7.x`).
- Omitting the block means "no declaration": the release inherits the range of the
  nearest older release that has a usable declaration. A malformed declaration leaves
  that release undeclared and does not affect other releases.

The game axis alone:

```html
<!-- sp-compat {"hamish.sprocket": ">=0.2.55.5"} -->
```

The game axis plus a loader axis:

```html
<!-- sp-compat {"hamish.sprocket": [">=0.2.55.5"], "lavagang.melonloader": [">=0.7.0"]} -->
```

A list of ranges:

```html
<!-- sp-compat {"hamish.sprocket": [">=0.2.50 <0.2.54", ">=0.2.55.5"], "lavagang.melonloader": ">=0.7.0"} -->
```

`0.2.53.x` and older are carried by the official MelonLoader; from `0.2.54` on it is
BepInEx Bleeding Edge plus the BepInEx/MelonLoader bridge. The bridge supplies
`lavagang.melonloader` at `0.7.3`, so a mod running under the bridge only needs to
declare `lavagang.melonloader` — the BepInEx axis does not have to be written.

## Opening a Pull Request

1. Create a branch and add the file at `mods/<package-id>/sprocket-mod.json`.
2. Open a Pull Request.
3. Wait for Registry CI and maintainer review.

CI verifies that:

- the metadata matches `schemas/sprocket-mod.schema.json`, the directory name matches
  `id`, and it contains no version number or download URL;
- the GitHub repository is public and not archived;
- the repository contains `LICENSE`/`COPYING` and actual source files; the license is the
  SPDX identifier the entry declares, and a file GitHub cannot classify still passes;
- at least one non-draft Release has a tag that can be parsed as SemVer;
- at least one Release asset matches the metadata include/exclude rules;
- every dependency id is a registered package or a capability, and real packages contain
  no dependency cycles;
- every recommendation is registered;
- every concrete install type named by a rule has at least one supplying loader (a
  wildcard only needs a supplier somewhere in its namespace), and the `supply` of the
  loader actually installed decides where the file goes;
- every row of `providers.json` is usable.

Validation never executes code from a submitted repository. Public source alone does not
prove that a Release binary was built from that source. Reproducible builds and GitHub
Artifact Attestations are treated as separate trust indicators.
