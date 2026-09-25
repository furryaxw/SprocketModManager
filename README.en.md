# Sprocket Mod Manager

[中文](README.md) | **English**

A Sprocket mod registry, GitHub Pages catalog, and Windows GUI client.

Only package-level metadata is maintained by hand. Every hour, GitHub Actions reads
each mod repository once and writes normalized versions, tags, and assets into the
Pages `index.json`. The website and default client therefore consume no anonymous
GitHub API quota for the catalog. Binaries still come directly from each mod's own
GitHub Release. The client resolves the cached snapshot, verifies publisher-provided
SHA-256 digests when available, derives each file's type from the install rules and PE
metadata, and transactionally installs files into the directory declared by the loader
that supplies that type (`{Sprocket}/Mods`, `{Sprocket}/BepInEx/plugins`, and so on).

## Current Vertical Slice

```text
furryaxw.sprocket-laser-rangefinder
  -> furryaxw.sprocket-depth
  -> GitHub Releases
  -> SprocketDepth.dll              -> UserLibs/
  -> SprocketLaserRangefinder.dll   -> Mods/
```

This scenario has been exercised against two real Releases, including download,
remote digest verification, DLL classification, isolated-directory installation,
state tracking, removal of the requested package, and orphan dependency cleanup.

## Loader management

Loaders are ordinary registry entries: `mods/lavagang.melonloader/` and
`mods/bepinex.bepinex-be/` have `kind` `modloader`, declare through `supply` which types
they provide to other packages and where, and declare their compatibility capabilities
through `provides`. The modloader page lists every `modloader` package with whether it is
installed, its installed version, the newest installable version, its compatibility
verdict for the current environment, and the types and directories it supplies. Install,
update, and removal all go through the ordinary install pipeline (resolve -> prepare ->
apply), sharing the mods' download-host restrictions, publisher SHA-256 verification,
ZIP limits, and transactional installation; a loader's own payload lands in the game root
through `install.payload`, or installs by type through `install.files` when its content
maps onto supply types. A base runtime keeps no per-file list: its install record holds
the version, the release assets, and the top-level entries it installed into (its own
directories plus the proxy files in the game root), and removal hands back the whole tree
and the proxy DLLs through that list.

Whichever type a mod's install rules declare, the solver puts the loader that supplies it
into the same install plan, so installing a MelonLoader mod installs MelonLoader in the
same transaction. The capability version a loader supplies is one of the compatibility
axes.

## Run

```powershell
.\.venv\Scripts\python.exe modman.py
```

Diagnostic mode can be persisted on Settings or forced for one launch with `--debug`; the two
values are combined with OR. It records `DEBUG` messages and enables WebView2 debugging. A normal launch records
`INFO` and higher levels:

```powershell
.\.venv\Scripts\python.exe modman.py --debug
.\SprocketModManager.exe --debug
```

Manager logs are stored at `%LOCALAPPDATA%\SprocketModManager\Latest.log`. Each launch clears
`Latest.log`, archives the previous session with a timestamp, and retains the five newest history
files. The About page can open this directory or upload the current manager log. The log upload
button on Settings continues to upload `MelonLoader\Latest.log` from the game directory.

The GUI is hardware-accelerated by Windows Edge WebView2, while Python continues to
handle the Registry, scanning, dependency resolution, and installation. Individual
installs and batch installs share one sequential download queue. Users can
keep browsing and append work while the queue runs; closing waits for the active
installation transaction to finish. Catalog rows show each mod's summary; the detail
header groups its name, ID, version, and authors, while the body reads the registered
repository's default README, uses GitHub's renderer, and sanitizes the result locally.
The install confirmation lists Registry-declared recommendations as unchecked options;
only recommendations explicitly selected by the user are added to the queue. Packages
marked as recommended for new installs show a star and stay pinned above regular results
only while the current runtime's mod directories (plain `Mods` when no bridge loader is
installed) contain no DLL. Once any mod exists, the catalog returns to its normal
sort. This marker never opens a prompt, selects, or installs a package.

When the catalog loads or the Installed page refreshes, the client scans unmanaged DLLs
under the active runtime identifiers' directories. It adopts a package only when the file
name, static install target, and GitHub Release SHA-256 all match uniquely. Unknown,
locally modified, digest-less, or ambiguous files remain unmanaged. Adopted packages can
be updated and removed normally; all other DLLs appear as read-only "Unrecognized" rows on
the Installed page, showing only their file name and path with no update or removal
action.

Use a local Registry with the CLI:

```powershell
.\.venv\Scripts\python.exe modman.py --index-file index.json packages
.\.venv\Scripts\python.exe modman.py --index-file index.json plan furryaxw.sprocket-laser-rangefinder --scan
.\.venv\Scripts\python.exe modman.py --index-file index.json --game-path G:\Sprocket install furryaxw.sprocket-laser-rangefinder
```

Global CLI options must appear before the subcommand. The default remote Registry
is `https://sprocketmods.furryaxw.top/index.json`.

## Validate

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods --offline
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output index.json
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output index.json --fetch-releases
```

Online validation calls only the GitHub API. It does not clone, build, or execute
third-party mod code.
With `GITHUB_TOKEN`, `--fetch-releases` produces the same embedded Release snapshot
used by Pages.

## Build the EXE

```powershell
.\build_exe.ps1
```

The output is written to `dist\SprocketModManager.exe`. The build script uses the
project's `.venv` and installs missing packaging dependencies from
`requirements.txt`. The GUI requires Windows 10/11 and the Edge WebView2 Runtime,
which is normally preinstalled with supported Windows versions and current Microsoft
Edge installations.

## Security Boundaries

- Only HTTPS Registry and Release download URLs are accepted: GitHub-sourced assets
  must sit under the mod's own repository releases, and external-source assets must sit
  on the hosts the entry allows. Only a modloader may declare an external source.
- File types and DLL classification read PE/.NET metadata only and never use
  `Assembly.Load`.
- ZIP archives are limited by entry count, per-file and total extracted size, and
  compression ratio. Absolute paths, `..`, and device paths are rejected.
- Files can only land in a directory declared by some loader's supply table, or in a
  loader's own `install.payload` `target`: a type may have several suppliers and the
  installed one decides, and a `subpath` cannot escape the game directory.
- The Translations category is a `patch` package: it takes over the supply directory of
  `xunity:translation`, archiving the whole directory (five newest kept) and clearing it
  before installing, and restoring the whole directory on removal.
- Native or unclassifiable DLLs require an install rule that names their type.
- Conflicting content at the same path, externally modified managed files, and
  manually installed files with a different hash block installation; patch mode
  overwrites by its replacement semantics instead and archives the replaced original
  under `SprocketModManager/backup/patched`.
- The game directory is never modified while Sprocket is running.
- Loaders and mods share one install pipeline: the same download-host restrictions,
  publisher digest verification, ZIP limits, transactional installation, and rollback
  on failure.
- READMEs are fetched only from the mod's registered GitHub repository. Scripts, forms,
  embedded content, unsafe URLs, and non-GitHub image sources are removed before display.
- Installation state is isolated per game directory. Uninstalling never removes files
  modified by the user. Ordinary preexisting files remain protected; files adopted by
  an exact Release hash become managed and may be deleted only while unchanged.
- The local developer server supports authorized private ZIP/DLL downloads, archive/file
  SHA-256 verification, and transactional installation. The client exchanges a server
  session token through GitHub Device Flow. Private manifests use Ed25519 detached
  canonical-JSON signatures, with the server public-key fingerprint confirmed on first
  use. The server remains a protocol proof of concept, not a production public backend.
  See [the private server signature protocol](docs/private-server-signatures.md) for
  client signatures, trust negotiation, key status, and rotation rules.

Manager self-updates: on startup the client checks the GitHub Release tagged `v<version>` with the
`SprocketModManager.exe` asset. When a newer release exists it offers two paths — **Update now**
downloads the new EXE next to the running one, verifies the asset SHA-256 GitHub reports, and hands
over to a swap child process that waits for the old process to exit and replaces it (Windows locks a
running EXE against overwriting itself); **Later** keeps this session running and asks again on the
next start. A source run, or a build that is not a single file, cannot replace itself and is sent to
the release page instead.

That chain trusts GitHub's HTTPS plus the asset digest GitHub computes. Guarding against a stolen
release account needs a fixed-public-key update manifest or verifiable Windows code signing.

## Registry

See [sprocket-mod-spec.en.md](sprocket-mod-spec.en.md) for the metadata specification
and [CONTRIBUTING.en.md](CONTRIBUTING.en.md) for the author submission workflow.
`site/` is a framework-free GitHub Pages site; `.github/workflows/pages.yml`
generates and deploys the Release snapshot after pushes and once per hour.

## License

This project is licensed under the GNU Affero General Public License v3.0
(AGPL-3.0). See [LICENSE](LICENSE).
