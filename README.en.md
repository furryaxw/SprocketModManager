# Sprocket Mod Manager

[中文](README.md) | **English**

A Sprocket mod registry, GitHub Pages catalog, and Windows GUI client.

Only package-level metadata is maintained by hand. Every hour, GitHub Actions reads
each mod repository once and writes normalized versions, tags, and assets into the
Pages `index.json`. The website and default client therefore consume no anonymous
GitHub API quota for the catalog. Binaries still come directly from each mod's own
GitHub Release. The client resolves the cached snapshot, verifies publisher-provided
SHA-256 digests when available, statically inspects DLLs, and transactionally installs
files into `Mods`, `Plugins`, `UserLibs`, or controlled paths under `UserData`.

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

## Run

```powershell
.\.venv\Scripts\python.exe modman.py
```

The GUI is hardware-accelerated by Windows Edge WebView2, while Python continues to
handle the Registry, scanning, dependency resolution, and installation. Individual
installs, batch installs, and update-all share one sequential download queue. Users can
keep browsing and append work while the queue runs; closing waits for the active
installation transaction to finish.

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

- Only HTTPS Registry and GitHub Release download URLs are accepted.
- DLL classification reads PE/.NET metadata only and never uses `Assembly.Load`.
- ZIP archives are limited by entry count, per-file and total extracted size, and
  compression ratio. Absolute paths, `..`, and device paths are rejected.
- Native or unrecognized DLLs require a Registry override that selects a target.
- Conflicting content at the same path, externally modified managed files, and
  manually installed files with a different hash block installation.
- The game directory is never modified while Sprocket is running.
- Installation state is isolated per game directory. Uninstalling never removes
  files that the user changed or that existed before installation.

The client currently checks GitHub Releases and provides an update link; it does
not download or replace itself automatically. Any future automatic updater should
use a fixed-public-key update manifest or verifiable Windows code signing, rather
than trusting an unsigned checksum beside the EXE in the same Release.

## Registry

See [sprocket-mod-spec.en.md](sprocket-mod-spec.en.md) for the metadata specification
and [CONTRIBUTING.en.md](CONTRIBUTING.en.md) for the author submission workflow.
`site/` is a framework-free GitHub Pages site; `.github/workflows/pages.yml`
generates and deploys the Release snapshot after pushes and once per hour.

## License

This project is licensed under the GNU Affero General Public License v3.0
(AGPL-3.0). See [LICENSE](LICENSE).
