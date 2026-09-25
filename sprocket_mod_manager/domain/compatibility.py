"""能力兼容性：本机给每个能力提供的版本决定每条 release 是三色里的哪一种。

索引里每条 release 的 `dependencies` 已经写明它对能力的区间。能力的版本有两个来源：
本机的游戏版本（游戏能力，索引顶层 `game.id` 给的 id）和已装加载器包通过 `provides`
声明出来的能力。这里只做区间匹配，不参与依赖求解：装哪个版本由调用方定，装下去之后
是否真的能跑由玩家自己决定。

索引顶层还带一张「加载器包 ↔ 游戏」表（注册表 `providers.json` 规范化而来）：表里说某个
加载器包的某段版本只支持某个游戏版本之前，而本机更晚，那这个组合自身就是矛盾的 —— 那不是
任何一条 release 的错，所以判定顶点为黄而不是红。这张表只从注册表来，客户端同步到一次就
缓存住，本地不放内置副本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .semver import satisfies

# 索引没给出游戏能力 id 时的兜底；正常情况下它由索引顶层 `game.id` 提供。
DEFAULT_GAME_CAPABILITY = "hamish.sprocket"

COMPATIBLE = "compatible"
UNKNOWN = "unknown"
INCOMPATIBLE = "incompatible"
# 不判这个包自己的能力声明（翻译包）：界面上不标色、不隐藏，但它依赖的包照常判。
NOT_APPLICABLE = "not_applicable"

TRANSLATION_CATEGORY = "translation"

# 游戏版本读出来是这几种时，没有任何声明判定得了：按「都不兼容」处理。
UNUSABLE_GAME_STATES = frozenset({"legacy", "unreadable"})


def version_parts(text: Any) -> tuple[int, ...] | None:
    parts = str(text or "").strip().split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _order(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def _token_allows(candidate: tuple[int, ...], token: str) -> bool:
    token = token.strip()
    if not token or token in {"*", "x", "X"}:
        return True

    segments = token.split(".")
    if any(part in {"x", "X", "*"} for part in segments):
        head: list[int] = []
        for part in segments:
            if part in {"x", "X", "*"}:
                break
            if not part.isdigit():
                return False
            head.append(int(part))
        return candidate[: len(head)] == tuple(head)

    operator = "="
    for prefix in (">=", "<=", ">", "<", "="):
        if token.startswith(prefix):
            operator = prefix
            token = token[len(prefix) :].strip()
            break
    target = version_parts(token)
    if target is None:
        return False

    order = _order(candidate, target)
    if operator == ">=":
        return order >= 0
    if operator == "<=":
        return order <= 0
    if operator == ">":
        return order > 0
    if operator == "<":
        return order < 0
    return order == 0


def range_allows(version: Any, range_spec: Any) -> bool:
    """版本是否落在区间里。区间写法就是索引里的规范形式（`>=a <=b`，多段用 `||`）。"""
    candidate = version_parts(version)
    if candidate is None:
        return False
    expression = str(range_spec or "*").strip()
    if not expression or expression == "*":
        return True
    for branch in expression.split("||"):
        tokens = [token for token in branch.replace(",", " ").split() if token]
        if tokens and all(_token_allows(candidate, token) for token in tokens):
            return True
    return False


def range_lower_bound(range_spec: Any) -> tuple[int, ...] | None:
    """区间里最大的下界（`>=`/`>`）；没有下界返回 None。"""
    best: tuple[int, ...] | None = None
    expression = str(range_spec or "*").strip()
    for branch in expression.split("||"):
        for token in branch.replace(",", " ").split():
            for prefix in (">=", ">"):
                if not token.startswith(prefix):
                    continue
                bound = version_parts(token[len(prefix) :].strip())
                if bound is not None and (best is None or _order(bound, best) > 0):
                    best = bound
                break
    return best


def loader_range_allows(version: Any, range_spec: Any) -> bool:
    """加载器包版本是否落在区间里。

    加载器版本是标准 SemVer，可能带预发布段（`6.0.0-be.788`），所以这里用 semver 判；
    读不出来的旧式写法退回按段比较。
    """
    try:
        return satisfies(str(version), str(range_spec or "*"))
    except ValueError:
        return range_allows(version, range_spec)


def providers_table(payload: Any) -> dict[str, Any]:
    """索引顶层的「加载器包 ↔ 游戏」表；形状不对或没有就是空表（没有本地内置表）。

    空表的意思是「这层不知道」：不参与过滤、也不报冲突 —— 表只从注册表来，同步过一次就缓存着。
    """
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list):
            cleaned = [
                {
                    "loader": str(entry["loader"]),
                    "version": str(entry["version"]),
                    "sprocket": str(entry["sprocket"]),
                }
                for entry in entries
                if isinstance(entry, dict)
                and "loader" in entry
                and "version" in entry
                and "sprocket" in entry
            ]
            return {"schema_version": 2, "entries": cleaned}
    return {"schema_version": 2, "entries": []}


def loader_table_decision(
        table: Mapping[str, Any],
        package_id: str,
        game_capability: str,
        game_version: str | None,
        release_version: str,
) -> tuple[str, tuple[dict[str, str], ...]] | None:
    """按「加载器包 ↔ 游戏」表判一个加载器发布：返回（判定，该用的兼容声明）。

    表里没有这个包的行、或者本机游戏版本读不出来时返回 None —— 这层不知道答案，调用方按 release
    自己的声明判。表里有行、但没有一行覆盖本机游戏版本，说明这条游戏线不用这个加载器：判不兼容，
    声明为空。命中一行时声明就是那条游戏轴，release 的版本落在那一行的 `version` 之外同样不兼容。
    """
    if not game_version:
        return None
    entries = [
        entry
        for entry in table.get("entries") or ()
        if isinstance(entry, dict) and str(entry.get("loader", "")) == package_id
    ]
    if not entries:
        return None
    for entry in entries:
        if not range_allows(game_version, entry.get("sprocket")):
            continue
        if not loader_range_allows(release_version, entry.get("version")):
            return INCOMPATIBLE, ()
        return COMPATIBLE, (
            {"id": game_capability, "version": str(entry.get("sprocket")), "when": "*"},
        )
    return INCOMPATIBLE, ()


def release_verdict(
        environment: "CapabilityEnvironment",
        *,
        category: str,
        dependencies: Iterable[dict[str, Any]] | None,
) -> str:
    """一条 release 的三色判定（判定口径唯一的一处）。

    翻译包（`translation`）不看自己的能力声明：正文是文本，声明往往是抄来的、也常常过时；
    它能不能用取决于它依赖的那些包 —— 那些包各自按自己的声明判，所以这里返回「不适用」。
    """
    if category == TRANSLATION_CATEGORY:
        return NOT_APPLICABLE
    return environment.verdict(dependencies)


@dataclass(frozen=True)
class CapabilityEnvironment:
    """判定用的能力表：每个能力本机的版本，外加加载器包 ↔ 游戏的表。

    `capabilities` 是能力 id -> 版本：游戏那项由 `game_id`/`sprocket` 给，其余来自已装
    加载器包的 `provides`（`{version}` 按实际发布版本替换）。`loaders` 是加载器**包** id ->
    在用的版本，只用来对上 `providers.json` 那张表 —— 表和能力是两个层次，包版本与它提供的
    能力版本可以不同（桥接包给 `lavagang.melonloader` 一个版本，自己的包版本是另一个）。
    """

    game_id: str = DEFAULT_GAME_CAPABILITY
    sprocket: str | None = None
    sprocket_state: str = "unconfigured"
    capabilities: Mapping[str, str] = field(default_factory=dict)
    loaders: Mapping[str, str] = field(default_factory=dict)
    table: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "table", providers_table(self.table))
        object.__setattr__(self, "loaders", dict(self.loaders or {}))
        object.__setattr__(self, "capabilities", dict(self.capabilities or {}))

    def capability_version(self, capability_id: str) -> str | None:
        if capability_id == self.game_id:
            return self.sprocket if self.sprocket_state == "ok" else None
        return self.capabilities.get(capability_id)

    @property
    def unusable_game(self) -> bool:
        return self.sprocket_state in UNUSABLE_GAME_STATES

    def verdict(self, dependencies: Iterable[dict[str, Any]] | None) -> str:
        """一条 release 的三色判定。

        任一能力轴不通过＝不兼容；没有可评估的轴＝未知；**这个组合自身矛盾**（加载器还跟不上
        这个游戏）时顶点为未知 —— 那不是这条 release 的错，但也不能说它兼容。
        """
        if self.unusable_game:
            return INCOMPATIBLE
        participated = False
        for dependency in dependencies or ():
            if not isinstance(dependency, dict):
                continue
            version = self.capability_version(str(dependency.get("id", "")))
            if version is None:
                continue
            participated = True
            if not range_allows(version, dependency.get("version")):
                return INCOMPATIBLE
        if not participated:
            return UNKNOWN
        if self.consistency()["state"] == "conflict":
            return UNKNOWN
        return COMPATIBLE

    def loader_row(self, loader_id: str) -> dict[str, Any] | None:
        """命中这个加载器包当前版本的那一行。

        一个包版本只对应**一行**：区间下界更大者优先（新的一行覆盖旧的一行），下界相同则
        表里靠后的覆盖靠前的，没有下界的兜底行排最后。这样加一行「新加载器支持更新的游戏」
        不会把老那一行弄坏 —— 老加载器仍然只命中它自己那行。
        """
        version = self.loaders.get(loader_id)
        if not version:
            return None
        matches = [
            (index, entry)
            for index, entry in enumerate(self.table.get("entries", ()))
            if isinstance(entry, dict)
            and str(entry.get("loader", "")) == loader_id
            and loader_range_allows(version, entry.get("version"))
        ]
        if not matches:
            return None

        def priority(item: tuple[int, dict[str, Any]]) -> tuple[int, tuple[int, ...], int]:
            index, entry = item
            bound = range_lower_bound(entry.get("version"))
            return (0 if bound is None else 1, bound or (), index)

        return max(matches, key=priority)[1]

    def consistency(self) -> dict[str, Any]:
        """这个组合是否自洽：表里说这段加载器只支持到某个游戏版本之前，而本机更晚。"""
        if self.sprocket is None or self.sprocket_state != "ok" or not self.loaders:
            return {"state": UNKNOWN, "entry": None, "loader": ""}
        for loader_id in sorted(self.loaders):
            row = self.loader_row(loader_id)
            if row is None:
                continue
            if not range_allows(self.sprocket, row.get("sprocket")):
                return {"state": "conflict", "entry": dict(row), "loader": loader_id}
        return {"state": "ok", "entry": None, "loader": ""}

    def axes(self, dependencies: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """逐轴结果：声明了什么、本机是什么、这一轴过没过。

        随索引一起给界面，界面只负责显示 —— 判定口径只有 `verdict()` 这一处，
        前端不再自己解析一次区间（两份实现迟早会说不一致的话）。
        `satisfied=None` 表示这一轴不参与判定（没声明，或者本机值未知）。
        """
        declared = {
            str(item.get("id")): str(item.get("version"))
            for item in dependencies or ()
            if isinstance(item, dict) and item.get("id")
        }
        others = sorted(
            (set(declared) | set(self.capabilities)) - {self.game_id}
        )
        capability_ids = [self.game_id, *others]
        result: list[dict[str, Any]] = []
        for capability_id in capability_ids:
            local = self.capability_version(capability_id)
            range_spec = declared.get(capability_id, "")
            result.append(
                {
                    "id": capability_id,
                    "declared": range_spec,
                    "local": local or "",
                    "satisfied": (
                        range_allows(local, range_spec) if range_spec and local else None
                    ),
                }
            )
        return result

    def label(self) -> str:
        """给人看的一行环境描述：求解器说清「为什么这些版本装不了」时用它。"""
        parts = [f"Sprocket {self.sprocket or '-'}"]
        parts.extend(
            f"{capability_id} {self.capabilities[capability_id] or '-'}"
            for capability_id in sorted(self.capabilities)
            if capability_id != self.game_id
        )
        return " / ".join(parts)

    def as_dict(self, *, table_source: str) -> dict[str, Any]:
        state = self.consistency()
        return {
            "state": state["state"],
            "entry": state["entry"],
            "loader": state["loader"],
            "table_source": table_source,
            "sprocket": self.sprocket,
            "loaders": dict(self.loaders),
        }
