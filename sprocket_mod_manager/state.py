from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import InstallError


EMPTY_STATE: dict[str, Any] = {"schema_version": 1, "packages": {}, "files": {}}


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InstallError("installed state is malformed")
    return [
        item
        for item in value
        if isinstance(item, str) and item.strip()
    ]


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
        if not isinstance(state, dict):
            raise InstallError("installed state is malformed")
        if state.get("schema_version") != 1:
            raise InstallError("unsupported installed state schema")
        if not isinstance(state.get("packages"), dict) or not isinstance(state.get("files"), dict):
            raise InstallError("installed state is malformed")
        for package_id, package in state["packages"].items():
            if (
                not isinstance(package_id, str)
                or not package_id.strip()
                or not isinstance(package, dict)
            ):
                raise InstallError("installed state is malformed")
            package["files"] = _string_list(package.get("files", []))
            package["dependencies"] = _string_list(package.get("dependencies", []))

        normalized_files: dict[str, dict[str, Any]] = {}
        for relative, entry in state["files"].items():
            if (
                not isinstance(relative, str)
                or not relative.strip()
                or not isinstance(entry, dict)
            ):
                continue
            entry["owners"] = _string_list(entry.get("owners", []))
            normalized_files[relative] = entry
        state["files"] = normalized_files
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        data = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        temporary.write_bytes(data)
        os.replace(temporary, self.path)
