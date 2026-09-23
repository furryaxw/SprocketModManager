"""环境兼容性：本机 Sprocket / MelonLoader 决定每个 release 是三色里的哪一种。

索引里每条 release 的 `dependencies` 已经写明它对两个环境虚拟包的区间
（`environment.sprocket` / `environment.melonloader`），这里只做区间匹配，不参与依赖求解：
装哪个版本由调用方定，装下去之后是否真的能跑由玩家自己决定。

索引顶层还带一张「加载器 ↔ 游戏」表（注册表 `site/environment.json` 规范化而来）：表里说
这段加载器只支持某个游戏版本之前，而本机更晚，那这个环境自身就是矛盾的 —— 那不是任何一个
release 的错，所以判定顶点为黄而不是红。这张表只从注册表来，客户端同步到一次就缓存住，
本地不放内置副本。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

SPROCKET_PACKAGE = "environment.sprocket"
MELONLOADER_PACKAGE = "environment.melonloader"
ENVIRONMENT_PACKAGES = (SPROCKET_PACKAGE, MELONLOADER_PACKAGE)

COMPATIBLE = "compatible"
UNKNOWN = "unknown"
INCOMPATIBLE = "incompatible"
# 不判这个包自己的环境声明（翻译包）：界面上不标色、不隐藏，但它依赖的包照常判。
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


def environment_table(payload: Any) -> dict[str, Any]:
    """索引顶层的「加载器 ↔ 游戏」表；形状不对或没有就是空表（没有本地内置表）。

    空表的意思是「这层不知道」：不参与过滤、也不报冲突 —— 表只从注册表来，同步过一次就缓存着。
    """
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list):
            cleaned = [
                {"melonloader": str(entry["melonloader"]), "sprocket": str(entry["sprocket"])}
                for entry in entries
                if isinstance(entry, dict) and "melonloader" in entry and "sprocket" in entry
            ]
            return {"schema_version": 1, "entries": cleaned}
    return {"schema_version": 1, "entries": []}


def release_verdict(
        environment: Environment,
        *,
        category: str,
        dependencies: Iterable[dict[str, Any]] | None,
) -> str:
    """一个 release 的三色判定（判定口径唯一的一处）。

    翻译包（`translation`）不看自己的环境声明：正文是文本，声明往往是抄来的、也常常过时；
    它能不能用取决于它依赖的那些包 —— 那些包各自按自己的声明判，所以这里返回「不适用」。
    """
    if category == TRANSLATION_CATEGORY:
        return NOT_APPLICABLE
    return environment.verdict(dependencies)


@dataclass(frozen=True)
class Environment:
    """判定用的环境：两个轴的取值、游戏版本状态，以及加载器 ↔ 游戏表。"""

    sprocket: str | None = None
    sprocket_state: str = "unconfigured"
    melonloader: str | None = None
    table: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "table", environment_table(self.table))

    def axis_version(self, package_id: str) -> str | None:
        if package_id == SPROCKET_PACKAGE:
            return self.sprocket if self.sprocket_state == "ok" else None
        if package_id == MELONLOADER_PACKAGE:
            return self.melonloader
        return None

    @property
    def unusable_game(self) -> bool:
        return self.sprocket_state in UNUSABLE_GAME_STATES

    def verdict(self, dependencies: Iterable[dict[str, Any]] | None) -> str:
        """一个 release 的三色判定。

        任一环境轴不通过＝不兼容；没有可评估的轴＝未知；**环境自身矛盾**（加载器还跟不上
        这个游戏）时顶点为未知 —— 那不是这个 release 的错，但也不能说它兼容。
        """
        if self.unusable_game:
            return INCOMPATIBLE
        participated = False
        for dependency in dependencies or ():
            if not isinstance(dependency, dict):
                continue
            version = self.axis_version(str(dependency.get("id", "")))
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

    def loader_row(self) -> dict[str, Any] | None:
        """命中当前加载器版本的那一行。

        一个加载器版本只对应**一行**：加载器区间下界更大者优先（新的一行覆盖旧的一行），
        下界相同则表里靠后的覆盖靠前的，没有下界的兜底行排最后。这样加一行「新加载器支持
        更新的游戏」不会把老那一行弄坏 —— 老加载器仍然只命中它自己那行。
        """
        if not self.melonloader:
            return None
        matches = [
            (index, entry)
            for index, entry in enumerate(self.table.get("entries", ()))
            if isinstance(entry, dict)
            and range_allows(self.melonloader, entry.get("melonloader"))
        ]
        if not matches:
            return None

        def priority(item: tuple[int, dict[str, Any]]) -> tuple[int, tuple[int, ...], int]:
            index, entry = item
            bound = range_lower_bound(entry.get("melonloader"))
            return (0 if bound is None else 1, bound or (), index)

        return max(matches, key=priority)[1]

    def consistency(self) -> dict[str, Any]:
        """环境自身是否自洽：表里说这段加载器只支持到某个游戏版本之前，而本机更晚。"""
        if self.sprocket is None or self.sprocket_state != "ok" or not self.melonloader:
            return {"state": UNKNOWN, "entry": None}
        row = self.loader_row()
        if row is None:
            return {"state": UNKNOWN, "entry": None}
        if not range_allows(self.sprocket, row.get("sprocket")):
            return {"state": "conflict", "entry": dict(row)}
        return {"state": "ok", "entry": None}

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
        result: list[dict[str, Any]] = []
        for package_id in ENVIRONMENT_PACKAGES:
            local = self.axis_version(package_id)
            range_spec = declared.get(package_id, "")
            result.append(
                {
                    "id": package_id,
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
        return f"Sprocket {self.sprocket or '-'} / MelonLoader {self.melonloader or '-'}"

    def as_dict(self, *, table_source: str) -> dict[str, Any]:
        state = self.consistency()
        return {
            "state": state["state"],
            "entry": state["entry"],
            "table_source": table_source,
            "sprocket": self.sprocket,
            "melonloader": self.melonloader,
        }
