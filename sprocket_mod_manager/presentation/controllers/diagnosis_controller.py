"""「错误修复」页的 API：跑一次诊断，交出报告。

只读：读磁盘上的事实、读日志、读规则包。这条路径上没有任何写操作，也没有任何"一键执行"。
"""

from __future__ import annotations

from typing import Any

from ...application.data_hub import KEY_DIAGNOSIS
from ...application.diagnosis import diagnosis_log_specs, run_diagnosis
from ...domain.errors import ModManagerError
from ...infrastructure.app_logging import manager_log_path
from ...infrastructure.diagnosis_cache import read_diagnosis_pack
from .base import ApiController


class DiagnosisController(ApiController):
    def _diagnosis_pack(self) -> tuple[dict[str, Any] | None, str]:
        """规则包：索引里那份优先，其次用上次同步缓存下来的那份，都没有就是「没有规则包」。"""
        registry = self.service.registry if self.service is not None else None
        pack = getattr(registry, "diagnosis", None)
        if pack and pack.get("entries"):
            return pack, "registry"
        cached = read_diagnosis_pack(self.config_store.app_dir)
        if cached is None:
            return None, "missing"
        return cached, "cache"

    def run_diagnosis(self) -> dict[str, Any]:
        """跑一次诊断。

        现状边走边推到数据层（`KEY_DIAGNOSIS`）：错误列表在扫描开始那一刻就建好了，日志的命中
        随后一条条进来，所以界面不用等这条调用返回就能先摆出结论。返回值是收尾那一份。
        """
        try:
            game_path = self._game_path_or_none()
            registry = self.service.registry if self.service is not None else None
            installed = (
                self.service.installed(game_path)
                if registry is not None and game_path is not None
                else {}
            )
            capabilities = getattr(self.service.environment, "capabilities", None)
            specs = diagnosis_log_specs(
                game_path,
                manager_log=manager_log_path(self.config_store.app_dir),
                packages=registry.packages if registry is not None else (),
                installed_ids=tuple(installed),
                capabilities=capabilities if isinstance(capabilities, dict) else {},
            )
            pack, pack_source = self._diagnosis_pack()
            report = run_diagnosis(
                game_dir=game_path,
                registry=registry,
                installed=installed,
                environment=self.current_environment(),
                pack=pack,
                pack_source=pack_source,
                specs=specs,
                on_progress=self._publish,
            )
            return self._success(report=report)
        except (OSError, ValueError, ModManagerError) as exc:
            return self._failure(exc, code="diagnosis_failed")

    def _publish(self, state: dict[str, Any]) -> None:
        """把当前现状交给数据层：界面订的就是这一份，形状与收尾那份一模一样。"""
        self.data.publish(KEY_DIAGNOSIS, state)
