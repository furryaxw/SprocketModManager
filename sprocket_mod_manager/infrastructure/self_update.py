"""单文件自更新：下载新版 exe，交给一个换壳子进程把正在运行的这份替换掉。

Windows 下运行中的可执行文件不能覆盖自己（文件被锁），所以走两个进程：

1. A（现在这份）把新版本下载到同目录的 `SprocketModManager.new.exe`，校验 SHA-256；
2. A 启动 `B --self-update <A> <B>`；
3. A 退出，释放对自身文件的占用；
4. B 等 A 可写 → 删掉 A → 把自己复制成 A → 启动新的 A。

打包成单文件时 `sys.frozen` 为真，B 就是那个新 exe 本身。源码运行（没有打包）时
`frozen_executable()` 返回 None，整条自更新关掉，界面改为把人带到发布页。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .defaults import MANAGER_REPOSITORY
from .http_client import GITHUB_ASSET_HOSTS
from ..domain.errors import DownloadError
from ..domain.models import ReleaseAsset
from ..domain.semver import Version
from ..utilities.checksums import SHA256_PATTERN, parse_checksum_text, sha256_file

LOGGER = logging.getLogger(__name__)

MANAGER_EXE_NAME = "SprocketModManager.exe"
STAGED_EXE_SUFFIX = ".new.exe"
CHECKSUM_SUFFIX = ".sha256"
DOWNLOAD_PART_SUFFIX = ".part"
SELF_UPDATE_FLAG = "--self-update"
UNLOCK_TIMEOUT_SECONDS = 20.0
UNLOCK_POLL_SECONDS = 0.5
CHECKSUM_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class ManagerUpdate:
    """比本机新、并且带着单文件自更新资产的那份发布。"""

    version: str
    tag: str
    notes: str
    page_url: str
    download_url: str
    size: int
    digest: str
    checksum_url: str


def frozen_executable() -> Path | None:
    """打包成单文件时自己的路径；源码运行返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    executable = str(getattr(sys, "executable", "") or "")
    return Path(executable) if executable else None


def can_self_update() -> bool:
    return frozen_executable() is not None


def staged_executable(current: Path) -> Path:
    """新版本先落在这里：与当前 exe 同目录，换壳时才能原地替代它。"""
    return current.with_name(f"{current.stem}{STAGED_EXE_SUFFIX}")


def staged_files(current: Path) -> tuple[Path, ...]:
    """自更新可能在这个目录里留下的东西（`.new.exe` 与两类 `.part`）。"""
    staged = staged_executable(current)
    return (
        staged,
        staged.with_name(staged.name + DOWNLOAD_PART_SUFFIX),
        current.with_name(current.name + DOWNLOAD_PART_SUFFIX),
    )


def cleanup_staged(current: Path) -> None:
    """清掉上一轮换壳留下的文件。删不掉（还锁着）就留到下次启动，不打扰用户。"""
    for path in staged_files(current):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            LOGGER.debug("could not remove leftover %s: %s", path, exc)


def find_update(
        github: object,
        current_version: str,
        repository: str = MANAGER_REPOSITORY,
) -> ManagerUpdate | None:
    """查最新发布：比本机新、且带自更新资产时返回它，否则返回 None。"""
    return update_from_release(github.latest_repository_release(repository), current_version)


def update_from_release(release: object, current_version: str) -> ManagerUpdate | None:
    """这份发布比本机新、且带 `<exe 名>` 资产时返回它，否则返回 None。"""
    try:
        current = Version.parse(current_version)
    except ValueError:
        LOGGER.warning("current version is not parseable: %s", current_version)
        return None
    if release.version <= current:
        return None
    assets = tuple(getattr(release, "assets", ()) or ())
    executable = _asset_named(assets, MANAGER_EXE_NAME)
    if executable is None:
        LOGGER.info("latest release %s has no %s asset", release.tag, MANAGER_EXE_NAME)
        return None
    checksum = _asset_named(assets, f"{MANAGER_EXE_NAME}{CHECKSUM_SUFFIX}")
    return ManagerUpdate(
        version=str(release.version),
        tag=release.tag,
        notes=str(getattr(release, "notes", "") or ""),
        page_url=release.page_url,
        download_url=executable.download_url,
        size=int(executable.size or 0),
        digest=str(executable.digest or ""),
        checksum_url=checksum.download_url if checksum else "",
    )


def published_digest(http: object, update: ManagerUpdate) -> str:
    """这份发布自称的 SHA-256：资产自带摘要优先，其次取同名 `.sha256`。取不到返回空串。"""
    algorithm, separator, value = str(update.digest or "").partition(":")
    if separator and algorithm.casefold() == "sha256" and SHA256_PATTERN.fullmatch(value):
        return value.casefold()
    if not update.checksum_url:
        return ""
    try:
        content = http.get_bytes(
            update.checksum_url,
            timeout=30,
            max_bytes=CHECKSUM_MAX_BYTES,
            allowed_hosts=GITHUB_ASSET_HOSTS,
        ).decode("utf-8-sig")
    except (DownloadError, UnicodeDecodeError) as exc:
        LOGGER.warning("could not read the published checksum: %s", exc)
        return ""
    return parse_checksum_text(content, MANAGER_EXE_NAME, allow_bare=True) or ""


def download_update(
        http: object,
        update: ManagerUpdate,
        destination: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        digest: str = "",
) -> Path:
    """把新 exe 下载到 destination，摘要不符就删掉并报错（不留半个文件）。

    `destination` 是 `.new.exe` 这个名字：下载先落到 `.new.exe.part`，校验通过才改名，
    所以「下载到一半」永远不会被当成可执行的新版本。
    """
    partial = destination.with_name(destination.name + DOWNLOAD_PART_SUFFIX)
    asset = ReleaseAsset(
        id=0,
        name=MANAGER_EXE_NAME,
        size=max(0, int(update.size or 0)),
        download_url=update.download_url,
        digest=f"sha256:{digest}" if digest else None,
    )
    _remove(partial)
    try:
        http.download(asset, partial, progress)
        expected = digest or published_digest(http, update)
        actual = sha256_file(partial)
        if expected and actual != expected:
            raise DownloadError(
                f"downloaded {MANAGER_EXE_NAME} does not match the published SHA-256"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, destination)
    except BaseException:
        _remove(partial)
        raise
    LOGGER.info("manager update downloaded version=%s bytes=%d", update.version, destination.stat().st_size)
    return destination


def launch_self_update(
        current: Path,
        staged: Path,
        *,
        app_dir: Path | None = None,
        launch: Callable[[list[str]], None] | None = None,
) -> Path:
    """拉起换壳子进程。调用方返回后要尽快退出，把自身文件让出来。"""
    if current == staged:
        raise ValueError("staged executable must differ from the running one")
    argv = [str(staged), SELF_UPDATE_FLAG, str(current), str(staged)]
    if app_dir is not None:
        argv += ["--app-dir", str(app_dir)]
    (launch or start_process)(argv)
    LOGGER.info("self-update child started target=%s staged=%s", current, staged)
    return staged


def start_process(argv: Iterable[str]) -> None:
    """起一个不跟着父进程一起走的子进程。"""
    arguments = [str(item) for item in argv]
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(arguments, close_fds=True, creationflags=flags)
        return
    subprocess.Popen(arguments, close_fds=True, start_new_session=True)


def wait_for_unlock(
        path: Path,
        timeout: float = UNLOCK_TIMEOUT_SECONDS,
        *,
        sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """等这个文件可以被写：运行中的 exe 在 Windows 上会被锁住。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with path.open("r+b"):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            sleep(UNLOCK_POLL_SECONDS)


def self_update_mode(
        argv: list[str],
        *,
        launch: Callable[[list[str]], None] | None = None,
        wait: Callable[[Path, float], bool] | None = None,
) -> int:
    """换壳子进程入口：等旧进程退出 → 用自己替换它 → 启动新的它。"""
    if len(argv) < 3:
        LOGGER.error("%s needs <target> <staged>", SELF_UPDATE_FLAG)
        return 2
    target = Path(argv[1]).expanduser()
    staged = Path(argv[2]).expanduser()
    if target == staged:
        LOGGER.error("self-update target and staged file are the same: %s", target)
        return 1
    waiter = wait or wait_for_unlock
    LOGGER.info("self-update waiting for %s to be released", target)
    if not waiter(target, UNLOCK_TIMEOUT_SECONDS):
        LOGGER.error("self-update could not replace %s: still in use", target)
        return 1
    try:
        _replace(target, staged)
    except OSError as exc:
        LOGGER.error("self-update could not replace %s: %s", target, exc)
        return 1
    LOGGER.info("self-update replaced %s", target)
    try:
        (launch or start_process)([str(target)])
    except OSError as exc:
        LOGGER.error("self-update could not restart %s: %s", target, exc)
        return 1
    return 0


def _replace(target: Path, staged: Path) -> None:
    """先把新版写到旁边，再原子改名：中途失败时旧版本还在，不会两头空。"""
    temporary = target.with_name(target.name + DOWNLOAD_PART_SUFFIX)
    _remove(temporary)
    shutil.copyfile(staged, temporary)
    os.replace(temporary, target)


def _asset_named(assets: Iterable[ReleaseAsset], name: str) -> ReleaseAsset | None:
    for asset in assets or ():
        if str(getattr(asset, "name", "")).casefold() == name.casefold():
            return asset
    return None


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        LOGGER.debug("could not remove %s: %s", path, exc)
