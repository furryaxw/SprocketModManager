"""私有包载荷的下载口。

私有条目的下载地址指向某台开发者服务器，取回时要带那台服务器的会话，还要过
`server-info` 的 `download_origins` —— 公开包的 `HttpClient` 管不了这件事。持有服务器配置的
一方实现这个口子，`PlanPreparer` 只负责问「这个包归你管吗」和「取回来」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain.models import ProgressCallback, RegistryPackage, ReleaseAsset, ReleaseInfo


class PrivateAssetSource(Protocol):
    def handles(self, package: RegistryPackage) -> bool:
        """这个包是不是归它管（来自它认识的那几台开发者服务器之一）。"""

    def download(
        self,
        package: RegistryPackage,
        release: ReleaseInfo,
        asset: ReleaseAsset,
        destination: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        """把这个资产取回写到 `destination`；取不回来就抛 `DownloadError`。"""
