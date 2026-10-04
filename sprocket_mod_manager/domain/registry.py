from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any, Iterable

from .errors import RegistryError, ScanError
from .models import RegistryPackage
from .compatibility import DEFAULT_GAME_CAPABILITY, providers_table
from .diagnosis import diagnosis_pack
from ..utilities.package_paths import (
    file_type_is_wildcard,
    file_type_namespace,
    validate_file_type,
    validate_subpath,
    validate_supply_target,
    validate_supply_type,
)


class Registry:
    def __init__(
            self,
            packages: list[RegistryPackage],
            provider_table: dict[str, Any] | None = None,
            game_id: str = DEFAULT_GAME_CAPABILITY,
            game_name: str = "",
            diagnosis: dict[str, Any] | None = None,
            *,
            server_packages: Iterable[RegistryPackage] = (),
    ):
        self.game_id = str(game_id)
        self.game_name = str(game_name)
        self.provider_table = providers_table(provider_table)
        self.diagnosis = diagnosis_pack(diagnosis)
        # 索引带来的那些包。私有包是整批换的（见 `merged_with`），所以这份底子要留着。
        self._index_packages = tuple(packages)
        # 开发者服务器下发的包：二进制来自那台服务器而不是各自的 GitHub Releases。
        sourced = tuple(server_packages)
        combined = [*packages, *sourced]
        first_sourced = len(packages)

        # 逐包校验：一条坏数据只让它自己标成不可用 —— 目录照常列出它并说明原因，
        # 别的包、供给表与能力表都不受牵连。
        problems: list[list[str]] = [list(package.issues) for package in combined]
        clean_indexes: list[int] = []
        for index, package in enumerate(combined):
            problems[index].extend(
                self._source_problems(package, server_sourced=index >= first_sourced)
                + self._supply_problems(package)
            )
            if not problems[index]:
                clean_indexes.append(index)

        clean = [combined[index] for index in clean_indexes]
        self._suppliers = self._build_suppliers(clean)
        supplied_types = set(self._suppliers)
        for index in clean_indexes:
            for file_type in combined[index].declared_types():
                if not self._type_is_supplied(file_type, supplied_types):
                    problems[index].append(
                        f"install file type is not supplied by any modloader: {file_type}"
                    )

        self._capabilities = self._build_capabilities(
            [combined[index] for index in clean_indexes if not problems[index]]
        )
        seen_ids: set[str] = set()
        for index in clean_indexes:
            if problems[index]:
                continue
            package_id = combined[index].id
            if package_id in seen_ids:
                problems[index].append(f"duplicate package id: {package_id}")
                continue
            seen_ids.add(package_id)

        self.packages = tuple(
            replace(package, issues=tuple(dict.fromkeys(found))) if found else package
            for package, found in zip(combined, problems)
        )
        # 只有没有问题的包能被按 id 找到：坏条目在目录里看得见，但解析不到、装不了。
        # 依赖/推荐指向没注册的 id 不在这儿判 —— 那是求解器与界面的事（缺失依赖照常显示）。
        self._by_id = {
            package.id: package for package in self.packages if not package.issues
        }

    @staticmethod
    def _source_problems(package: RegistryPackage, *, server_sourced: bool = False) -> list[str]:
        """这个包的二进制来源是否自洽。

        外部来源没有可以查询的 API，所以版本与资产只能由注册表条目自己给出；主机白名单
        保证下载地址不会漂到任意站点。基础运行时之外的包只允许从它自己的 GitHub Releases 出货，
        唯一的例外是开发者服务器下发的包（`server_sourced`）：那台服务器的条目本来就不在 GitHub 上，
        二进制由它自己分发，客户端按指纹确认过身份、下载时再核对它声明的下行主机。
        """
        problems: list[str] = []
        source = package.source
        source_type = str(source.get("type", "github"))
        if package.is_modloader and not package.supply:
            problems.append("a modloader must supply at least one install type")
        if source_type == "github":
            if "hosts" in source:
                problems.append("release.source.hosts only applies to external sources")
            return problems
        if source_type != "external":
            problems.append(f"unknown release source type: {source_type}")
            return problems
        if not package.is_modloader and not server_sourced:
            problems.append("only a modloader may declare an external release source")
        if not package.asset_hosts():
            problems.append("external release source needs at least one allowed host")
        if not package.releases:
            problems.append("external release source needs an embedded releases list")
        return problems

    @staticmethod
    def _build_capabilities(packages: Iterable[RegistryPackage]) -> frozenset[str]:
        """索引里认得的能力 id：各包显式提供的能力，外加没写 `provides` 的加载器类包自己。

        模组自己的包 id 是**包**，不是能力轴 —— 模组之间靠包依赖相连。加载器类包没写
        `provides` 时隐含提供自己的包 id，能力轴与包 id 同名。
        """
        capabilities: set[str] = set()
        for package in packages:
            declared = package.declared_capabilities()
            if declared:
                capabilities.update(declared)
            elif package.is_loader:
                capabilities.add(package.id)
        return frozenset(capabilities)

    @staticmethod
    def _build_suppliers(
            packages: tuple[RegistryPackage, ...] | list[RegistryPackage],
    ) -> dict[str, tuple[RegistryPackage, ...]]:
        """类型 -> 供给它的加载器（可能不止一个）。

        一个类型可以有多个供给者：同一套模组既可能跑在原生加载器下，也可能跑在把目录重新
        安家的桥接加载器下，安装位置取决于实际装的是哪一个。只收下校验过的包，坏条目不会
        污染这张表。
        """
        suppliers: dict[str, list[RegistryPackage]] = {}

        def add(key: str, package: RegistryPackage) -> None:
            providers = suppliers.setdefault(key, [])
            if package not in providers:
                providers.append(package)

        for package in packages:
            for raw_type, _raw_target in package.supply.items():
                add(validate_supply_type(raw_type), package)
        return {key: tuple(value) for key, value in suppliers.items()}

    @staticmethod
    def _supply_problems(package: RegistryPackage) -> list[str]:
        """这个包声明的供给类型、供给位置与文件规则是否合法。"""
        problems: list[str] = []
        for raw_type, raw_target in package.supply.items():
            try:
                validate_supply_type(raw_type)
                validate_supply_target(raw_target)
            except ScanError as exc:
                problems.append(f"invalid supply entry {raw_type!r}: {exc}")
        for rule in package.file_rules:
            try:
                validate_file_type(str(rule.get("type", "")))
                if rule.get("subpath"):
                    validate_subpath(str(rule["subpath"]))
            except ScanError as exc:
                problems.append(f"invalid install file rule: {exc}")
        for rule in package.payload_rules:
            try:
                validate_supply_target(str(rule.get("target", "")))
                if rule.get("subpath"):
                    validate_subpath(str(rule["subpath"]))
            except ScanError as exc:
                problems.append(f"invalid install payload rule: {exc}")
        return problems

    @staticmethod
    def _type_is_supplied(file_type: str, supplied_types: Iterable[str]) -> bool:
        return any(
            file_type == supplied
            or (
                file_type_is_wildcard(file_type)
                and file_type_namespace(supplied) == file_type_namespace(file_type)
            )
            for supplied in supplied_types
        )

    def providers_for_type(self, file_type: str) -> tuple[RegistryPackage, ...]:
        """供给这个类型的所有加载器；`<ns>:*` 解析成该名字空间下所有类型的供给者。"""
        validated = validate_file_type(file_type)
        if file_type_is_wildcard(validated):
            namespace = file_type_namespace(validated)
            providers: dict[str, RegistryPackage] = {}
            for supplied, candidates in self._suppliers.items():
                if file_type_namespace(supplied) != namespace:
                    continue
                for candidate in candidates:
                    providers.setdefault(candidate.id, candidate)
            return tuple(providers.values())
        return self._suppliers.get(validated, ())

    def supplier_for_type(
            self,
            file_type: str,
            installed: Iterable[str] = (),
    ) -> RegistryPackage | None:
        """确定这个类型该由哪个加载器供给；无法唯一确定时返回 None。

        只有一个供给者就是它。有多个供给者时只看已安装的那一个：装上的加载器决定了目录，
        不能靠猜。一个都没装（或装了不止一个）就没有唯一答案，由调用方解释给用户。
        """
        candidates = self.providers_for_type(file_type)
        if len(candidates) == 1:
            return candidates[0]
        installed_ids = set(installed)
        matches = [package for package in candidates if package.id in installed_ids]
        if len(matches) == 1:
            return matches[0]
        return None

    def install_directories(self, installed: Iterable[str] = ()) -> dict[str, PurePosixPath]:
        """类型 -> 游戏根目录下的相对目录（`{Sprocket}` 前缀已去掉）。

        只列出能唯一确定供给者的类型；有多个候选且无法从已安装集合里定下来的类型不在这里，
        调用方必须自己先报错，而不是拿一个猜出来的目录去安装。
        """
        directories: dict[str, PurePosixPath] = {}
        for file_type in self._suppliers:
            provider = self.supplier_for_type(file_type, installed)
            if provider is None:
                continue
            directories[file_type] = validate_supply_target(provider.supply[file_type])
        return directories

    def modloaders(self) -> tuple[RegistryPackage, ...]:
        return tuple(package for package in self.packages if package.is_modloader)

    def knows_capability(self, capability_id: str) -> bool:
        return capability_id == self.game_id or capability_id in self._capabilities

    def has_package(self, package_id: str) -> bool:
        """这个 id 是不是一个真实包（能力不是包：装不了，也不进依赖图）。"""
        return package_id in self._by_id

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Registry":
        if data.get("schema_version") != 1:
            raise RegistryError("unsupported registry schema_version")
        raw_packages = data.get("packages")
        if not isinstance(raw_packages, list):
            raise RegistryError("registry packages must be a list")
        raw_game = data.get("game")
        game_id = DEFAULT_GAME_CAPABILITY
        game_name = ""
        if isinstance(raw_game, dict) and raw_game.get("id"):
            game_id = str(raw_game["id"])
            game_name = str(raw_game.get("name", ""))
        packages: list[RegistryPackage] = []
        for index, raw in enumerate(raw_packages):
            if not isinstance(raw, dict):
                continue
            try:
                packages.append(RegistryPackage.from_dict(raw))
            except (KeyError, TypeError, ValueError) as exc:
                # 读不出来的条目仍然进目录：留一份能显示的最小读数，原因挂在 issues 上。
                packages.append(
                    RegistryPackage.from_invalid(
                        raw,
                        f"invalid registry package: {exc}",
                        fallback_id=f"invalid.package-{index}",
                    )
                )
        return cls(
            packages, data.get("providers"), game_id, game_name, diagnosis=data.get("diagnosis")
        )

    @property
    def index_packages(self) -> tuple[RegistryPackage, ...]:
        """索引带来的那些包；开发者服务器下发的那些不在其中。

        只列一个来源的读数要这一份：私有包有服务器那份读数（`KEY_SERVERS`），跟着 `packages`
        一起列出来，同一个模组就会在目录里出现两次。
        """
        return self.packages[: len(self._index_packages)]

    def merged_with(self, packages: Iterable[RegistryPackage]) -> Registry:
        """换成这批**开发者服务器下发的**包：结果里索引那些包照旧，只有这批是新的。

        那批包不在索引里，界面却把它们和索引一起列出来；解析、安装、卸载因此也要能按 id 找到
        它们，否则私有包永远停在「看得见、装不了」。每次都给全量 —— 一台服务器掉线，它上一轮
        那些包就该跟着消失。供给表与诊断规则包是索引的事实，原样带过去。
        """
        merged = Registry(
            list(self._index_packages),
            game_id=self.game_id,
            game_name=self.game_name,
            server_packages=tuple(packages),
        )
        merged.provider_table = self.provider_table
        merged.diagnosis = self.diagnosis
        return merged

    def get(self, package_id: str) -> RegistryPackage:
        try:
            return self._by_id[package_id]
        except KeyError as exc:
            raise RegistryError(f"package is not registered: {package_id}") from exc

    def resolve_identifier(self, value: str) -> RegistryPackage:
        direct = self._by_id.get(value)
        if direct:
            return direct
        folded = value.casefold()
        matches = [
            package
            for package in self.packages
            if package.name.casefold() == folded or package.id.casefold() == folded
        ]
        if len(matches) == 1:
            return matches[0]
        raise RegistryError(f"package is not registered: {value}")
