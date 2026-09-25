from __future__ import annotations

import fnmatch
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Mapping

import dnfile

from ..domain.errors import ScanError
from ..domain.models import PreparedFile, RegistryPackage
from ..utilities.archive_safety import (
    MAX_ARCHIVE_FILES,
    MAX_ARCHIVE_FILE_BYTES,
    MAX_ARCHIVE_TOTAL_BYTES,
    MAX_COMPRESSION_RATIO,
)
from ..utilities.checksums import sha256_file
from ..utilities.package_paths import (
    STANDARD_ROOTS,
    file_type_is_wildcard,
    file_type_namespace,
    validate_file_type,
    validate_relative_path,
    validate_subpath,
    validate_supply_target,
    validate_target,
)

IGNORED_SUFFIXES = {
    ".pdb",
    ".xml",
    ".md",
    ".txt",
    ".cs",
    ".csproj",
    ".sln",
}

MELONLOADER_MOD_TYPE = "melonloader:mod"
MELONLOADER_PLUGIN_TYPE = "melonloader:plugin"
MELONLOADER_USERLIB_TYPE = "melonloader:userlib"
BEPINEX_PLUGIN_TYPE = "bepinex:plugin"

# 没有入口基类、也看不出引用哪套加载器时的兜底类型。
DEFAULT_MANAGED_TYPE = MELONLOADER_USERLIB_TYPE

MELONLOADER_PREFIX = "melonloader"
BEPINEX_PREFIX = "bepinex"

# v1 的 override 目标根 -> v2 类型：老条目升级时按同一张表换算。
ROOT_TYPES = {
    "Mods": MELONLOADER_MOD_TYPE,
    "Plugins": MELONLOADER_PLUGIN_TYPE,
    "UserLibs": MELONLOADER_USERLIB_TYPE,
}


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


def _matched_rule(package: RegistryPackage, source_name: str) -> dict[str, str] | None:
    for rule in package.file_rules:
        if _matches(str(rule.get("match", "")), source_name):
            return rule
    return None


def _matched_payload_rule(package: RegistryPackage, source_name: str) -> dict[str, str] | None:
    for rule in package.payload_rules:
        if _matches(str(rule.get("match", "")), source_name):
            return rule
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


def _assembly_references(pe: dnfile.dnPE) -> tuple[str, ...]:
    table = pe.net.mdtables.AssemblyRef if pe.net else None
    if table is None:
        return ()
    names: list[str] = []
    for row in table.rows:
        raw = getattr(row, "Name", None)
        name = getattr(raw, "value", raw)
        if name:
            names.append(str(name))
    return tuple(names)


def classify_dll_type(path: Path) -> str:
    """静态判断一个托管 DLL 属于哪个加载器的哪一类。

    只读 PE/.NET 元数据，不 `Assembly.Load`、不执行。基类决定入口类型；看不出入口时按
    引用的程序集判断是哪套加载器的库 —— BepInEx 从 `BepInEx/plugins` 加载，所以引用了
    BepInEx 又没有入口基类的程序集就是它的一支插件，最后兜底 MelonLoader 用户库。
    """
    try:
        pe = dnfile.dnPE(str(path))
    except Exception as exc:
        raise ScanError(f"cannot parse PE metadata for {path.name}: {exc}") from exc
    try:
        if not pe.net or not pe.net.mdtables.TypeDef:
            raise ScanError(f"native or unsupported DLL requires an install rule: {path.name}")
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
                return MELONLOADER_MOD_TYPE
            if namespace == "MelonLoader" and name == "MelonPlugin":
                return MELONLOADER_PLUGIN_TYPE
            if namespace.startswith("BepInEx") and name in {"BaseUnityPlugin", "BasePlugin"}:
                return BEPINEX_PLUGIN_TYPE
            if base in rows:
                return base_kind(base, seen)
            return None

        kinds = {kind for row in rows if (kind := base_kind(row, set()))}
        if len(kinds) > 1:
            raise ScanError(f"assembly contains several mod entry types: {path.name}")
        if kinds:
            return next(iter(kinds))
        references = _assembly_references(pe)
        if any(name == "BepInEx" or name.startswith("BepInEx.") for name in references):
            return BEPINEX_PLUGIN_TYPE
        return DEFAULT_MANAGED_TYPE
    finally:
        close = getattr(pe, "close", None)
        if close:
            close()


class PackageScanner:
    """把 Release 资产里的文件映射成「目标路径 + 类型」。

    目标路径由**供给该类型的加载器**决定：注册表里 `supply` 说了 `melonloader:mod` 装在
    `{Sprocket}/Mods`，这里就把它落在那里。v1 条目没有类型，仍按写死的根与 override 解析。
    """

    def __init__(self, install_directories: Mapping[str, PurePosixPath] | None = None):
        self._directories = {
            validate_file_type(file_type): PurePosixPath(directory)
            for file_type, directory in (install_directories or {}).items()
        }

    def scan(
            self,
            package: RegistryPackage,
            asset_path: Path,
            output_dir: Path,
    ) -> tuple[list[PreparedFile], list[str]]:
        suffix = asset_path.suffix.casefold()
        if suffix == ".dll":
            return self._scan_file(package, asset_path.name, asset_path, output_dir)
        if suffix != ".zip":
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
        suffix = source_path.suffix.casefold()

        target: PurePosixPath | None = None
        if package.uses_payload:
            target = self._payload_target(package, source_name)
        elif package.schema_version >= 2:
            rule = _matched_rule(package, source_name)
            if rule is not None:
                target = self._target_for_type(
                    str(rule["type"]),
                    source_path,
                    source_name,
                    subpath=rule.get("subpath"),
                    layout=str(rule.get("layout") or "file"),
                )
            elif suffix == ".dll" and package.install.get("scan_dlls", True):
                target = self._target_for_type(
                    classify_dll_type(source_path), source_path, source_name, layout="file"
                )
        else:
            target = _override_target(package, source_name)
            if target is None and suffix == ".dll":
                target = _declared_target(source_name)
            if target is None and suffix == ".dll" and package.install.get("scan_dlls", True):
                target = PurePosixPath(self._legacy_root(classify_dll_type(source_path))) / source_path.name

        if target is None:
            if suffix in IGNORED_SUFFIXES or suffix != ".dll":
                return [], [source_name]
            raise ScanError(f"cannot determine install target for {source_name}")
        return [
            PreparedFile(
                package_id=package.id,
                source=source_path,
                source_name=source_name,
                target=target.as_posix(),
                sha256=sha256_file(source_path),
            )
        ], []

    def _payload_target(self, package: RegistryPackage, source_name: str) -> PurePosixPath | None:
        """加载器自己的载荷：目标直接写在规则里，不查任何供给表。

        一个加载器供给哪些**别人**用的类型（`supply`）与它自己装在哪里（`payload`）是两回事，
        所以这条线不经过「谁供给这个类型」。
        """
        rule = _matched_payload_rule(package, source_name)
        if rule is None:
            return None
        directory = validate_supply_target(str(rule.get("target", "")))
        parts = [part for part in directory.parts if part != "."]
        if rule.get("subpath"):
            parts.extend(validate_subpath(str(rule["subpath"])).parts)
        if str(rule.get("layout") or "file") == "tree":
            parts.extend(validate_relative_path(source_name).parts)
        else:
            parts.append(PurePosixPath(source_name).name)
        target = PurePosixPath(*parts)
        validate_relative_path(target.as_posix())
        return target

    def _target_for_type(
            self,
            file_type: str,
            source_path: Path,
            source_name: str,
            *,
            subpath: object = None,
            layout: str = "file",
    ) -> PurePosixPath:
        file_type = validate_file_type(file_type)
        if file_type_is_wildcard(file_type):
            concrete = classify_dll_type(source_path)
            if file_type_namespace(concrete) != file_type_namespace(file_type):
                raise ScanError(
                    f"{source_name} is not a {file_type_namespace(file_type)} module"
                )
            file_type = concrete
        directory = self._directories.get(file_type)
        if directory is None:
            raise ScanError(
                f"no installed modloader supplies the install type {file_type!r}"
            )
        parts = [part for part in directory.parts if part != "."]
        if subpath:
            parts.extend(validate_subpath(str(subpath)).parts)
        if layout == "tree":
            # 加载器自己的载荷：ZIP 里的目录结构就是游戏目录里的结构。
            parts.extend(validate_relative_path(source_name).parts)
        else:
            parts.append(PurePosixPath(source_name).name)
        target = PurePosixPath(*parts)
        validate_relative_path(target.as_posix())
        return target

    @staticmethod
    def _legacy_root(file_type: str) -> str:
        for root, mapped in ROOT_TYPES.items():
            if mapped == file_type:
                return root
        return "UserLibs"

    @staticmethod
    def _deduplicate(files: list[PreparedFile]) -> list[PreparedFile]:
        by_target: dict[str, PreparedFile] = {}
        for file in files:
            previous = by_target.get(file.target.casefold())
            if previous and previous.sha256 != file.sha256:
                raise ScanError(f"package contains conflicting files for {file.target}")
            by_target[file.target.casefold()] = previous or file
        return list(by_target.values())
