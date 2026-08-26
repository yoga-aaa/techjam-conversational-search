from __future__ import annotations

import json
from pathlib import Path


class NullTraceSink:
    def record(self, event: dict) -> None:
        return None


class JsonlTraceSink:
    """Writes internal, ground-truth-free turn traces for local diagnosis."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
