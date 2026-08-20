# Sprocket Mod Manager Architecture

`sprocket_mod_manager` is organized by responsibility. Its root contains only
`__init__.py`; implementation modules belong to one of five explicit packages.

```text
sprocket_mod_manager/
|-- domain/          # Business data and rules
|-- application/     # Use cases and workflow coordination
|-- infrastructure/  # Filesystem, network, persistence, external services
|-- presentation/    # Desktop adapters and frontend resources
`-- utilities/       # Dependency-free reusable helpers
```

## `domain/`

- `models.py`: package, release, resolution, and prepared-plan value objects
- `semver.py`: version parsing, ordering, and range matching
- `registry.py`: parsed registry data and package identifier lookup
- `errors.py`: shared application error taxonomy

Domain modules must not import application, infrastructure, or presentation.

## `application/`

- `service.py`: public use-case facade for resolve, install, remove, and adopt
- `solver.py`: dependency resolution
- `preparer.py`: download verification and install-plan preparation
- `adoption.py`: adoption of existing game files
- `catalog.py`: concurrent catalog release loading
- `install_queue.py`: queued installation state and worker serialization
- `private_install.py`: private-package preparation workflow

Application modules coordinate domain rules and infrastructure adapters. They
must not import presentation.

## `infrastructure/`

- `config.py`, `defaults.py`: configuration and default locations
- `state.py`, `credential_store.py`, `profiles.py`: persisted local state
- `github.py`, `http_client.py`, `registry_source.py`: public remote transports
- `private_servers/`: private catalog models, cache, GitHub sync, and server client
- `release_checksums.py`, `scanner.py`, `installer.py`: package inspection and filesystem
- `file_transaction.py`, `xunity_backup.py`: rollback and translation backups
- `melonloader.py`, `log_upload.py`: external integrations
- `app_logging.py`, `desktop.py`: manager diagnostics and Windows shell integration

Infrastructure implements I/O and may use domain types. It must not import
presentation.

## `utilities/`

- `checksums.py`: streaming file hashes and common checksum text parsing
- `archive_safety.py`: shared limits for untrusted ZIP extraction
- `dependencies.py`: release-conditional dependency selection
- `urls.py`: loopback detection and proxy URL normalization
- `package_paths.py`: safe package-relative and install-target paths
- `processes.py`: shared process-state detection
- `ui_values.py`: reusable UI value normalization

Utilities may depend only on the Python standard library and domain errors.
Product-specific Release metadata and remote checksum lookup remain in
`infrastructure/release_checksums.py`.

## `presentation/`

- `web_gui.py`: JavaScript API facade
- `webview_app.py`: WebView2 window and desktop lifecycle
- `api_support.py`: API serialization and packaged resource lookup
- `controllers/`: feature-specific WebView API implementations
- `client_ui/`: HTML, CSS, JavaScript, and packaged image resources

`modman.py` is the executable entry point. Package-level public domain symbols
remain exported by `sprocket_mod_manager.__init__`.
