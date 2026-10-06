# SprocketModManager

[中文](README.zh.md) | **English**

A Sprocket mod registry, GitHub Pages catalog, and a Windows/Linux GUI client.

Only package-level metadata is maintained by hand. Every hour, GitHub Actions reads
each mod repository once and writes normalized versions, tags, and assets into the
Pages `data/packages.json`. The website and default client therefore consume no anonymous
GitHub API quota for the catalog. Binaries still come directly from each mod's own
GitHub Release. The client resolves the cached snapshot, verifies publisher-provided
SHA-256 digests when available, derives each file's type from the install rules and PE
metadata, and transactionally installs files into the directory declared by the loader
that supplies that type (`{Sprocket}/Mods`, `{Sprocket}/BepInEx/plugins`, and so on).

## Community

Release announcements go to the `#mod-releases` channel of the official Sprocket
Discord server:

- Server invite: https://discord.com/invite/baFH43keyR
- Release channel: https://discord.com/channels/788349365466038283/1531947654957891614

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

Loaders are ordinary registry entries: `mods/lavagang/melonloader.json` and
`mods/bepinex/bepinex-be.json` have `kind` `modloader`, declare through `supply` which types
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
files. The About page can open this directory. The sidebar's Upload logs action lists the sources
that can be uploaded: the manager log is always available, and a runtime log detected in the game
directory (`MelonLoader\Latest.log`, `BepInEx\LogOutput.log`) is listed once the file exists. The
chosen source is uploaded and returns a public link that can be copied.

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
be updated and removed normally; all other DLLs are marked "Local only" on the Installed
page, showing only their file name and path with no update or removal action.

Use a local Registry with the CLI:

```powershell
.\.venv\Scripts\python.exe modman.py --index-dir site packages
.\.venv\Scripts\python.exe modman.py --index-dir site plan furryaxw.sprocket-laser-rangefinder --scan
.\.venv\Scripts\python.exe modman.py --index-dir site --game-path G:\Sprocket install furryaxw.sprocket-laser-rangefinder
```

Global CLI options must appear before the subcommand. The default remote Registry
is `https://sprocketmods.furryaxw.top/data`, holding `packages.json`, `environment.json`, and
`diagnosis.json`.

## Running on Linux

The Linux client runs from a checkout or from the single-file build `build_linux.sh` produces; both
keep their data in `~/.sprocket-mod-manager`. `requirements.txt` adds PyQt6 and Qt WebEngine on
Linux, and pywebview runs the client in Qt WebEngine instead of WebView2:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python modman.py
```

Qt also needs the X11 runtime libraries a desktop distribution ships (`libxcb-cursor0`,
`libxkbcommon-x11-0` and friends on Debian/Ubuntu); a bare window manager or a container may need
them installed.

Qt WebEngine runs with hardware acceleration. A start that never gets the window up (some Mesa
drivers crash its GPU path) is recorded in `~/.sprocket-mod-manager`, and later starts use software
rendering; `--disable-gpu` forces software rendering for one run, `--enable-gpu` tries hardware
acceleration again, and `QTWEBENGINE_CHROMIUM_FLAGS` still overrides the Chromium flags. Logs and
configuration live in `~/.sprocket-mod-manager`. `--debug` records `DEBUG` and opens the Qt remote
debugging port, but not the DevTools window, which would block startup.

The game path is detected from Steam libraries under `~/.local/share/Steam`, `~/.steam/steam`, and
the Flatpak Steam directory. Sprocket runs through Proton, which uses Wine's built-in proxy DLLs
and ignores the loader's copy in the game directory, so add the override for the installed loader
to Sprocket's Steam launch options:

```text
WINEDLLOVERRIDES="winhttp=n,b" %command%     # BepInEx
WINEDLLOVERRIDES="version=n,b" %command%     # MelonLoader
```

If no loader log (`BepInEx/LogOutput.log`, `MelonLoader/Latest.log`) appears after launching the
game, the override is missing.

The client reads the game directory's process out of `/proc`, so a running Sprocket blocks an
install the same way it does on Windows. Opening a folder uses `xdg-open`, and revealing a file uses
the desktop's `org.freedesktop.FileManager1` service, falling back to the containing directory.
Credentials (the GitHub login and private-server sessions) are files under
`~/.sprocket-mod-manager/credentials`, created `0600`; Windows encrypts the same files with the
account's DPAPI key. Release assets are per platform, so the update check looks for
`SprocketModManager-linux-x64` here.

`build_linux.sh` produces that single-file build; it replaces itself in place like the Windows one:

```sh
sh build_linux.sh
./dist/SprocketModManager-linux-x64
```

Keep it in a directory the user can write (`~/.local/bin`, a `~/Applications` folder): replacing a
running build is exactly what self-update does, and a root-owned directory refuses it. Only a
single-file build can replace itself — a source run reports updates and opens the release page.

Test with the Linux interpreter; the UI render harness needs `node` on `PATH`:

```sh
.venv/bin/python -m unittest discover -s tests
```

## Uninstalling

Mods, loaders, and patch packages are removed from the client's Installed page; removal hands back
the files listed in the install record, and files that are protected or changed by the user stay.

The client itself writes no registry keys and creates no shortcuts, so deleting the binary
(`SprocketModManager.exe`, `SprocketModManager-linux-x64`) uninstalls it. It leaves two state
directories that can be deleted separately: the manager directory
(`%LOCALAPPDATA%\SprocketModManager`, `~/.sprocket-mod-manager`; configuration, logs, and WebView
storage) and `<game>/SprocketModManager` (install records, the DLL metadata cache, and backups of
replaced files).

## Validate

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods --offline
.\.venv\Scripts\python.exe validate_registry.py --mods-dir mods
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output-dir site
.\.venv\Scripts\python.exe gen-index.py --mods-dir mods --output-dir site --fetch-releases
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

Manager self-updates: on startup the client checks the GitHub Release tagged `v<version>` for the
asset of the running platform (`SprocketModManager.exe`, `SprocketModManager-linux-x64`). When a
newer release exists it offers two paths — **Update now** downloads the new build next to the
running one, verifies the asset SHA-256 GitHub reports, and hands over to a swap child process that
replaces it and starts the new build; **Later** keeps this session running and asks again on the
next start. That child waits for the old process to release the locked EXE on Windows only: on Linux
the running file is replaced directly, and the old process keeps its own inode. A source run, or a
build that is not a single file, cannot replace itself and is sent to the release page instead.

That chain trusts GitHub's HTTPS plus the asset digest GitHub computes. Guarding against a stolen
release account needs a fixed-public-key update manifest or verifiable Windows code signing.

## Code signing policy

Windows release artifacts use free open-source code signing provided by SignPath
Foundation (application in progress): builds are signed through SignPath.io and the
certificate is held by SignPath Foundation.

Free code signing provided by SignPath.io, certificate by SignPath Foundation

- Committers and reviewers: [@furryaxw](https://github.com/furryaxw)
- Approvers: [@furryaxw](https://github.com/furryaxw)
- Privacy policy: see [Privacy policy](#privacy-policy).

## Privacy policy

The program contains no telemetry or usage analytics and collects no user data. Two
situations send data: after the user picks a log file in the sidebar's log upload menu, the
text of that file goes to `https://paste.furryaxw.top/api/q/` with no background upload (see
[log upload](docs/log-upload-design.en.md)); and a user-configured private server receives data
under the user's actions.

Every other network request only reads data — its own Release and updates, the Registry
index and mod Release metadata, mod READMEs — and goes to GitHub or this project's Pages
site, subject to the
[GitHub Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-privacy-statement).
The GUI is rendered by Microsoft Edge WebView2, a component that may itself reach the
network under the
[Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement).

## Registry

See [sprocket-mod-spec.en.md](https://github.com/furryaxw/SprocketModManager/blob/registry/sprocket-mod-spec.en.md) for the metadata specification
and [CONTRIBUTING.en.md](https://github.com/furryaxw/SprocketModManager/blob/registry/CONTRIBUTING.en.md) for the author submission workflow.
`site/` is a framework-free GitHub Pages site; `.github/workflows/pages.yml`
generates and deploys the Release snapshot after pushes and once per hour.

## License

This project is licensed under the GNU Affero General Public License v3.0
(AGPL-3.0). See [LICENSE](LICENSE).
