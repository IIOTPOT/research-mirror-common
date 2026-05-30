from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .interfaces import SourceCapabilities, SourceConnector


@dataclass(frozen=True)
class StaticSourceConnector:
    source_id: str
    capabilities: SourceCapabilities
    description: str = ""

    def fetch_entries(self, *, since: Any) -> list[dict[str, Any]]:
        raise NotImplementedError(f"{self.source_id} connector is implemented in its source-specific project")

    def fetch_material(self, entry: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.source_id} connector is implemented in its source-specific project")


class SourceRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, SourceConnector] = {}

    def register(self, connector: SourceConnector) -> None:
        source_id = str(connector.source_id).strip()
        if not source_id:
            raise ValueError("source_id is required")
        self._connectors[source_id] = connector

    def get(self, source_id: str) -> SourceConnector:
        return self._connectors[source_id]

    def source_ids(self) -> list[str]:
        return sorted(self._connectors)

    def describe(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for source_id, connector in sorted(self._connectors.items()):
            result[source_id] = {
                "source_id": source_id,
                "capabilities": connector.capabilities.as_dict(),
                "description": getattr(connector, "description", ""),
            }
        return result


def default_source_registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(
        StaticSourceConnector(
            "d8",
            SourceCapabilities(pdf=True, comments=False, hashtags=False),
            "D8 Capital Research mirror",
        )
    )
    registry.register(
        StaticSourceConnector(
            "alfa",
            SourceCapabilities(pdf=True, comments=False, hashtags=False),
            "Alfa Research mirror",
        )
    )
    registry.register(
        StaticSourceConnector(
            "rencap",
            SourceCapabilities(pdf=True, private_mode=True, hashtags=True),
            "RenCap Research Portal mirror",
        )
    )
    registry.register(
        StaticSourceConnector(
            "alenka",
            SourceCapabilities(pdf=True, comments=True, rebuild=True),
            "Alenka Capital mirror",
        )
    )
    registry.register(
        StaticSourceConnector(
            "mozgovik",
            SourceCapabilities(pdf=True, comments=True, hashtags=True),
            "Smart-Lab Mozgovik premium mirror",
        )
    )
    registry.register(
        StaticSourceConnector(
            "euler",
            SourceCapabilities(pdf=True, subscribers=True, private_mode=True, hashtags=True),
            "Euler Research mirror",
        )
    )
    return registry
