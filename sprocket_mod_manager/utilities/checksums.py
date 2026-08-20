from __future__ import annotations

import hashlib
import re
from pathlib import Path

SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksum_text(
        text: str,
        filename: str,
        *,
        allow_bare: bool = False,
) -> str | None:
    """Read a SHA-256 from common sidecar checksum formats."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if allow_bare and SHA256_PATTERN.fullmatch(line):
            return line.casefold()
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
        if match and Path(match.group(2).strip()).name.casefold() == filename.casefold():
            return match.group(1).casefold()
        match = re.fullmatch(r"SHA256\s*\((.+)\)\s*=\s*([0-9a-fA-F]{64})", line, re.I)
        if match and Path(match.group(1).strip()).name.casefold() == filename.casefold():
            return match.group(2).casefold()
    return None
