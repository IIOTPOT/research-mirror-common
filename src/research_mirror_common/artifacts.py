from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ArtifactStorage(Protocol):
    def put_bytes(self, source_id: str, key: str, data: bytes) -> Path:
        ...

    def path_for(self, source_id: str, key: str) -> Path:
        ...

    def exists(self, source_id: str, key: str) -> bool:
        ...


class LocalArtifactStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def put_bytes(self, source_id: str, key: str, data: bytes) -> Path:
        path = self.path_for(source_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def path_for(self, source_id: str, key: str) -> Path:
        safe_source = _safe_segment(source_id)
        parts = [_safe_segment(part) for part in Path(str(key)).parts if part not in {"", "."}]
        if not parts:
            raise ValueError("artifact key is required")
        return self.root.joinpath(safe_source, *parts)

    def exists(self, source_id: str, key: str) -> bool:
        return self.path_for(source_id, key).exists()


def _safe_segment(value: object) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError(f"unsafe artifact path segment: {value!r}")
    return text
