from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import InstallError


EMPTY_STATE: dict[str, Any] = {"schema_version": 1, "packages": {}, "files": {}}


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return deepcopy(EMPTY_STATE)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError(f"cannot read installed state: {exc}") from exc
        if state.get("schema_version") != 1:
            raise InstallError("unsupported installed state schema")
        if not isinstance(state.get("packages"), dict) or not isinstance(state.get("files"), dict):
            raise InstallError("installed state is malformed")
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        data = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary.write_bytes(data)
        os.replace(temporary, self.path)
