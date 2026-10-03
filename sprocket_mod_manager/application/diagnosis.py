"""一次诊断：采集本机事实、流式读日志、跑规则包，边扫边交报告。

只诊断，不动任何文件：命中的每一条都带着证据、原因和一串教程步骤，用户自己按教程处理。

规则包从注册表来（索引还没拉下来时用同步缓存那份，都没有就是「没有规则包」）。日志每个来源
只取**最新**一份 —— 运行时声明的路径就是它当前在写的那份文件，轮转出去的历史日志不看。

交付方式是边扫边交：环境检查那部分先算完并立刻交出去（错误列表这一刻就建好了），随后日志一行
一行喂给签名匹配，命中一条交一条。`on_progress` 每次收到的都是当前那份完整状态，形状与返回值
一模一样。扫描会一直跑到日志读完 —— 单条规则可以被自己的闸门放弃，整次扫描不会中途交出一份
不完整却看着正常的报告。

`params` 是给模板填的纯字符串，`labels` 是同一批占位符的本地化值（`{语言: 文案}`）：
界面有 `labels` 就用它，否则退回 `params`。名字这类东西只有这样才不会把包 id 甩给用户看。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..domain.compatibility import CapabilityEnvironment
from ..domain.diagnosis import (
    BUCKET_OPTIONAL,
    BUCKET_REQUIRED,
    UNJUDGED_CHECKS_FAILED,
    UNJUDGED_LOG_UNREADABLE,
    UNJUDGED_PACK_MISSING,
    diagnosis_pack,
    log_hits,
    log_signatures,
    log_unjudged,
    sort_findings,
    split_buckets,
    state_findings,
)
from ..domain.errors import ModManagerError
from ..domain.registry import Registry
from ..infrastructure.log_reader import READ_OK, LogStream
from .identifiers import detected_capabilities, log_sources, log_target, runtime_states
from .unity_logs import unity_log_sources

LOGGER = logging.getLogger(__name__)

ROLE_MANAGER = "manager_log"
ROLE_LOADER = "loader_log"
ROLE_UNITY = "unity_log"

# 同一次扫描里状态最多隔这么久交一次：命中很密时不必每一行都推一遍界面。
PROGRESS_SECONDS = 0.2


@dataclass(frozen=True)
class LogSpec:
    """要读的一份日志：它在规则包里的角色、给人看的标签、以及文件位置。"""

    role: str
    label: str
    path: Path | None


def diagnosis_log_specs(
        game_dir: Path | None,
        *,
        manager_log: Path | None,
        packages: Iterable[Any] = (),
        installed_ids: Iterable[str] = (),
        capabilities: Mapping[str, object] | None = None,
) -> tuple[LogSpec, ...]:
    """要读的日志：管理器自己那份，加上这个游戏目录里当前存在的那些。"""
    specs: list[LogSpec] = []
    if manager_log is not None:
        specs.append(LogSpec(ROLE_MANAGER, str(manager_log), Path(manager_log)))
    if game_dir is not None:
        for source in log_sources(game_dir, packages, installed_ids, capabilities or {}):
            specs.append(LogSpec(ROLE_LOADER, source.path, log_target(game_dir, source)))
        for source in unity_log_sources(game_dir):
            specs.append(LogSpec(ROLE_UNITY, source.path, log_target(game_dir, source)))
    return tuple(specs)


def build_facts(
        *,
        game_dir: Path | None,
        registry: Registry | None,
        installed: Mapping[str, Any] | None,
        environment: CapabilityEnvironment | None,
) -> dict[str, Any]:
    """规则包能读到的那份本机事实。

    「加载器装没装」用 `runtime_states()`：管理器之外装上去的加载器也在场（磁盘上检测到运行时
    就算），只看安装记录会把那种机器报成「还没有装加载器」。

    `name` / `loader_name` 是**纯字符串**（注册表里的 `name`）；本地化的那份由
    `_attach_labels()` 补成 `labels`，判定层不用知道界面现在是什么语言。
    """
    loaders = tuple(package for package in (registry.packages if registry else ()) if package.is_loader)
    loader_ids = {package.id for package in loaders}
    installed = installed or {}
    states = runtime_states(loaders, installed, detected_capabilities(game_dir)) if loaders else {}
    facts: dict[str, Any] = {
        "game_configured": game_dir is not None,
        "game_version": str(getattr(environment, "sprocket", "") or ""),
        "loaders_available": bool(loader_ids),
        "loaders_installed": sorted(package_id for package_id, state in states.items() if state[0]),
        "environment_conflict": {"conflict": False},
        "packages_broken": [],
    }

    if environment is not None:
        state = environment.consistency()
        if state.get("state") == "conflict":
            loader_id = str(state.get("loader") or "")
            facts["environment_conflict"] = {
                "conflict": True,
                "loader": loader_id,
                "loader_name": _plain_name(registry, loader_id),
            }

    broken: list[dict[str, Any]] = []
    for package_id, entry in installed.items():
        if not isinstance(entry, dict) or not entry.get("corrupted"):
            continue
        broken.append(
            {
                "id": str(package_id),
                "name": _plain_name(registry, str(package_id)),
                "is_loader": str(package_id) in loader_ids,
            }
        )
    facts["packages_broken"] = broken
    return facts


def _plain_name(registry: Registry | None, package_id: str) -> str:
    """注册表里的纯字符串名字；查不到就退回包 id。"""
    if registry is None or not package_id:
        return package_id
    try:
        return registry.get(package_id).name or package_id
    except (ModManagerError, KeyError, TypeError, ValueError):
        return package_id


def _display_name(registry: Registry | None, package_id: str) -> dict[str, str]:
    if registry is None or not package_id:
        return {}
    try:
        return dict(registry.get(package_id).display_name or {})
    except (ModManagerError, KeyError, TypeError, ValueError):
        return {}


def _attach_labels(findings: Iterable[dict[str, Any]], registry: Registry | None) -> None:
    """把包 id 换成能看懂的本地化名字：`{name}` / `{loader}` 两个占位符都用得上。"""
    for finding in findings:
        package_id = str((finding.get("params") or {}).get("package") or "")
        if not package_id:
            continue
        display = _display_name(registry, package_id)
        if display:
            labels = finding.setdefault("labels", {})
            labels["name"] = dict(display)
            labels["loader"] = dict(display)


def _report(
        *,
        stamp: datetime,
        started_at: float,
        pack: Mapping[str, Any],
        pack_source: str,
        findings: Iterable[dict[str, Any]],
        unjudged: Iterable[dict[str, Any]],
        readings: Iterable[dict[str, Any]],
        lines_read: int,
        running: bool,
) -> dict[str, Any]:
    """当前状态的那份读数：扫描中途交出去的与最后返回的是**同一份形状**。"""
    buckets = split_buckets(sort_findings([dict(item) for item in findings]))
    return {
        "generated_at": stamp.isoformat().replace("+00:00", "Z"),
        "pack_version": int(pack.get("pack_version") or 0),
        "pack_source": str(pack_source),
        "rules_total": len(pack.get("entries") or ()),
        "required": buckets[BUCKET_REQUIRED],
        "optional": buckets[BUCKET_OPTIONAL],
        "unjudged": [dict(item) for item in unjudged],
        "sources": [dict(item) for item in readings],
        "lines_read": lines_read,
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        "running": bool(running),
    }


def run_diagnosis(
        *,
        game_dir: Path | None,
        registry: Registry | None,
        installed: Mapping[str, Any] | None,
        environment: CapabilityEnvironment | None,
        pack: Mapping[str, Any] | None,
        pack_source: str,
        specs: Iterable[LogSpec],
        on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """跑一次诊断，交出现状；`on_progress` 收到每一次现状，返回的是收尾那一份。

    「事实不够不许说成没问题」有两处落点：某条规则的闸门吃满时它记 `timeout`（不报"没有"），
    它要的日志一份都没读到时记 `log_missing`。
    """
    started_at = time.monotonic()
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    normalized = diagnosis_pack(pack)
    try:
        facts = build_facts(
            game_dir=game_dir, registry=registry, installed=installed, environment=environment
        )
        state_hits, unjudged = state_findings(facts, normalized)
    except Exception as exc:  # 环境那几条整体读不出来：照样出报告，并说明是「判不了」
        LOGGER.warning("environment checks failed error=%s", exc)
        state_hits, unjudged = [], [{"rule": "", "reason": UNJUDGED_CHECKS_FAILED}]
    if not normalized.get("entries"):
        unjudged.append({"rule": "", "reason": UNJUDGED_PACK_MISSING})
    _attach_labels(state_hits, registry)

    try:
        signatures = log_signatures(normalized)
    except Exception as exc:  # 签名表建不出来：规则读不了，如实说，别当作「没有命中」
        LOGGER.warning("log signatures failed error=%s", exc)
        signatures = []
        unjudged.append({"rule": "", "reason": UNJUDGED_CHECKS_FAILED})
    readings: list[dict[str, Any]] = []
    lines_read = 0
    roles: set[str] = set()

    def current_hits() -> list[dict[str, Any]]:
        """这一刻该报的命中：环境那几条（一次算完）+ 已经够条件的签名。"""
        return [*state_hits, *log_hits(signatures)]

    def state(*, running: bool) -> dict[str, Any]:
        return _report(
            stamp=stamp,
            started_at=started_at,
            pack=normalized,
            pack_source=pack_source,
            findings=current_hits(),
            unjudged=unjudged,
            readings=readings,
            lines_read=lines_read,
            running=running,
        )

    def emit(*, running: bool) -> None:
        if on_progress is not None:
            on_progress(state(running=running))

    # 错误列表这一刻就建好：环境检查那几条先摆上，日志的命中随后一条条进来。
    emit(running=True)
    last_emit = started_at

    for spec in specs:
        try:
            with LogStream(spec.path, role=spec.role, source=spec.label) as stream:
                if stream.status == READ_OK:
                    roles.add(spec.role)
                for line in stream:
                    lines_read += 1
                    hit = False
                    for signature in signatures:
                        before = signature.count
                        signature.feed(line)
                        hit = hit or signature.count != before
                    now = time.monotonic()
                    if hit and now - last_emit >= PROGRESS_SECONDS:
                        last_emit = now
                        emit(running=True)
                readings.append(stream.reading())
        except Exception as exc:  # 一份日志读不下去只跳过它，别让整份诊断没有报告
            LOGGER.warning("log source failed source=%s error=%s", spec.label, exc)
            unjudged.append({"rule": "", "reason": UNJUDGED_LOG_UNREADABLE})
        emit(running=True)

    timed_out = {signature.rule_id for signature in signatures if signature.abandoned}
    unjudged.extend(log_unjudged(signatures, available_roles=roles, timed_out=timed_out))
    report = state(running=False)
    emit(running=False)
    LOGGER.info(
        "diagnosis completed required=%d optional=%d unjudged=%d lines=%d elapsed_ms=%d",
        len(report["required"]),
        len(report["optional"]),
        len(report["unjudged"]),
        report["lines_read"],
        report["elapsed_ms"],
    )
    return report
