from __future__ import annotations

import os
from pathlib import Path


def open_directory(path: Path) -> None:
    directory = path.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OSError("opening directories is only supported on Windows")
    startfile(str(directory))
