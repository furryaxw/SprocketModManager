from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..domain.errors import DownloadError

MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LogUploadResult:
    request_id: str
    status: int
    bytes_uploaded: int
    url: str


def latest_log_path(game_dir: Path) -> Path:
    return game_dir.expanduser() / "MelonLoader" / "Latest.log"


def upload_log_file(path: Path, endpoint: str, *, app_version: str, timeout: int = 30,
                    max_bytes: int = MAX_LOG_BYTES) -> LogUploadResult:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DownloadError("log upload endpoint must be an HTTPS URL")
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"log file not found: {path}")
    raw = path.read_bytes()
    body = raw[-max_bytes:].decode("utf-8", errors="replace").encode("utf-8")
    LOGGER.info("log upload started file=%s bytes=%d", path.name, len(body))
    request_id = uuid.uuid4().hex
    request = Request(endpoint, data=body, method="POST", headers={
        "Content-Type": "text/plain; charset=utf-8", "User-Agent": f"sprocket-mod-manager/{app_version}"
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            response_body = response.read(MAX_RESPONSE_BYTES)
            status = int(response.status)
    except (HTTPError, URLError, OSError) as exc:
        LOGGER.error("log upload request failed file=%s error=%s", path.name, exc)
        raise DownloadError(f"log upload failed: {exc}") from exc
    if not 200 <= status < 300:
        LOGGER.error("log upload returned status=%d file=%s", status, path.name)
        raise DownloadError(f"log upload returned HTTP {status}")
    url = response_body.decode("ascii", errors="ignore").strip()
    if not url.startswith("https://"):
        LOGGER.error("log upload returned invalid URL file=%s", path.name)
        raise DownloadError("log upload returned an invalid URL")
    LOGGER.info("log upload completed file=%s status=%d bytes=%d", path.name, status, len(body))
    return LogUploadResult(request_id, status, len(body), url)


def upload_latest_log(game_dir: Path, endpoint: str, *, app_version: str, timeout: int = 30,
                      max_bytes: int = MAX_LOG_BYTES) -> LogUploadResult:
    return upload_log_file(
        latest_log_path(game_dir),
        endpoint,
        app_version=app_version,
        timeout=timeout,
        max_bytes=max_bytes,
    )
