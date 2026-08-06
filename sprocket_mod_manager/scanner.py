from __future__ import annotations

import fnmatch
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import dnfile

from .errors import ScanError
from .hashing import sha256_file
from .models import PreparedFile, RegistryPackage


STANDARD_ROOTS = {"Mods", "Plugins", "UserLibs", "UserData"}
ALLOWED_ROOTS = STANDARD_ROOTS | {"AutoTranslator"}
XUNITY_TRANSLATION_MODE = "xunity-translation"
MAX_ARCHIVE_FILES = 4096
MAX_ARCHIVE_FILE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
IGNORED_SUFFIXES = {
    ".pdb",
    ".xml",
    ".md",
    ".txt",
    ".cs",
    ".csproj",
    ".sln",
}


def validate_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or ":" in value:
        raise ScanError(f"unsafe package path: {value!r}")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ScanError(f"unsafe package path: {value!r}")
    return path


def validate_target(value: str) -> PurePosixPath:
    path = validate_relative_path(value)
    if path.parts[0] not in ALLOWED_ROOTS:
        raise ScanError(f"target root is not allowed: {value!r}")
    return path


def _matches(pattern: str, path: str) -> bool:
    folded = path.casefold()
    return fnmatch.fnmatchcase(folded, pattern.casefold()) or fnmatch.fnmatchcase(
        PurePosixPath(path).name.casefold(), pattern.casefold()
    )


def _override_target(package: RegistryPackage, source_name: str) -> PurePosixPath | None:
    for override in package.install.get("overrides", ()):
        if _matches(str(override.get("match", "")), source_name):
            directory = validate_target(str(override.get("target", "")))
            if directory.parts[0] not in STANDARD_ROOTS:
                raise ScanError(f"override target root is not allowed: {directory.as_posix()!r}")
            return directory / PurePosixPath(source_name).name
    return None


def _is_excluded(package: RegistryPackage, source_name: str) -> bool:
    return any(_matches(str(pattern), source_name) for pattern in package.install.get("exclude", ()))


def _declared_target(source_name: str) -> PurePosixPath | None:
    path = validate_relative_path(source_name)
    parts = path.parts
    if parts[0] in STANDARD_ROOTS:
        return path
    if len(parts) > 1 and parts[1] in STANDARD_ROOTS:
        return PurePosixPath(*parts[1:])
    return None


def classify_managed_dll(path: Path) -> str:
    try:
        pe = dnfile.dnPE(str(path))
    except Exception as exc:
        raise ScanError(f"cannot parse PE metadata for {path.name}: {exc}") from exc
    try:
        if not pe.net or not pe.net.mdtables.TypeDef:
            raise ScanError(f"native or unsupported DLL requires an install override: {path.name}")
        rows = tuple(pe.net.mdtables.TypeDef.rows)

        def base_kind(row: object, seen: set[int]) -> str | None:
            identity = id(row)
            if identity in seen:
                return None
            seen.add(identity)
            extends = getattr(row, "Extends", None)
            base = getattr(extends, "row", None)
            if base is None:
                return None
            namespace = str(
                getattr(base, "TypeNamespace", None) or getattr(base, "Namespace", None) or ""
            )
            name = str(getattr(base, "TypeName", None) or getattr(base, "Name", None) or "")
            if namespace == "MelonLoader" and name == "MelonMod":
                return "Mods"
            if namespace == "MelonLoader" and name == "MelonPlugin":
                return "Plugins"
            if base in rows:
                return base_kind(base, seen)
            return None

        kinds = {kind for row in rows if (kind := base_kind(row, set()))}
        if len(kinds) > 1:
            raise ScanError(f"assembly contains both MelonMod and MelonPlugin entry types: {path.name}")
        return next(iter(kinds), "UserLibs")
    finally:
        close = getattr(pe, "close", None)
        if close:
            close()


class PackageScanner:
    def scan(
        self,
        package: RegistryPackage,
        asset_path: Path,
        output_dir: Path,
    ) -> tuple[list[PreparedFile], list[str]]:
        suffix = asset_path.suffix.casefold()
        if package.install.get("mode") == XUNITY_TRANSLATION_MODE and suffix != ".zip":
            raise ScanError("XUnity translation packages must use a ZIP Release asset")
        if suffix == ".dll":
            return self._scan_file(package, asset_path.name, asset_path, output_dir)
        if suffix not in {".zip", ".smod"}:
            raise ScanError(f"unsupported Release asset type: {asset_path.name}")
        return self._scan_archive(package, asset_path, output_dir)

    def _scan_archive(
        self,
        package: RegistryPackage,
        archive_path: Path,
        output_dir: Path,
    ) -> tuple[list[PreparedFile], list[str]]:
        prepared: list[PreparedFile] = []
        ignored: list[str] = []
        total_size = 0
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise ScanError(f"invalid ZIP asset {archive_path.name}: {exc}") from exc
        with archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_FILES:
                raise ScanError(f"archive contains too many entries: {archive_path.name}")
            for member in members:
                if member.is_dir():
                    continue
                source_path = validate_relative_path(member.filename)
                total_size += member.file_size
                if member.file_size > MAX_ARCHIVE_FILE_BYTES or total_size > MAX_ARCHIVE_TOTAL_BYTES:
                    raise ScanError(f"archive exceeds extraction size limits: {archive_path.name}")
                if member.compress_size == 0 and member.file_size > 0:
                    raise ScanError(f"invalid compressed entry: {member.filename}")
                if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                    raise ScanError(f"archive entry compression ratio is unsafe: {member.filename}")
                extracted = output_dir / "content" / Path(*source_path.parts)
                extracted.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, extracted.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                files, skipped = self._scan_file(package, source_path.as_posix(), extracted, output_dir)
                prepared.extend(files)
                ignored.extend(skipped)
        return self._deduplicate(prepared), ignored

    def _scan_file(
        self,
        package: RegistryPackage,
        source_name: str,
        source_path: Path,
        output_dir: Path,
    ) -> tuple[list[PreparedFile], list[str]]:
        del output_dir
        if _is_excluded(package, source_name):
            return [], [source_name]
        if package.install.get("mode") == XUNITY_TRANSLATION_MODE:
            source = validate_relative_path(source_name)
            target = PurePosixPath("AutoTranslator", *source.parts)
            validate_target(target.as_posix())
            return [
                PreparedFile(
                    package_id=package.id,
                    source=source_path,
                    source_name=source_name,
                    target=target.as_posix(),
                    sha256=sha256_file(source_path),
                )
            ], []
        suffix = source_path.suffix.casefold()
        target = _override_target(package, source_name)
        if target is None and suffix == ".dll":
            target = _declared_target(source_name)
        if target is None and suffix == ".dll" and package.install.get("scan_dlls", True):
            target = PurePosixPath(classify_managed_dll(source_path)) / source_path.name
        if target is None:
            if suffix in IGNORED_SUFFIXES or suffix != ".dll":
                return [], [source_name]
            raise ScanError(f"cannot determine install target for {source_name}")
        validate_target(target.as_posix())
        return [
            PreparedFile(
                package_id=package.id,
                source=source_path,
                source_name=source_name,
                target=target.as_posix(),
                sha256=sha256_file(source_path),
            )
        ], []

    @staticmethod
    def _deduplicate(files: list[PreparedFile]) -> list[PreparedFile]:
        by_target: dict[str, PreparedFile] = {}
        for file in files:
            previous = by_target.get(file.target.casefold())
            if previous and previous.sha256 != file.sha256:
                raise ScanError(f"package contains conflicting files for {file.target}")
            by_target[file.target.casefold()] = previous or file
        return list(by_target.values())
