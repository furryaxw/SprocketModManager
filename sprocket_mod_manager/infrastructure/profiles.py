from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .installer import Installer
from .manager_paths import STATE_FILE_NAME, manager_state_dir
from .state import StateStore
from ..domain.errors import InstallError

LOGGER = logging.getLogger(__name__)


class InstallerProfiles:
    """Map game installations to isolated manager state stores."""

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = app_dir

    def installer_for(self, game_dir: Path) -> Installer:
        resolved_game_dir = game_dir.expanduser().resolve()
        state_dir = manager_state_dir(resolved_game_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        store = StateStore(state_dir / STATE_FILE_NAME)
        self._migrate_legacy_state(resolved_game_dir, store)
        return Installer(self.app_dir, store)

    def legacy_state_path(self, game_dir: Path) -> Path:
        """旧版位置（AppData/profiles/<游戏目录哈希>/installed.json）。"""
        resolved_game_dir = game_dir.expanduser().resolve()
        profile_key = hashlib.sha256(
            str(resolved_game_dir).casefold().encode("utf-8")
        ).hexdigest()[:20]
        return self.app_dir / "profiles" / profile_key / "installed.json"

    def _migrate_legacy_state(self, game_dir: Path, store: StateStore) -> None:
        """把 AppData 里的旧记录搬到游戏目录一次；旧文件保留不删，便于回退。"""
        if store.path.is_file():
            return

        legacy = self.legacy_state_path(game_dir)
        if not legacy.is_file():
            return

        try:
            state = StateStore(legacy).load()
        except InstallError as exc:
            LOGGER.warning("legacy installed state could not be read: %s", exc)
            return

        try:
            store.save(state)
        except OSError as exc:
            LOGGER.warning("could not migrate installed state into the game directory: %s", exc)
            return
        LOGGER.info(
            "migrated installed state from %s to %s (legacy file kept as a backup)",
            legacy,
            store.path,
        )
