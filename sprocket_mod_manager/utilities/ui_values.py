from __future__ import annotations

DEFAULT_TEXT_SCALE = 100
MIN_TEXT_SCALE = 100
MAX_TEXT_SCALE = 160


def normalize_text_scale(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_TEXT_SCALE
    try:
        scale = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TEXT_SCALE
    return max(MIN_TEXT_SCALE, min(MAX_TEXT_SCALE, scale))
