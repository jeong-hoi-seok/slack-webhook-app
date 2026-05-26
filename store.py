import json
import logging
import pathlib
from typing import Optional

logger = logging.getLogger(__name__)

_FILE = pathlib.Path(__file__).parent / "mr_threads.json"


def _load() -> dict:
    if not _FILE.exists():
        return {}
    try:
        with open(_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("mr_threads.json corrupted, starting fresh")
        return {}


def get(mr_key: str) -> Optional[str]:
    return _load().get(mr_key)


def set(mr_key: str, thread_ts: str):
    data = _load()
    data[mr_key] = thread_ts
    with open(_FILE, "w") as f:
        json.dump(data, f)
