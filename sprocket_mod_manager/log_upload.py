from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .errors import DownloadError

MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class LogUploadResult:
    request_id: str
    status: int
    bytes_uploaded: int
    url: str


def latest_log_path(game_dir: Path) -> Path:
    return game_dir.expanduser() / "MelonLoader" / "Latest.log"


def upload_latest_log(game_dir: Path, endpoint: str, *, app_version: str, timeout: int = 30, max_bytes: int = MAX_LOG_BYTES) -> LogUploadResult:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DownloadError("log upload endpoint must be an HTTPS URL")
    path = latest_log_path(game_dir)
    if not path.is_file():
        raise FileNotFoundError(f"MelonLoader log not found: {path}")
    raw = path.read_bytes()
    body = raw[-max_bytes:].decode("utf-8", errors="replace").encode("utf-8")
    request_id = uuid.uuid4().hex
    request = Request(endpoint, data=body, method="POST", headers={
        "Content-Type": "text/plain; charset=utf-8", "User-Agent": f"sprocket-mod-manager/{app_version}"
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read(MAX_RESPONSE_BYTES)
            status = int(response.status)
    except (HTTPError, URLError, OSError) as exc:
        raise DownloadError(f"log upload failed: {exc}") from exc
    if not 200 <= status < 300:
        raise DownloadError(f"log upload returned HTTP {status}")
    url = response_body.decode("ascii", errors="ignore").strip()
    if not url.startswith("https://"):
        raise DownloadError("log upload returned an invalid URL")
    return LogUploadResult(request_id, status, len(body), url)
