"""诊断规则包：规范化在线下发的规则，并把本机事实与日志行求值成报告条目。

规则包和 `providers` 那张表同一条链路来（仓库源文件 -> 索引 -> 注册表 -> 本地缓存），
所以每个字段都要扛得住畸形输入：整包坏掉降级成空包，单条坏掉只丢那一条。

两类规则，`check` 与 `match` 互斥：

* **环境检查**（`check`）：代码实现的判定，读 `facts`，规则包只选跑哪些、算几级。
* **日志签名**（`match`）：规则包给正则，按来源角色匹配日志行。

来源按**角色**寻址（`manager_log` / `loader_log` / `unity_log`），不认加载器 id ——
换一个加载器不用改规则包。

日志签名按行喂进 `LogSignature`：读取与匹配交织，内存只留命中计数与首条命中，所以扫到哪就
能出到哪。规则包的正则来自远程、标准库的 `re` 又没有超时，所以一段文本按 `BLOCK_CHARS` 切块
滑过、块间留 `BLOCK_OVERLAP` 重叠：单次匹配的规模有界，跨度不超过重叠的命中不会被块边界切断。
单条规则另有 `RULE_BUDGET_SECONDS` 闸门，吃满就只放弃那一条并记 `timeout`。
**单次 `search` 内部中断不了** —— 这是这里挡不住的残留风险，所以窗口宽度本身就是一个安全参数。

这几个原语只读入参，不碰 I/O 也不落盘：`state_findings()` 算环境检查那几条，`log_signatures()`
造出每条签名的匹配状态、由调用方读到一行就喂一行，`log_hits()` 与 `log_unjudged()` 取结论。
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
# 规则/环境这一侧自己读不出来：不是「没判过」，是判不了。
UNJUDGED_CHECKS_FAILED = "checks_failed"
# 某一份日志读不下去（打不开、读到一半坏了）。
UNJUDGED_LOG_UNREADABLE = "log_unreadable"

# 行内滑窗：单块匹配的规模有界。块间重叠用来保住跨块的命中，所以它必须是「一条命中最多能
# 有多长」的上界 —— 规则包里出现更长的跨度时，那条命中会被块边界切断。
BLOCK_CHARS = 8 * 1024
BLOCK_OVERLAP = 512
# 单条规则的匹配闸门：一条病态正则只该毁掉它自己，不该拖垮整次诊断。
RULE_BUDGET_SECONDS = 1.0
# 报告里引用的原文上限：命中行本身可能是一整行日志，证据给到能认出来就够。
EVIDENCE_CHARS = 2000


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


def _check_environment_conflict(facts: Mapping[str, Any]) -> dict[str, str] | None:
    """点名那个加载器：`loader` 是给人看的名字，`package` 是跳转用的包 id。

    两者都由采集层从注册表补齐 —— 判定层手上没有注册表，也不该有。
    """
    conflict = facts.get("environment_conflict")
    if not isinstance(conflict, dict) or not conflict.get("conflict"):
        return None
    package = str(conflict.get("loader") or "")
    return {
        "package": package,
        "loader": str(conflict.get("loader_name") or "") or package,
        "sprocket": str(facts.get("game_version") or ""),
    }


def _broken_files(facts: Mapping[str, Any], *, loaders: bool) -> dict[str, str] | None:
    broken = [
        item
        for item in facts.get("packages_broken") or ()
        if isinstance(item, dict) and bool(item.get("is_loader")) == loaders
    ]
    if not broken:
        return None
    first = broken[0]
    package = str(first.get("id") or "")
    return {
        "package": package,
        "name": str(first.get("name") or "") or package,
        "count": str(len(broken)),
    }


def _check_loader_files_broken(facts: Mapping[str, Any]) -> dict[str, str] | None:
    return _broken_files(facts, loaders=True)


def _check_mod_files_broken(facts: Mapping[str, Any]) -> dict[str, str] | None:
    return _broken_files(facts, loaders=False)


def _check_loader_missing(facts: Mapping[str, Any]) -> dict[str, str] | None:
    if not facts.get("game_configured") or not facts.get("loaders_available"):
        return None
    if facts.get("loaders_installed"):
        return None
    return {"sprocket": str(facts.get("game_version") or "")}


CHECK_FUNCTIONS: dict[str, Callable[[Mapping[str, Any]], dict[str, str] | None]] = {
    "environment_conflict": _check_environment_conflict,
    "loader_files_broken": _check_loader_files_broken,
    "mod_files_broken": _check_mod_files_broken,
    "loader_missing": _check_loader_missing,
}


def _state_finding(entry: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any] | None:
    function = CHECK_FUNCTIONS.get(str(entry["check"]))
    if function is None:
        return None
    params = function(facts)
    if params is None:
        return None
    return _finding(entry, params=params, log_line=None)


def state_findings(
        facts: Mapping[str, Any],
        pack: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """环境检查类规则：不依赖日志，一次算完。

    游戏目录没配时这一整类都判不了 —— 事实不够就不说成没问题。
    """
    findings: list[dict[str, Any]] = []
    unjudged: list[dict[str, Any]] = []
    game_ready = bool(facts.get("game_configured"))
    for entry in pack.get("entries") or ():
        if not entry.get("check"):
            continue
        if not game_ready:
            unjudged.append({"rule": entry["id"], "reason": UNJUDGED_GAME_NOT_CONFIGURED})
            continue
        hit = _state_finding(entry, facts)
        if hit is not None:
            findings.append(hit)
    return findings, unjudged


class LogSignature:
    """一条日志签名规则的匹配状态：命中行数与首条命中都累计在这里。

    调用方读到一行就 `feed()` 一行，读取与匹配交织：内存只留计数与首条命中，扫到哪就能出到哪，
    `settled` 之后也照旧统计 —— 报告里的 `{count}` 是这条签名在整份日志里出现的**真实行数**，
    不是"够不够判"。

    同一行里命中几处只算一次：这个计数是行数，配上 `min_count` 才有"刷了几次屏"的意思。
    """

    def __init__(self, entry: Mapping[str, Any]) -> None:
        match = entry["match"]
        self.entry = entry
        self.rule_id = str(entry["id"])
        self.sources = frozenset(str(role) for role in match["sources"])
        self.minimum = int(match["min_count"])
        self.count = 0
        self.first: dict[str, Any] | None = None
        self.cost = 0.0
        self._pattern = re.compile(match["pattern"])

    @property
    def settled(self) -> bool:
        """已经够出一条结论了：调用方可以先把这条交给界面，再继续往下扫。"""
        return self.first is not None and self.count >= self.minimum

    @property
    def abandoned(self) -> bool:
        """闸门吃满：这一条不再往下扫，收尾时按「没跑完」报。"""
        return self.cost > RULE_BUDGET_SECONDS

    def feed(self, line: Mapping[str, Any]) -> None:
        if self.abandoned or str(line.get("role") or "") not in self.sources:
            return
        text = str(line.get("text") or "")
        started = time.monotonic()
        found = self._search(text)
        self.cost += time.monotonic() - started
        if found is None:
            return
        self.count += 1
        if self.first is None:
            self.first = {
                "source": str(line.get("source") or ""),
                "number": int(line.get("number") or 0),
                "text": text[:EVIDENCE_CHARS],
                "groups": [group or "" for group in found.groups()],
            }

    def _search(self, text: str) -> re.Match[str] | None:
        """一行之内按窗口滑过：短行一次匹配，长行切成有重叠的块，跨块的命中割不断。"""
        if len(text) <= BLOCK_CHARS:
            return self._pattern.search(text)
        step = BLOCK_CHARS - BLOCK_OVERLAP
        for start in range(0, len(text), step):
            found = self._pattern.search(text[start : start + BLOCK_CHARS])
            if found is not None:
                return found
        return None

    def finding(self) -> dict[str, Any] | None:
        """命中够数就是一条结论：证据取第一条命中的行号与原文。"""
        if self.first is None or self.count < self.minimum:
            return None
        params = {"count": str(self.count)}
        for index, group in enumerate(self.first["groups"], start=1):
            params[f"group{index}"] = group
        return _finding(
            self.entry,
            params=params,
            log_line={
                "source": self.first["source"],
                "number": self.first["number"],
                "text": self.first["text"],
            },
        )


def log_signatures(pack: Mapping[str, Any]) -> tuple[LogSignature, ...]:
    """规则包里所有日志签名规则的匹配状态：一趟读取喂完所有规则，不必为每条规则各读一遍日志。"""
    return tuple(
        LogSignature(entry) for entry in pack.get("entries") or () if entry.get("match")
    )


def log_hits(signatures: Iterable[LogSignature]) -> list[dict[str, Any]]:
    """已经够条件出结论的签名：命中一条就是一条，扫描进行中可以反复来取。"""
    hits: list[dict[str, Any]] = []
    for signature in signatures:
        hit = signature.finding()
        if hit is not None:
            hits.append(hit)
    return hits


def log_unjudged(
        signatures: Iterable[LogSignature],
        *,
        available_roles: Iterable[str] | None = None,
        timed_out: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """判不了的签名：闸门吃满的（没扫完）与没有来源覆盖的（没日志）。

    闸门吃满那些确实没扫完，所以既不能报命中也不能报"没有" —— 一律 `timeout`。
    """
    roles = {str(role) for role in (available_roles or ())}
    stopped = {str(rule) for rule in timed_out}
    unjudged: list[dict[str, Any]] = []
    for signature in signatures:
        if signature.rule_id in stopped:
            unjudged.append({"rule": signature.rule_id, "reason": UNJUDGED_TIMEOUT})
        elif not signature.sources & roles:
            unjudged.append({"rule": signature.rule_id, "reason": UNJUDGED_LOG_MISSING})
    return unjudged


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """命中条目的次序：必须解决在前，桶内按 level 升序，同 level 按规则 id。"""
    findings.sort(
        key=lambda item: (
            0 if item["bucket"] == BUCKET_REQUIRED else 1,
            item["level"],
            item["rule"],
        )
    )
    return findings


def split_buckets(findings: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按「必须解决 / 非必要」分成两栏；顺序沿用 `sort_findings()` 排好的那份。"""
    buckets: dict[str, list[dict[str, Any]]] = {BUCKET_REQUIRED: [], BUCKET_OPTIONAL: []}
    for finding in findings:
        buckets.setdefault(str(finding.get("bucket") or BUCKET_OPTIONAL), []).append(dict(finding))
    return buckets
