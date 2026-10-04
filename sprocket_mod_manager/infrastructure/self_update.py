"""单文件自更新：下载新版，交给一个换壳子进程把正在运行的这份替换掉。

Windows 下运行中的可执行文件不能覆盖自己（文件被锁），所以换壳要等旧进程让出文件：

1. A（现在这份）把新版本下载到同目录，校验 SHA-256；
2. A 启动 `B --self-update <A> <B>`；
3. A 退出，释放对自身文件的占用；
4. B 等 A 可写 → 把自己复制成 A → 启动新的 A。

Linux 上替换运行中的可执行文件本来就合法（旧进程继续用自己的 inode），第 3、4 步不用等，
但落地的新文件必须带可执行位。

打包成单文件、且这个平台有对应发布资产时 `updatable_executable()` 有值；源码运行或没有资产的
平台整条自更新关掉，界面改为把人带到发布页。
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

MANAGER_ASSET_NAMES = {
    "win32": "SprocketModManager.exe",
    "linux": "SprocketModManager-linux-x64",
}
STAGED_EXE_SUFFIX = ".new.exe"
STAGED_SUFFIX = ".new"
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


def manager_asset_name() -> str:
    """这个平台的发布资产名；没有资产的平台返回空串，自更新整条关掉。"""
    return MANAGER_ASSET_NAMES.get(sys.platform, "")


def frozen_executable() -> Path | None:
    """打包成单文件时自己的路径；源码运行返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    executable = str(getattr(sys, "executable", "") or "")
    return Path(executable) if executable else None


def can_self_update() -> bool:
    return updatable_executable() is not None


def updatable_executable() -> Path | None:
    """能原地替换自己的那个文件：打包成单文件、且这个平台有发布资产时才有。"""
    if not manager_asset_name():
        return None
    return frozen_executable()


def staged_executable(current: Path) -> Path:
    """新版本先落在这里：与当前这份同目录，换壳时才能原地替代它。"""
    if os.name == "nt":
        return current.with_name(f"{current.stem}{STAGED_EXE_SUFFIX}")
    return current.with_name(f"{current.name}{STAGED_SUFFIX}")


def staged_files(current: Path) -> tuple[Path, ...]:
    """自更新可能在这个目录里留下的东西（换壳落地文件与两类 `.part`）。"""
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
    executable = _asset_named(assets, manager_asset_name())
    if executable is None:
        LOGGER.info("latest release %s has no %s asset", release.tag, manager_asset_name())
        return None
    checksum = _asset_named(assets, f"{manager_asset_name()}{CHECKSUM_SUFFIX}")
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
    return parse_checksum_text(content, manager_asset_name(), allow_bare=True) or ""


def download_update(
        http: object,
        update: ManagerUpdate,
        destination: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
        digest: str = "",
) -> Path:
    """把新版下载到 destination，摘要不符就删掉并报错（不留半个文件）。

    `destination` 是换壳落地文件的名字：下载先落到 `<destination>.part`，校验通过才改名，
    所以「下载到一半」永远不会被当成可执行的新版本。
    """
    partial = destination.with_name(destination.name + DOWNLOAD_PART_SUFFIX)
    asset = ReleaseAsset(
        id=0,
        name=manager_asset_name(),
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
                f"downloaded {manager_asset_name()} does not match the published SHA-256"
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
    """等这个文件可以被写：运行中的 exe 在 Windows 上会被锁住。

    别的平台上替换运行中的可执行文件本来就合法（改名换的是目录项，旧进程继续用自己的
    inode），不用等 —— 而且对运行中的 ELF 开 `r+b` 只会拿到 ETXTBSY。
    """
    if os.name != "nt":
        return True
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
    """换壳子进程入口：等旧进程让出文件 → 用自己替换它 → 启动新的它。"""
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
    if os.name != "nt":
        # 复制不带权限位；Linux 上丢了可执行位，新的这份就起不来了。
        os.chmod(temporary, 0o755)
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
