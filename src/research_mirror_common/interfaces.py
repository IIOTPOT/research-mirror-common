from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Protocol


@dataclass(frozen=True)
class SourceCapabilities:
    pdf: bool = False
    comments: bool = False
    subscribers: bool = False
    private_mode: bool = False
    hashtags: bool = False
    rebuild: bool = False

    def as_dict(self) -> dict[str, bool]:
        return asdict(self)


class SourceConnector(Protocol):
    source_id: str
    capabilities: SourceCapabilities

    def fetch_entries(self, *, since: Any) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_material(self, entry: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def render_preview(self, material: dict[str, Any]) -> list[str]:
        return []

    def deliver(self, material: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def refresh_comments(self, material: dict[str, Any]) -> list[dict[str, Any]]:
        return []
