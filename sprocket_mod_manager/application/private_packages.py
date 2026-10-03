"""私有包载荷的下载口。

包归属**不是猜出来的**：它就是「这次刷新是哪个服务器下发了这个包」。包 id 的第一段是 Team，
但 Team 不告诉你服务器 —— 同一个人在两台服务器上都可能有同名 Team —— 所以归属只能来自
下发它的那次响应。谁下发的谁负责取回，会话也就用对了。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from ..domain.errors import DownloadError
from ..domain.models import ProgressCallback, RegistryPackage, ReleaseAsset, ReleaseInfo
from ..infrastructure.private_servers import DeveloperServerClient, DeveloperServerError


class PrivatePackageSource:
    def __init__(self, clients: Mapping[str, DeveloperServerClient] | None = None):
        self._clients: dict[str, DeveloperServerClient] = dict(clients or {})
        # package id → 下发它的 server id
        self._owners: dict[str, str] = {}

    def register(self, server_id: str, client: DeveloperServerClient) -> None:
        """交进来一台已连上的服务器：它下发的包才路由得回去，也才敢记归属。"""
        owner = str(server_id).strip()
        if owner:
            self._clients[owner] = client

    def learn(self, server_id: str, packages: Iterable[dict]) -> None:
        """记下这批包来自哪台服务器。后学的覆盖先学的：同一 id 出现在两台服务器上时，
        以最近一次刷新为准，避免一个包同时挂在两个会话下。"""
        owner = str(server_id).strip()
        if not owner:
            return
        if owner not in self._clients:
            raise DownloadError(f"unknown developer server: {owner}")
        for package in packages:
            package_id = str(package.get("id", "")).strip() if isinstance(package, dict) else ""
            if package_id:
                self._owners[package_id] = owner

    def forget(self, server_id: str) -> None:
        """忘掉一台服务器：它的会话与它下发的包归属一起撤掉。"""
        owner = str(server_id).strip()
        self._clients.pop(owner, None)
        for package_id in [key for key, value in self._owners.items() if value == owner]:
            del self._owners[package_id]

    def owner_of(self, package_id: str) -> str:
        return self._owners.get(str(package_id).strip(), "")

    def refresh(self) -> list[dict]:
        """逐台拉私有索引（每条都已验签），顺手记下归属，返回合并后的条目。

        异常直接冒出去，不在这里吞：验签不过和连不上是两件性质不同的事，但都**必须**让调用方
        知道 —— 把验签失败降级成「这台这次跳过」，等于给了攻击者一条静默降级的路。
        """
        merged: list[dict] = []
        for server_id, client in self._clients.items():
            entries = client.packages()
            self.learn(server_id, entries)
            merged.extend(entries)
        return merged

    def handles(self, package: RegistryPackage) -> bool:
        return self.owner_of(package.id) != ""

    def download(
        self,
        package: RegistryPackage,
        release: ReleaseInfo,
        asset: ReleaseAsset,
        destination: Path,
        progress: ProgressCallback | None = None,
    ) -> None:
        owner = self.owner_of(package.id)
        client = self._clients.get(owner) if owner else None
        if client is None:
            raise DownloadError(f"no developer server is responsible for {package.id}")
        try:
            client.download(
                package.id,
                str(release.version),
                destination,
                url=asset.download_url,
                progress=progress,
            )
        except DeveloperServerError as exc:
            raise DownloadError(f"{package.id}: {exc}") from exc
