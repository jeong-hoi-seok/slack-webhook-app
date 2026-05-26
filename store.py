import json
import os
from typing import Optional

_FILE = "mr_threads.json"


def _load() -> dict:
    if not os.path.exists(_FILE):
        return {}
    with open(_FILE) as f:
        return json.load(f)


def get(mr_key: str) -> Optional[str]:
    return _load().get(mr_key)


def set(mr_key: str, thread_ts: str):
    data = _load()
    data[mr_key] = thread_ts
    with open(_FILE, "w") as f:
        json.dump(data, f)
