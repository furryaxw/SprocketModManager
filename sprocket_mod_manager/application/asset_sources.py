"""包载荷的下载口：谁负责取这个包的字节，以及取回来。

计划准备阶段只问这两件事，不关心来源是哪一类。来源按顺序问，第一个认领的负责到底；一个都不
认领就走公开来源（按包自己声明的资产主机取回）。会话、主机白名单这类差别留在各自的来源里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain.models import ProgressCallback, RegistryPackage, ReleaseAsset, ReleaseInfo
from ..infrastructure.http_client import HttpClient


class AssetSource(Protocol):
    def handles(self, package: RegistryPackage) -> bool:
        """这个包是不是归它管。"""

    def download(
        self,
        package: RegistryPackage,
        release: ReleaseInfo,
        asset: ReleaseAsset,
        destination: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        """把这个资产取回写到 `destination`；取不回来就抛 `DownloadError`。"""


class PublicAssetSource:
    """公开来源：按条目声明的资产主机取回。谁都不认领的包由它兜底。"""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    @staticmethod
    def handles(package: RegistryPackage) -> bool:
        return True

    def download(
            self,
            package: RegistryPackage,
            release: ReleaseInfo,
            asset: ReleaseAsset,
            destination: Path,
            progress: ProgressCallback | None = None,
    ) -> None:
        self.http.download(
            asset, destination, progress=progress, hosts=set(package.asset_hosts())
        )
