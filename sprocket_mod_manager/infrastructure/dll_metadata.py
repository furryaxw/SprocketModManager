"""静态读取 DLL 内嵌元数据（只读 PE/CLR 字节，绝不加载或执行目标程序集）。

字段来源与优先级见 `SprocketModAPI/docs/mod-metadata.md`；DLL 分类规则见
`sprocket-mod-spec.md` 的「DLL 分类」。本模块只做读取，不做 Registry 匹配。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import dnfile
import pefile

from ..domain.errors import ScanError
from ..utilities.checksums import sha256_file

MELON_KIND_MODS = "Mods"
MELON_KIND_PLUGINS = "Plugins"

MELON_MOD_BASE = ("MelonLoader", "MelonMod")
MELON_PLUGIN_BASE = ("MelonLoader", "MelonPlugin")

# MelonPluginInfoAttribute / MelonModInfoAttribute 都继承 MelonInfoAttribute（见 MelonLoader 源码），
# 三者的元数据 blob 布局完全相同，所以按名字统一处理。
MELON_INFO_TYPES = frozenset({"MelonInfoAttribute", "MelonModInfoAttribute", "MelonPluginInfoAttribute"})
MELON_CREDITS_TYPE = "MelonAdditionalCreditsAttribute"
# 这两个特性只存在于程序集上，MelonBase 不暴露它们（游戏内菜单与管理器都直接读程序集特性）。
MELON_ADDITIONAL_DEPENDENCIES_TYPE = "MelonAdditionalDependenciesAttribute"
MELON_INCOMPATIBLE_ASSEMBLIES_TYPE = "MelonIncompatibleAssembliesAttribute"

ASSEMBLY_METADATA_TYPE = ("System.Reflection", "AssemblyMetadataAttribute")
ASSEMBLY_FILE_VERSION_TYPE = ("System.Reflection", "AssemblyFileVersionAttribute")
TARGET_FRAMEWORK_TYPE = ("System.Runtime.Versioning", "TargetFrameworkAttribute")

SPROCKET_PREFIX = "Sprocket.Mod."

# 契约键名 -> 管理器使用的规范化小写短名。比较时先去掉下划线/连字符再折成小写。
SPROCKET_KEY_MAP = {
    "id": "id",
    "displayname": "display_name",
    "description": "description",
    "authors": "authors",
    "homepage": "homepage",
    "repository": "repository",
    "category": "category",
    "license": "license",
}

# 自定义特性 blob 的固定开头（ECMA-335 II.23.3：Prolog = 0x0001 小端）。
CUSTOM_ATTRIBUTE_PROLOG = b"\x01\x00"

# MelonLoader 的三个 Info 构造函数（签名参数种类 -> 语义）。类型种类见 `_read_element_type`：
# T=System.Type，s=string，i=int32，A=string[]。
_MELON_INFO_SHAPES = {
    ("T", "s", "s", "s", "s"): "text",
    ("T", "s", "s", "s", "s", "A"): "text_with_credits",
    ("T", "s", "i", "i", "i", "s", "s"): "numeric",
    ("T", "s", "i", "i", "i", "s", "s", "s"): "numeric_with_identifier",
}

_INTEGER_WIDTHS = {"i1": 1, "i2": 2, "i": 4, "i8": 8}

# 只有这些特性承载本模块对外暴露的字段；它们解码失败才算「降级」，其余特性静默跳过，
# 避免 `errors` 被 HarmonyPatch 之类的无关特性淹掉。
_INTERESTING_ATTRIBUTES = frozenset(
    {
        ASSEMBLY_METADATA_TYPE,
        ASSEMBLY_FILE_VERSION_TYPE,
        TARGET_FRAMEWORK_TYPE,
        ("MelonLoader", MELON_CREDITS_TYPE),
        ("MelonLoader", MELON_ADDITIONAL_DEPENDENCIES_TYPE),
        ("MelonLoader", MELON_INCOMPATIBLE_ASSEMBLIES_TYPE),
    }
)


class _MalformedAttribute(ValueError):
    """单个自定义特性 blob 畸形；只影响该特性，不影响其余字段。"""


@dataclass(frozen=True)
class DllMetadata:
    """静态解析结果。任何取不到的字段都是 None，问题记录在 `errors`。

    `is_managed` 表示存在 CLR 元数据；`is_native` 表示 PE 解析成功但没有 CLR 元数据。
    两者可能同时为 False（PE 本身畸形或无法解析），此时 `errors` 里会有原因。
    """

    path: str
    is_managed: bool
    is_native: bool
    assembly_name: str | None
    assembly_version: str | None
    file_version: str | None
    target_framework: str | None
    melon_kind: str | None
    melon_name: str | None
    melon_version: str | None
    melon_author: str | None
    melon_download_link: str | None
    melon_credits: str | None
    sprocket: dict[str, str]
    errors: tuple[str, ...]
    required_dependencies: tuple[str, ...] = ()
    incompatible_assemblies: tuple[str, ...] = ()


def melon_info_description(metadata: DllMetadata) -> str | None:
    """把 MelonInfo 的字段合成一行人类可读摘要，没有 MelonInfo 时返回 None。

    MelonInfo **没有描述字段**（见 `mod-metadata.md`），所以这里不是「描述」，
    只是名称/版本/作者的拼接，供界面在缺少 `Sprocket.Mod.Description` 时兜底显示。
    """
    if not metadata.melon_name and not metadata.melon_version:
        return None
    text = metadata.melon_name or Path(metadata.path).stem
    if metadata.melon_version:
        text = f"{text} {metadata.melon_version}"
    if metadata.melon_author:
        text = f"{text} by {metadata.melon_author}"
    if metadata.melon_download_link:
        text = f"{text} ({metadata.melon_download_link})"
    return text


def read_dll_metadata(path: str | os.PathLike[str]) -> DllMetadata:
    """静态读取一个 DLL 的内嵌元数据。

    只解析字节，不加载程序集、不执行 DLL 代码。任何字段级失败（畸形特性 blob、
    缺失的表、无法解析的签名等）都降级为 None 并追加到 `errors`，不抛异常。

    仅两种根本性错误抛 `ScanError`：
    1. 路径不存在或不可读；
    2. 文件没有 `MZ` DOS 头（根本不是 PE 文件）。
    有 `MZ` 头但内部结构损坏的文件不抛异常，返回 `is_managed=False, is_native=False`
    并在 `errors` 里说明。
    """
    target = Path(path)
    if not target.is_file():
        raise ScanError(f"not a readable file: {target}")
    try:
        with target.open("rb") as handle:
            magic = handle.read(2)
    except OSError as exc:
        raise ScanError(f"cannot read {target.name}: {exc}") from exc
    if magic != b"MZ":
        raise ScanError(f"not a PE file: {target.name}")

    errors: list[str] = []
    pe = _open_pe(target, errors)
    try:
        net = getattr(pe, "net", None) if pe is not None else None
        is_managed = net is not None
        is_native = pe is not None and net is None

        assembly_name: str | None = None
        assembly_version: str | None = None
        if net is not None:
            assembly_name, assembly_version = _read_assembly_identity(net, errors)

        file_version: str | None = None
        target_framework: str | None = None
        melon_kind: str | None = None
        melon_name = melon_version = melon_author = melon_download_link = None
        melon_credits: str | None = None
        required_dependencies: tuple[str, ...] = ()
        incompatible_assemblies: tuple[str, ...] = ()
        metadata_raw: dict[str, str] = {}

        if net is not None:
            melon_kind = _read_melon_kind(net, errors)
            for attribute in _iter_attributes(net, errors):
                if attribute.full_name == ASSEMBLY_METADATA_TYPE:
                    _collect_metadata(attribute, metadata_raw, errors)
                elif attribute.full_name == ASSEMBLY_FILE_VERSION_TYPE:
                    file_version = file_version or _first_string(attribute)
                elif attribute.full_name == TARGET_FRAMEWORK_TYPE:
                    target_framework = target_framework or _first_string(attribute)
                elif attribute.namespace == "MelonLoader" and attribute.name in MELON_INFO_TYPES:
                    if melon_name is None and melon_version is None and melon_author is None:
                        info = _read_melon_info(attribute, errors)
                        melon_name, melon_version, melon_author, melon_download_link, credits = info
                        melon_credits = melon_credits or credits
                    else:
                        errors.append(f"ignored duplicate MelonInfo attribute: {attribute.name}")
                elif attribute.full_name == ("MelonLoader", MELON_CREDITS_TYPE):
                    melon_credits = melon_credits or _first_string(attribute)
                elif attribute.full_name == ("MelonLoader", MELON_ADDITIONAL_DEPENDENCIES_TYPE):
                    required_dependencies = required_dependencies + _string_array(attribute)
                elif attribute.full_name == ("MelonLoader", MELON_INCOMPATIBLE_ASSEMBLIES_TYPE):
                    incompatible_assemblies = incompatible_assemblies + _string_array(attribute)

        if file_version is None:
            # 托管程序集没有 AssemblyFileVersionAttribute 时，回退到 PE 的 VS_FIXEDFILEINFO。
            file_version = _read_fixed_file_version(target, errors)

        return DllMetadata(
            path=str(target),
            is_managed=is_managed,
            is_native=is_native,
            assembly_name=assembly_name,
            assembly_version=assembly_version,
            file_version=file_version,
            target_framework=target_framework,
            melon_kind=melon_kind,
            melon_name=melon_name,
            melon_version=melon_version,
            melon_author=melon_author,
            melon_download_link=melon_download_link,
            melon_credits=melon_credits,
            sprocket=_sprocket_fields(metadata_raw),
            errors=tuple(errors),
            required_dependencies=_dedupe(required_dependencies),
            incompatible_assemblies=_dedupe(incompatible_assemblies),
        )
    finally:
        if pe is not None:
            close = getattr(pe, "close", None)
            if close:
                close()


def pe_file_version(path: str | os.PathLike[str]) -> str | None:
    """PE 资源里的 `VS_FIXEDFILEINFO` 版本（`major.minor.patch`）；读不出来返回 `None`。

    运行时检测只需要「这个 DLL 是哪版」，不需要完整解析程序集，所以单独开这一条读法；
    修订号省掉，运行时的版本写法就是三段。
    """
    version = _read_fixed_file_version(Path(path), [])
    if not version:
        return None
    return ".".join(version.split(".")[:3])


def _open_pe(path: Path, errors: list[str]) -> object | None:
    try:
        return dnfile.dnPE(str(path))
    except Exception as exc:  # dnfile 对损坏的 PE 会抛各种异常
        errors.append(f"cannot parse PE metadata: {exc}")
        return None


def _read_assembly_identity(net: object, errors: list[str]) -> tuple[str | None, str | None]:
    table = getattr(getattr(net, "mdtables", None), "Assembly", None)
    rows = list(getattr(table, "rows", ()) or ())
    if not rows:
        return None, None
    try:
        row = rows[0]
        name = str(getattr(row, "Name", "") or "") or None
        version = "{}.{}.{}.{}".format(
            int(getattr(row, "MajorVersion", 0)),
            int(getattr(row, "MinorVersion", 0)),
            int(getattr(row, "BuildNumber", 0)),
            int(getattr(row, "RevisionNumber", 0)),
        )
        return name, version
    except Exception as exc:
        errors.append(f"cannot read Assembly table: {exc}")
        return None, None


def _read_melon_kind(net: object, errors: list[str]) -> str | None:
    """按 TypeDef.Extends 继承链判断 MelonMod / MelonPlugin。

    与 `scanner.classify_dll_type` 的判据一致，但这里是独立实现：本模块不改 scanner，
    而且需要在已经打开的 PE 上复用（避免二次解析）。
    """
    table = getattr(getattr(net, "mdtables", None), "TypeDef", None)
    rows = tuple(getattr(table, "rows", ()) or ())
    if not rows:
        return None

    def base_kind(row: object, seen: set[int]) -> str | None:
        identity = id(row)
        if identity in seen:
            return None
        seen.add(identity)
        base = getattr(getattr(row, "Extends", None), "row", None)
        if base is None:
            return None
        namespace = str(getattr(base, "TypeNamespace", None) or getattr(base, "Namespace", None) or "")
        name = str(getattr(base, "TypeName", None) or getattr(base, "Name", None) or "")
        if (namespace, name) == MELON_MOD_BASE:
            return MELON_KIND_MODS
        if (namespace, name) == MELON_PLUGIN_BASE:
            return MELON_KIND_PLUGINS
        if base in rows:
            return base_kind(base, seen)
        return None

    kinds = {kind for row in rows if (kind := base_kind(row, set()))}
    if len(kinds) > 1:
        errors.append("assembly derives from both MelonMod and MelonPlugin")
        return None
    return next(iter(kinds), None)


@dataclass(frozen=True)
class _Attribute:
    namespace: str
    name: str
    shape: str | None
    arguments: tuple[object, ...]

    @property
    def full_name(self) -> tuple[str, str]:
        return (self.namespace, self.name)


def _iter_attributes(net: object, errors: list[str]) -> list[_Attribute]:
    table = getattr(getattr(net, "mdtables", None), "CustomAttribute", None)
    try:
        rows = tuple(getattr(table, "rows", ()) or ())
    except Exception as exc:
        errors.append(f"cannot read CustomAttribute table: {exc}")
        return []

    local_types = _local_method_types(net)
    attributes: list[_Attribute] = []
    for index, row in enumerate(rows):
        try:
            type_row = getattr(getattr(row, "Type", None), "row", None)
        except Exception as exc:
            errors.append(f"cannot read custom attribute at row {index}: {exc}")
            continue
        declared = _declaring_type(type_row, local_types)
        if declared is None:
            errors.append(f"cannot resolve custom attribute type at row {index}")
            continue
        try:
            value = getattr(row, "Value", None)
            blob = getattr(value, "value", None) if value is not None else None
            kinds = _parse_ctor_signature(type_row)
            arguments = _decode_arguments(blob, kinds)
        except _MalformedAttribute as exc:
            if _is_interesting(declared):
                errors.append(f"malformed {declared[1]} at row {index}: {exc}")
            continue
        except Exception as exc:  # dnfile 内部的懒加载也可能在这里失败
            if _is_interesting(declared):
                errors.append(f"cannot read {declared[1]} at row {index}: {exc}")
            continue
        attributes.append(
            _Attribute(
                namespace=declared[0],
                name=declared[1],
                shape=_MELON_INFO_SHAPES.get(kinds),
                arguments=arguments,
            )
        )
    return attributes


def _is_interesting(declared: tuple[str, str]) -> bool:
    if declared in _INTERESTING_ATTRIBUTES:
        return True
    return declared[0] == "MelonLoader" and declared[1] in MELON_INFO_TYPES


def _local_method_types(net: object) -> dict[int, tuple[str, str]]:
    """MethodDef row -> 声明它的 TypeDef 的 (namespace, name)。

    特性类定义在**同一个**程序集里时（少见但合法），CustomAttribute.Type 指向 MethodDef
    而不是 MemberRef，需要用 TypeDef.MethodList 反查。
    """
    mapping: dict[int, tuple[str, str]] = {}
    table = getattr(getattr(net, "mdtables", None), "TypeDef", None)
    for row in getattr(table, "rows", ()) or ():
        namespace = str(getattr(row, "TypeNamespace", None) or getattr(row, "Namespace", None) or "")
        name = str(getattr(row, "TypeName", None) or getattr(row, "Name", None) or "")
        for handle in getattr(row, "MethodList", None) or ():
            method = getattr(handle, "row", None)
            if method is not None:
                mapping[id(method)] = (namespace, name)
    return mapping


def _declaring_type(type_row: object, local_types: dict[int, tuple[str, str]]) -> tuple[str, str] | None:
    if isinstance(type_row, dnfile.mdtable.MemberRefRow):
        target = getattr(type_row.Class, "row", None)
        namespace = str(getattr(target, "TypeNamespace", None) or getattr(target, "Namespace", None) or "")
        name = str(getattr(target, "TypeName", None) or getattr(target, "Name", None) or "")
        return (namespace, name) if name else None
    if isinstance(type_row, dnfile.mdtable.MethodDefRow):
        return local_types.get(id(type_row))
    if isinstance(type_row, dnfile.mdtable.TypeRefRow):
        return (str(type_row.TypeNamespace or ""), str(type_row.TypeName or ""))
    return None


def _parse_ctor_signature(type_row: object) -> tuple[str, ...]:
    """从构造函数签名里取出参数种类，用它决定 blob 里有多少个固定参数。

    必须按签名解码：blob 在固定参数之后还有 `uint16 NumNamed` 和具名参数，
    靠启发式「一直读 SerString」会把具名参数当成位置参数（例如
    `TargetFrameworkAttribute` 的 `FrameworkDisplayName`）。
    """
    signature = getattr(getattr(type_row, "Signature", None), "value", None)
    if not signature:
        raise _MalformedAttribute("missing constructor signature")
    if signature[0] & 0x0F or (signature[0] & 0x10):
        raise _MalformedAttribute("unsupported calling convention")
    try:
        count, pos = _read_compressed_uint(signature, 1)
        _, pos = _read_element_type(signature, pos)
        kinds = []
        for _ in range(count):
            kind, pos = _read_element_type(signature, pos)
            kinds.append(kind)
    except _MalformedAttribute:
        raise
    except Exception as exc:
        raise _MalformedAttribute(f"cannot parse constructor signature: {exc}") from exc
    return tuple(kinds)


def _decode_arguments(blob: bytes | None, kinds: tuple[str, ...]) -> tuple[object, ...]:
    if not blob:
        raise _MalformedAttribute("empty attribute blob")
    if not blob.startswith(CUSTOM_ATTRIBUTE_PROLOG):
        raise _MalformedAttribute("missing 0x0001 prolog")
    position = 2
    values: list[object] = []
    for kind in kinds:
        if kind in ("s", "T"):
            # SerString：1 字节长度 + UTF-8；0xFF 表示 null。System.Type 参数同样按字符串编码。
            value, position = _read_ser_string(blob, position)
            values.append(value)
        elif kind in _INTEGER_WIDTHS:
            width = _INTEGER_WIDTHS[kind]
            if position + width > len(blob):
                raise _MalformedAttribute(f"truncated {kind} argument")
            values.append(int.from_bytes(blob[position:position + width], "little", signed=True))
            position += width
        elif kind == "A":
            if position + 4 > len(blob):
                raise _MalformedAttribute("truncated array length")
            count = int.from_bytes(blob[position:position + 4], "little", signed=False)
            position += 4
            if count == 0xFFFFFFFF:
                values.append(None)
                continue
            items: list[str | None] = []
            for _ in range(count):
                item, position = _read_ser_string(blob, position)
                items.append(item)
            values.append(tuple(items))
        else:
            raise _MalformedAttribute(f"unsupported argument type {kind!r}")
    # 固定参数之后是 `uint16 NumNamed` + 具名参数，本模块只关心固定参数。
    return tuple(values)


def _read_compressed_uint(data: bytes, position: int) -> tuple[int, int]:
    if position >= len(data):
        raise _MalformedAttribute("truncated compressed integer")
    first = data[position]
    if first & 0x80 == 0:
        return first, position + 1
    if first & 0xC0 == 0x80:
        if position + 1 >= len(data):
            raise _MalformedAttribute("truncated compressed integer")
        return ((first & 0x3F) << 8) | data[position + 1], position + 2
    if position + 4 >= len(data):
        raise _MalformedAttribute("truncated compressed integer")
    return int.from_bytes(data[position + 1:position + 5], "little"), position + 5


def _read_ser_string(data: bytes, position: int) -> tuple[str | None, int]:
    # SerString（ECMA-335 II.23.3）：首字节 0xFF 表示 null；否则长度是压缩无符号整数
    # （长度 >= 0x80 时长度字段占 2 字节，这一点最容易被只读 1 个字节的实现踩坑）。
    if position >= len(data):
        raise _MalformedAttribute("truncated SerString")
    if data[position] == 0xFF:
        return None, position + 1
    length, position = _read_compressed_uint(data, position)
    end = position + length
    if end > len(data):
        raise _MalformedAttribute("SerString runs past the blob")
    return data[position:end].decode("utf-8", errors="replace"), end


def _read_element_type(data: bytes, position: int) -> tuple[str, int]:
    """读一个签名元素类型，返回 (种类, 新位置)。种类见 `_MELON_INFO_SHAPES`。

    只覆盖自定义特性参数会用到的类型；遇到协变/泛型等复杂类型直接抛 `_MalformedAttribute`，
    由调用方把该特性降级跳过。
    """
    if position >= len(data):
        raise _MalformedAttribute("truncated signature")
    tag = data[position]
    position += 1
    if tag == 0x0E:
        return "s", position
    if tag in (0x02, 0x04, 0x05):
        return "i1", position
    if tag in (0x03, 0x06, 0x07):
        return "i2", position
    if tag in (0x08, 0x09):
        return "i", position
    if tag in (0x0A, 0x0B):
        return "i8", position
    if tag in (0x01, 0x0C, 0x0D, 0x14, 0x1C):
        return "?", position
    if tag in (0x11, 0x12):
        _, position = _read_compressed_uint(data, position)
        return ("T" if tag == 0x12 else "?"), position
    if tag in (0x13, 0x1E):
        _, position = _read_compressed_uint(data, position)
        return "?", position
    if tag == 0x1D:  # SZARRAY
        inner, position = _read_element_type(data, position)
        return ("A" if inner == "s" else "?"), position
    if tag in (0x0F, 0x10, 0x45):  # PTR / BYREF / PINNED
        _, position = _read_element_type(data, position)
        return "?", position
    if tag in (0x1F, 0x20):  # CMOD_REQD / CMOD_OPT：先修正符再真实类型
        _, position = _read_compressed_uint(data, position)
        return _read_element_type(data, position)
    raise _MalformedAttribute(f"unsupported signature element type 0x{tag:02X}")


def _first_string(attribute: _Attribute) -> str | None:
    for argument in attribute.arguments:
        if isinstance(argument, str):
            return argument
    return None


def _string_array(attribute: _Attribute) -> tuple[str, ...]:
    """取 `params string[]` 参数里的程序集名（裁剪空白，空项丢弃）。"""
    values: list[str] = []
    for argument in attribute.arguments:
        if isinstance(argument, tuple):
            for item in argument:
                if isinstance(item, str) and item.strip():
                    values.append(item.strip())
        elif isinstance(argument, str) and argument.strip():
            values.append(argument.strip())
    return tuple(values)


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    """保序去重；多个同类特性叠加时同样去重。"""
    return tuple(dict.fromkeys(values))


def _collect_metadata(attribute: _Attribute, target: dict[str, str], errors: list[str]) -> None:
    if len(attribute.arguments) < 2:
        errors.append("AssemblyMetadataAttribute without key/value arguments")
        return
    key = attribute.arguments[0]
    value = attribute.arguments[1]
    if not isinstance(key, str):
        errors.append("AssemblyMetadataAttribute with a non-string key")
        return
    if not isinstance(value, str):
        value = ""
    if key in target:
        # 契约允许 AssemblyMetadata 重复出现；v1 取第一条，重复的记录下来便于排查。
        errors.append(f"duplicate AssemblyMetadata key ignored: {key}")
        return
    target[key] = value


def _sprocket_fields(metadata_raw: dict[str, str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in metadata_raw.items():
        if not key.lower().startswith(SPROCKET_PREFIX.lower()):
            continue
        suffix = key[len(SPROCKET_PREFIX):]
        normalized = SPROCKET_KEY_MAP.get(suffix.lower().replace("_", "").replace("-", ""))
        if normalized is None:
            # 未知的 Sprocket.Mod.* 键**直接丢弃**：DLL 里只保留规范化后的 sprocket 字段。
            continue
        fields.setdefault(normalized, value)
    return fields


def _read_melon_info(
        attribute: _Attribute,
        errors: list[str],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """按构造函数形状取 name/version/author/downloadLink（+ 旧版内联 credits）。"""
    arguments = attribute.arguments
    shape = attribute.shape
    try:
        if shape == "text":
            return _string_at(arguments, 1), _string_at(arguments, 2), _string_at(arguments, 3), (
                _string_at(arguments, 4)
            ), None
        if shape == "text_with_credits":
            credits = arguments[5]
            joined = ", ".join(item for item in credits if item) if isinstance(credits, tuple) else ""
            return _string_at(arguments, 1), _string_at(arguments, 2), _string_at(arguments, 3), (
                _string_at(arguments, 4)
            ), joined or None
        if shape == "numeric":
            # MelonLoader 源码：$"{major}.{minor}.{revision}"（identifier 为空时省略）。
            version = "{}.{}.{}".format(arguments[2], arguments[3], arguments[4])
            return _string_at(arguments, 1), version, _string_at(arguments, 5), (
                _string_at(arguments, 6)
            ), None
        if shape == "numeric_with_identifier":
            version = "{}.{}.{}{}".format(arguments[2], arguments[3], arguments[4], arguments[5] or "")
            return _string_at(arguments, 1), version, _string_at(arguments, 6), (
                _string_at(arguments, 7)
            ), None
    except (IndexError, TypeError) as exc:
        errors.append(f"malformed {attribute.name} arguments: {exc}")
        return None, None, None, None, None
    errors.append(f"unsupported {attribute.name} constructor signature")
    return None, None, None, None, None


def _string_at(arguments: tuple[object, ...], index: int) -> str | None:
    if index >= len(arguments):
        return None
    value = arguments[index]
    return value if isinstance(value, str) and value else None


def _read_fixed_file_version(path: Path, errors: list[str]) -> str | None:
    """从 PE 资源里的 VS_FIXEDFILEINFO 取文件版本（原生 DLL 或没写特性的托管 DLL 用）。"""
    try:
        pe = pefile.PE(str(path), fast_load=True)
    except Exception as exc:
        errors.append(f"cannot read PE version info: {exc}")
        return None
    try:
        # fast_load 不解析资源目录，而 VS_FIXEDFILEINFO 在资源里，必须显式解析。
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        entries = getattr(pe, "VS_FIXEDFILEINFO", None)
        if not entries:
            return None
        info = entries[0]
        if info.Signature != 0xFEEF04BD:
            return None
        return "{}.{}.{}.{}".format(
            (info.FileVersionMS >> 16) & 0xFFFF,
            info.FileVersionMS & 0xFFFF,
            (info.FileVersionLS >> 16) & 0xFFFF,
            info.FileVersionLS & 0xFFFF,
        )
    except Exception as exc:
        errors.append(f"cannot decode VS_FIXEDFILEINFO: {exc}")
        return None
    finally:
        close = getattr(pe, "close", None)
        if close:
            close()


_METADATA_CACHE: dict[tuple[str, int, int], DllMetadata] = {}
_METADATA_CACHE_LIMIT = 512

# 解析 1MB+ 程序集的自定义特性表要花上百毫秒（13 个 DLL 冷启动约 2 秒），所以除了内存缓存，
# 还落一份磁盘缓存，但**按内容 hash 键**：`files{路径: {size, mtime, hash}}` + `meta{hash: 解析结果}`。
# 这样同一份 DLL 换了路径/名字可以复用解析结果，(size, mtime) 巧合也不会误命中。
# 快路径仍然先看 (size, mtime)（免去每次刷新都算 hash），未命中才真正算 hash。
_CACHE_VERSION = 1
_disk_cache_path: Path | None = None
_sections: dict[str, dict] = {"files": {}, "meta": {}}
_sections_loaded = False
_sections_dirty = False

_metadata_backend: object | None = None


def configure_metadata_cache(path: str | os.PathLike[str] | None) -> None:
    """指定**独立**的缓存文件；传 None 关闭磁盘缓存。

    管理器里走 `configure_metadata_backend`（`infrastructure/file_metadata.py`，
    文件是 `<game>/SprocketModManager/file-metadata.json`）。这个函数留给独立使用方与测试。
    """
    global _disk_cache_path, _sections, _sections_loaded, _sections_dirty, _metadata_backend
    _metadata_backend = None
    _disk_cache_path = Path(path) if path else None
    _sections = {"files": {}, "meta": {}}
    _sections_loaded = False
    _sections_dirty = False


def _disk_cache_enabled() -> bool:
    """磁盘缓存是否可用：要么挂了后端，要么配了独立缓存文件。"""
    return _metadata_backend is not None or _disk_cache_path is not None


def configure_metadata_backend(backend: object | None) -> None:
    """把缓存后端换成独立文件/LRU 之类；传 None 回到"独立缓存文件"模式。

    后端只需实现 `load_sections() -> dict` 与 `store_sections(sections)`，
    sections 形状为 `{"schema_version": 1, "files": {...}, "meta": {...}}`。
    """
    global _metadata_backend, _disk_cache_path, _sections, _sections_loaded, _sections_dirty
    _metadata_backend = backend
    _disk_cache_path = None
    _sections = {"files": {}, "meta": {}}
    _sections_loaded = False
    _sections_dirty = False


def _load_sections() -> None:
    global _sections_loaded, _sections
    if _sections_loaded:
        return
    _sections_loaded = True

    payload: dict | None = None
    if _metadata_backend is not None:
        try:
            candidate = _metadata_backend.load_sections()
        except (OSError, ValueError):
            # 后端坏了不是错误：丢掉重新解析即可。
            return
        payload = candidate if isinstance(candidate, dict) else None
    elif _disk_cache_path is not None and _disk_cache_path.is_file():
        try:
            candidate = json.loads(_disk_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            candidate = None
        if isinstance(candidate, dict) and candidate.get("schema_version") == _CACHE_VERSION:
            payload = candidate

    if payload is None:
        return
    files = payload.get("files")
    meta = payload.get("meta")
    _sections = {
        "files": dict(files) if isinstance(files, dict) else {},
        "meta": dict(meta) if isinstance(meta, dict) else {},
    }


def prune_metadata_cache() -> dict[str, int]:
    """清掉缓存里已经没意义的部分，返回 `{"files": n, "meta": n}`（被删掉的条数）。

    两件事，都是"文件名与 hash 的绑定关系"在说话：

    1. **磁盘上不存在的路径**：手工删掉的 DLL、临时目录、改名前的旧路径 —— 它们永远不会再被
       查询，留着只会让 `file-metadata.json` 无限长。
    2. **没有任何文件引用的 hash**：同一个文件重编一次就换一个 hash，旧解析结果再没人要。

    只有"文件名 → hash"这条绑定还活着的时候，hash 才值得留。必须在 `_load_sections()` 之后调用。
    """
    global _sections_dirty
    files = _sections.get("files")
    metas = _sections.get("meta")
    if not isinstance(files, dict) or not isinstance(metas, dict):
        return {"files": 0, "meta": 0}

    dropped_files = 0
    for path in list(files):
        try:
            exists = Path(path).exists()
        except OSError:
            exists = False
        if not exists:
            files.pop(path, None)
            dropped_files += 1

    referenced = {
        str(entry.get("hash"))
        for entry in files.values()
        if isinstance(entry, dict) and entry.get("hash")
    }
    dropped_meta = 0
    for digest in list(metas):
        if str(digest) not in referenced:
            metas.pop(digest, None)
            dropped_meta += 1

    if dropped_files or dropped_meta:
        _sections_dirty = True
    return {"files": dropped_files, "meta": dropped_meta}


def flush_metadata_cache() -> bool:
    """把新增/更新的条目写回去（原子写 / 后端写）。返回是否真的写了。

    写之前先做一次清理（见 :func:`prune_metadata_cache`），所以缓存大小只会跟着**当前存在的
    受管文件**走，不会随着历史构建无限增长。
    """
    global _sections_dirty
    if _metadata_backend is not None or _disk_cache_path is not None:
        _load_sections()
        prune_metadata_cache()
    if not _sections_dirty:
        return False

    payload = {
        "schema_version": _CACHE_VERSION,
        "files": _sections["files"],
        "meta": _sections["meta"],
    }
    if _metadata_backend is not None:
        try:
            _metadata_backend.store_sections(payload)
        except (OSError, ValueError):
            return False
        _sections_dirty = False
        return True
    if _disk_cache_path is None:
        return False

    try:
        _disk_cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = _disk_cache_path.with_suffix(_disk_cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, _disk_cache_path)
    except OSError:
        return False
    _sections_dirty = False
    return True


def _serialize(metadata: DllMetadata) -> dict:
    return {
        "is_managed": metadata.is_managed,
        "is_native": metadata.is_native,
        "assembly_name": metadata.assembly_name,
        "assembly_version": metadata.assembly_version,
        "file_version": metadata.file_version,
        "target_framework": metadata.target_framework,
        "melon_kind": metadata.melon_kind,
        "melon_name": metadata.melon_name,
        "melon_version": metadata.melon_version,
        "melon_author": metadata.melon_author,
        "melon_download_link": metadata.melon_download_link,
        "melon_credits": metadata.melon_credits,
        "sprocket": metadata.sprocket,
        "errors": list(metadata.errors),
        "required_dependencies": list(metadata.required_dependencies),
        "incompatible_assemblies": list(metadata.incompatible_assemblies),
    }


def _deserialize(path: str, data: dict) -> DllMetadata:
    return DllMetadata(
        path=path,
        is_managed=bool(data.get("is_managed")),
        is_native=bool(data.get("is_native")),
        assembly_name=data.get("assembly_name"),
        assembly_version=data.get("assembly_version"),
        file_version=data.get("file_version"),
        target_framework=data.get("target_framework"),
        melon_kind=data.get("melon_kind"),
        melon_name=data.get("melon_name"),
        melon_version=data.get("melon_version"),
        melon_author=data.get("melon_author"),
        melon_download_link=data.get("melon_download_link"),
        melon_credits=data.get("melon_credits"),
        sprocket=dict(data.get("sprocket") or {}),
        # JSON 里是 list，这里恢复成元组，保持与直接解析完全一致的形状。
        errors=tuple(data.get("errors") or ()),
        required_dependencies=tuple(data.get("required_dependencies") or ()),
        incompatible_assemblies=tuple(data.get("incompatible_assemblies") or ()),
    )


def cached_sha256(path: str | os.PathLike[str]) -> str | None:
    """取文件的 SHA-256，带 **(size, mtime) 快路径**；文件读不了返回 `None`。

    列表刷新要靠它"实时识别"文件是不是某个发布版本 —— 没被改过的文件只做一次 `stat`，
    不会每次都重算 hash。命中过 hash 的文件会把 (size, mtime, hash) 记进元数据缓存文件。
    """
    global _sections_dirty

    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return None

    if _disk_cache_enabled():
        _load_sections()
        entry = _sections["files"].get(str(target))
        if (isinstance(entry, dict) and entry.get("size") == stat.st_size
                and entry.get("mtime") == stat.st_mtime_ns and entry.get("hash")):
            return str(entry["hash"])

    try:
        digest = sha256_file(target)
    except OSError:
        return None
    if not digest:
        return None

    if _disk_cache_enabled():
        _sections["files"][str(target)] = {
            "size": stat.st_size,
            "mtime": stat.st_mtime_ns,
            "hash": digest,
        }
        _sections_dirty = True
    return digest


def read_cached_metadata(path: str | os.PathLike[str]) -> DllMetadata:
    """按**内容 hash** 缓存 :func:`read_dll_metadata`（内存 + 可选磁盘）。

    磁盘分两段：`files{路径: {size, mtime, hash}}` 与 `meta{hash: 解析结果}`；
    快路径用 (size, mtime) 直接取 hash，未命中才算 hash —— 同一份内容已解析过就直接复用。
    """
    global _sections_dirty

    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return read_dll_metadata(target)

    key = (str(target), stat.st_size, stat.st_mtime_ns)
    cached = _METADATA_CACHE.get(key)
    if cached is not None:
        return cached

    digest = cached_sha256(target)
    metadata: DllMetadata | None = _meta_for(str(target), digest) if digest else None
    if metadata is None:
        metadata = read_dll_metadata(target)
        if digest and _disk_cache_enabled():
            _sections["meta"][digest] = _serialize(metadata)
            _sections_dirty = True

    if len(_METADATA_CACHE) >= _METADATA_CACHE_LIMIT:
        _METADATA_CACHE.clear()
    _METADATA_CACHE[key] = metadata
    return metadata


def _meta_for(path: str, digest: str) -> DllMetadata | None:
    """按内容 hash 取解析结果（同一份内容换路径/改名都能复用）。"""
    data = _sections["meta"].get(digest)
    if not isinstance(data, dict):
        return None
    try:
        return _deserialize(path, data)
    except (TypeError, ValueError):
        return None


def clear_metadata_cache(*, include_disk: bool = False) -> None:
    """丢掉内存缓存；`include_disk=True` 时连磁盘缓存的记忆也一起丢（测试用）。"""
    global _sections, _sections_loaded, _sections_dirty
    _METADATA_CACHE.clear()
    if include_disk:
        _sections = {"files": {}, "meta": {}}
        _sections_loaded = False
        _sections_dirty = False


__all__ = [
    "DllMetadata",
    "MELON_KIND_MODS",
    "MELON_KIND_PLUGINS",
    "clear_metadata_cache",
    "configure_metadata_cache",
    "flush_metadata_cache",
    "melon_info_description",
    "pe_file_version",
    "read_cached_metadata",
    "read_dll_metadata",
]
