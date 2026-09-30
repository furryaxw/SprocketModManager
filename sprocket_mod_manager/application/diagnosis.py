"""一次诊断：采集本机事实、读最新那份日志、跑规则包、组装两级报告。

只诊断，不动任何文件：命中的每一条都带着证据、原因和一串教程步骤，用户自己按教程处理。

规则包从注册表来（索引还没拉下来时用同步缓存那份，都没有就是「没有规则包」）。日志每个来源
只取**最新**一份 —— 运行时声明的路径就是它当前在写的那份文件，轮转出去的历史日志不看。

`params` 是给模板填的纯字符串，`labels` 是同一批占位符的本地化值（`{语言: 文案}`）：
界面有 `labels` 就用它，否则退回 `params`。名字这类东西只有这样才不会把包 id 甩给用户看。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..domain.compatibility import CapabilityEnvironment
from ..domain.diagnosis import (
    BUCKET_OPTIONAL,
    BUCKET_REQUIRED,
    UNJUDGED_PACK_MISSING,
    evaluate,
    split_buckets,
)
from ..domain.errors import ModManagerError
from ..domain.registry import Registry
from ..infrastructure.log_reader import READ_OK, read_log_lines
from .identifiers import detected_capabilities, log_sources, log_target, runtime_states
from .unity_logs import unity_log_sources

LOGGER = logging.getLogger(__name__)

ROLE_MANAGER = "manager_log"
ROLE_LOADER = "loader_log"
ROLE_UNITY = "unity_log"

# 手动触发的诊断，用户就等在屏幕前；到点就交出已经跑完的那部分，并告诉他没跑完。
BUDGET_SECONDS = 3.0


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


def read_specs(specs: Iterable[LogSpec]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """读所有日志 -> （行, 每个来源的读数, 有没有被截断过）。"""
    lines: list[dict[str, Any]] = []
    readings: list[dict[str, Any]] = []
    truncated = False
    for spec in specs:
        text = read_log_lines(spec.path, role=spec.role, source=spec.label)
        lines.extend(text.lines)
        truncated = truncated or text.truncated
        readings.append(
            {
                "role": spec.role,
                "label": spec.label,
                "path": str(spec.path) if spec.path is not None else "",
                "status": text.status,
                "lines": len(text.lines),
            }
        )
    return lines, readings, truncated


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


def run_diagnosis(
        *,
        game_dir: Path | None,
        registry: Registry | None,
        installed: Mapping[str, Any] | None,
        environment: CapabilityEnvironment | None,
        pack: Mapping[str, Any] | None,
        pack_source: str,
        specs: Iterable[LogSpec],
        budget_seconds: float = BUDGET_SECONDS,
) -> dict[str, Any]:
    """跑一次诊断，交出报告。不落盘、不改文件。"""
    started = time.monotonic()
    lines, readings, truncated = read_specs(specs)
    facts = build_facts(
        game_dir=game_dir, registry=registry, installed=installed, environment=environment
    )
    available_roles = {
        str(reading["role"]) for reading in readings if str(reading["status"]) == READ_OK
    }
    # 预算只盖住匹配这一段：风险来自规则包里的正则，读日志该花多久就花多久。
    findings, unjudged = evaluate(
        facts,
        lines,
        pack,
        available_roles=available_roles,
        deadline=time.monotonic() + max(budget_seconds, 0.0),
    )
    if not (pack or {}).get("entries"):
        unjudged.append({"rule": "", "reason": UNJUDGED_PACK_MISSING})
    _attach_labels(findings, registry)
    buckets = split_buckets(findings)
    elapsed = time.monotonic() - started
    LOGGER.info(
        "diagnosis completed required=%d optional=%d unjudged=%d lines=%d elapsed_ms=%d",
        len(buckets[BUCKET_REQUIRED]),
        len(buckets[BUCKET_OPTIONAL]),
        len(unjudged),
        len(lines),
        int(elapsed * 1000),
    )
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "pack_version": int((pack or {}).get("pack_version") or 0),
        "pack_source": str(pack_source),
        "rules_total": len((pack or {}).get("entries") or ()),
        "required": buckets[BUCKET_REQUIRED],
        "optional": buckets[BUCKET_OPTIONAL],
        "unjudged": unjudged,
        "sources": readings,
        "lines_read": len(lines),
        "truncated": truncated,
        "elapsed_ms": int(elapsed * 1000),
    }
