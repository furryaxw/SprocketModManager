from __future__ import annotations

import hashlib
from pathlib import Path

from .installer import Installer
from .state import StateStore


class InstallerProfiles:
    """Map game installations to isolated manager state stores."""

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = app_dir

    def installer_for(self, game_dir: Path) -> Installer:
        resolved_game_dir = game_dir.expanduser().resolve()
        profile_key = hashlib.sha256(
            str(resolved_game_dir).casefold().encode("utf-8")
        ).hexdigest()[:20]
        profile_dir = self.app_dir / "profiles" / profile_key
        profile_dir.mkdir(parents=True, exist_ok=True)
        marker = profile_dir / "game-path.txt"
        if not marker.is_file():
            marker.write_text(str(resolved_game_dir), encoding="utf-8")
        return Installer(self.app_dir, StateStore(profile_dir / "installed.json"))
