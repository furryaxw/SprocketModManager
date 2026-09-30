"""诊断规则包：规范化在线下发的规则，并把本机事实与日志行求值成报告条目。

规则包和 `providers` 那张表同一条链路来（仓库源文件 -> 索引 -> 注册表 -> 本地缓存），
所以每个字段都要扛得住畸形输入：整包坏掉降级成空包，单条坏掉只丢那一条。

两类规则，`check` 与 `match` 互斥：

* **环境检查**（`check`）：代码实现的判定，读 `facts`，规则包只选跑哪些、算几级。
* **日志签名**（`match`）：规则包给正则，按来源角色匹配日志行。

来源按**角色**寻址（`manager_log` / `loader_log` / `unity_log`），不认加载器 id ——
换一个加载器不用改规则包。

`evaluate()` 只读入参，不碰 I/O 也不落盘；它可能提前收工：`deadline` 到了就把没跑完的
规则记成 `timeout`。规则包的正则来自远程，而标准库的 `re` 没有超时，所以卡在单条正则里
是这份实现挡不住的残留风险，`deadline` 只能在行与规则之间收手。
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterable, Mapping

BUCKET_REQUIRED = "required"
BUCKET_OPTIONAL = "optional"
BUCKETS = (BUCKET_REQUIRED, BUCKET_OPTIONAL)

# `level` 只在桶内排顺序，1 最靠前。「必须解决」的门槛由 `bucket` 表达：游戏起不来。
MIN_LEVEL = 1
MAX_LEVEL = 5
DEFAULT_LEVEL = 3

# 代码里实现了的检查名。规则包只能引用这里有的名字。
CHECKS = (
    "environment_conflict",
    "loader_files_broken",
    "mod_files_broken",
    "loader_missing",
)

# 日志来源的角色，不是加载器 id。
LOG_ROLES = ("manager_log", "loader_log", "unity_log")

# 应用内跳转允许的页面。包里的目标只能是这些，跳转本身不写任何东西。
GO_TO_PAGES = (
    "installed",
    "catalog",
    "modloaders",
    "translations",
    "downloads",
    "settings",
    "about",
    "diagnosis",
)

# 未判定的原因。界面按这个查文案 —— 「事实不够」不许说成「没问题」。
UNJUDGED_GAME_NOT_CONFIGURED = "game_not_configured"
UNJUDGED_LOG_MISSING = "log_missing"
UNJUDGED_PACK_MISSING = "pack_missing"
UNJUDGED_TIMEOUT = "timeout"


def _text_map(value: Any) -> dict[str, str]:
    """`{语言: 文案}`：只留非空字符串，语言键统一小写。文案里的 `{参数}` 由界面填。"""
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip().casefold(): item.strip()
        for key, item in value.items()
        if isinstance(item, str) and item.strip()
    }


def _text_steps(value: Any) -> dict[str, list[str]]:
    """`{语言: [步骤, ...]}`：步骤有序，空步骤丢掉。"""
    if not isinstance(value, dict):
        return {}
    steps: dict[str, list[str]] = {}
    for key, items in value.items():
        language = str(key).strip().casefold()
        if not language or not isinstance(items, list):
            continue
        ordered = [str(item).strip() for item in items if isinstance(item, str) and item.strip()]
        if ordered:
            steps[language] = ordered
    return steps


def _go_to(value: Any) -> dict[str, str]:
    """应用内跳转目标：页面必须在闭集里，`package` 可选。"""
    if not isinstance(value, dict):
        return {}
    page = str(value.get("page") or "").strip()
    if page not in GO_TO_PAGES:
        return {}
    target = {"page": page}
    package = str(value.get("package") or "").strip()
    if package:
        target["package"] = package
    return target


def _match(value: Any) -> dict[str, Any] | None:
    """日志签名：来源角色、正则、最少命中行数。

    正则只在这里**试编一次**丢弃结果：规范化后的包要能写进 JSON 缓存，所以存的是字符串。
    编不过的签名整条丢掉，而不是留到求值时再炸。
    """
    if not isinstance(value, dict):
        return None
    pattern = str(value.get("pattern") or "")
    if not pattern:
        return None
    try:
        re.compile(pattern)
    except re.error:
        return None
    raw_sources = value.get("sources")
    if not isinstance(raw_sources, list):
        return None
    sources = [role for role in (str(item).strip() for item in raw_sources) if role in LOG_ROLES]
    if not sources:
        return None
    minimum = value.get("min_count")
    minimum = minimum if isinstance(minimum, int) and minimum >= 1 else 1
    return {"sources": sources, "pattern": pattern, "min_count": minimum}


def _level(value: Any) -> int:
    return value if isinstance(value, int) and MIN_LEVEL <= value <= MAX_LEVEL else DEFAULT_LEVEL


def _entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    rule_id = str(value.get("id") or "").strip()
    bucket = str(value.get("bucket") or "").strip()
    title = _text_map(value.get("title"))
    if not rule_id or bucket not in BUCKETS or not title:
        return None
    check = str(value.get("check") or "").strip() or None
    match = _match(value.get("match"))
    # 两类互斥：同时给了两份判定，谁说了算没有答案，整条丢掉。
    if (check is None) == (match is None):
        return None
    if check is not None and check not in CHECKS:
        return None
    return {
        "id": rule_id,
        "bucket": bucket,
        "level": _level(value.get("level")),
        "title": title,
        "explain": _text_map(value.get("explain")),
        "evidence": _text_map(value.get("evidence")),
        "tutorial": _text_steps(value.get("tutorial")),
        "go_to": _go_to(value.get("go_to")),
        "check": check,
        "match": match,
    }


def diagnosis_pack(payload: Any) -> dict[str, Any]:
    """把任意 payload 归一成一份可用的规则包；坏条目只丢自己，没有可用条目就是空包。"""
    empty: dict[str, Any] = {"schema_version": 1, "pack_version": 0, "entries": []}
    if not isinstance(payload, dict):
        return empty
    raw = payload.get("entries")
    if not isinstance(raw, list):
        return empty
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        entry = _entry(item)
        if entry is None or entry["id"] in seen:
            continue
        seen.add(entry["id"])
        entries.append(entry)
    if not entries:
        return empty
    schema_version = payload.get("schema_version")
    pack_version = payload.get("pack_version")
    return {
        "schema_version": schema_version if isinstance(schema_version, int) else 1,
        "pack_version": pack_version if isinstance(pack_version, int) else 0,
        "entries": entries,
    }


def _finding(
        entry: Mapping[str, Any],
        *,
        params: Mapping[str, str],
        log_line: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """一条命中：规则的文案与教程原样带上，参数留给界面填。

    `go_to.package` 优先取规则包里写死的那个，其次是这条命中自己算出来的 `package` 参数：
    环境类检查点名的加载器只有求值的时候才知道是谁。
    """
    target = dict(entry["go_to"])
    package = str(params.get("package") or "")
    if target and package:
        target.setdefault("package", package)
    return {
        "rule": entry["id"],
        "bucket": entry["bucket"],
        "level": entry["level"],
        "title": dict(entry["title"]),
        "explain": dict(entry["explain"]),
        "evidence": dict(entry["evidence"]),
        "tutorial": {key: list(steps) for key, steps in entry["tutorial"].items()},
        "params": {key: str(value) for key, value in params.items()},
        "log_line": dict(log_line) if log_line else None,
        "go_to": target,
    }


def _check_environment_conflict(facts: Mapping[str, Any]) -> tuple[dict[str, str], None] | None:
    """点名那个加载器：`loader` 是给人看的名字，`package` 是跳转用的包 id。

    两者都由采集层从注册表补齐 —— 判定层手上没有注册表，也不该有。
    """
    conflict = facts.get("environment_conflict")
    if not isinstance(conflict, dict) or not conflict.get("conflict"):
        return None
    package = str(conflict.get("loader") or "")
    return (
        {
            "package": package,
            "loader": str(conflict.get("loader_name") or "") or package,
            "sprocket": str(facts.get("game_version") or ""),
        },
        None,
    )


def _broken_files(facts: Mapping[str, Any], *, loaders: bool) -> tuple[dict[str, str], None] | None:
    broken = [
        item
        for item in facts.get("packages_broken") or ()
        if isinstance(item, dict) and bool(item.get("is_loader")) == loaders
    ]
    if not broken:
        return None
    first = broken[0]
    package = str(first.get("id") or "")
    return (
        {
            "package": package,
            "name": str(first.get("name") or "") or package,
            "count": str(len(broken)),
        },
        None,
    )


def _check_loader_files_broken(facts: Mapping[str, Any]) -> tuple[dict[str, str], None] | None:
    return _broken_files(facts, loaders=True)


def _check_mod_files_broken(facts: Mapping[str, Any]) -> tuple[dict[str, str], None] | None:
    return _broken_files(facts, loaders=False)


def _check_loader_missing(facts: Mapping[str, Any]) -> tuple[dict[str, str], None] | None:
    if not facts.get("game_configured") or not facts.get("loaders_available"):
        return None
    if facts.get("loaders_installed"):
        return None
    return {"sprocket": str(facts.get("game_version") or "")}, None


CHECK_FUNCTIONS: dict[str, Callable[[Mapping[str, Any]], tuple[dict[str, str], None] | None]] = {
    "environment_conflict": _check_environment_conflict,
    "loader_files_broken": _check_loader_files_broken,
    "mod_files_broken": _check_mod_files_broken,
    "loader_missing": _check_loader_missing,
}


def _state_finding(entry: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any] | None:
    function = CHECK_FUNCTIONS.get(str(entry["check"]))
    if function is None:
        return None
    hit = function(facts)
    if hit is None:
        return None
    params, _evidence = hit
    return _finding(entry, params=params, log_line=None)


def _log_finding(entry: Mapping[str, Any], lines: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """按签名扫一遍日志行：命中数够就出一条，证据取第一条命中的原文与行号。"""
    match = entry["match"]
    try:
        pattern = re.compile(match["pattern"])
    except re.error:
        return None
    sources = set(match["sources"])
    first: dict[str, Any] | None = None
    count = 0
    for line in lines:
        if str(line.get("role") or "") not in sources:
            continue
        text = str(line.get("text") or "")
        found = pattern.search(text)
        if found is None:
            continue
        count += 1
        if first is None:
            first = {
                "source": str(line.get("source") or ""),
                "number": int(line.get("number") or 0),
                "text": text,
                "groups": [group or "" for group in found.groups()],
            }
    if first is None or count < match["min_count"]:
        return None
    params = {"count": str(count)}
    for index, group in enumerate(first["groups"], start=1):
        params[f"group{index}"] = group
    return _finding(
        entry,
        params=params,
        log_line={
            "source": first["source"],
            "number": first["number"],
            "text": first["text"],
        },
    )


def evaluate(
        facts: Mapping[str, Any] | None,
        lines: Iterable[Mapping[str, Any]] | None,
        pack: Mapping[str, Any] | None,
        *,
        available_roles: Iterable[str] | None = None,
        deadline: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """规则包 -> （命中条目，未判定条目）。

    传进来的包先过一遍 `diagnosis_pack()`：入口只此一个，未规范化的包在这里也不会把
    求值炸掉。命中条目按「必须解决在前、桶内 level 升序」排好。未判定的三种原因：游戏目录没配、
    这条要的日志没有、时间预算到了 —— 「事实不够」一律不说成「没问题」。
    """
    facts = facts or {}
    pack = diagnosis_pack(pack)
    roles = {str(role) for role in (available_roles or ())}
    game_ready = bool(facts.get("game_configured"))
    collected = list(lines or ())
    findings: list[dict[str, Any]] = []
    unjudged: list[dict[str, Any]] = []

    for entry in pack.get("entries") or ():
        if deadline is not None and time.monotonic() > deadline:
            unjudged.append({"rule": entry["id"], "reason": UNJUDGED_TIMEOUT})
            continue
        if entry.get("check"):
            if not game_ready:
                unjudged.append({"rule": entry["id"], "reason": UNJUDGED_GAME_NOT_CONFIGURED})
                continue
            hit = _state_finding(entry, facts)
        else:
            if not set(entry["match"]["sources"]) & roles:
                unjudged.append({"rule": entry["id"], "reason": UNJUDGED_LOG_MISSING})
                continue
            hit = _log_finding(entry, collected)
        if hit is not None:
            findings.append(hit)

    findings.sort(key=lambda item: (0 if item["bucket"] == BUCKET_REQUIRED else 1, item["level"], item["rule"]))
    return findings, unjudged


def split_buckets(findings: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按「必须解决 / 非必要」分成两栏；顺序沿用 `evaluate()` 排好的那份。"""
    buckets: dict[str, list[dict[str, Any]]] = {BUCKET_REQUIRED: [], BUCKET_OPTIONAL: []}
    for finding in findings:
        buckets.setdefault(str(finding.get("bucket") or BUCKET_OPTIONAL), []).append(dict(finding))
    return buckets
