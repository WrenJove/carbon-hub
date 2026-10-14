"""适配器基类与注册表。

适配器只负责"把素材读成 ActivityRecord"，绝不自己算排放。
新增一个数据源 = 新增一个子类 + 注册，内核一行不改。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AdapterCapability, IngestResult, RawInput


class CarbonAdapter(ABC):
    capability: AdapterCapability

    @abstractmethod
    def ingest(self, raw: RawInput) -> IngestResult:
        """素材 -> 统一活动记录。认不出的行进 warnings，不静默丢弃。"""


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, CarbonAdapter] = {}

    def register(self, adapter: CarbonAdapter) -> CarbonAdapter:
        self._adapters[adapter.capability.id] = adapter
        return adapter

    def get(self, adapter_id: str) -> CarbonAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise KeyError(f"未注册的适配器: {adapter_id}") from exc

    def all(self) -> list[CarbonAdapter]:
        return list(self._adapters.values())

    def catalogue(self) -> list[dict]:
        """给界面用的清单：已接入 / 可接入 / 待接入。"""

        return [
            {
                "id": a.capability.id,
                "name": a.capability.display_name,
                "status": a.capability.status,
                "ingest": list(a.capability.ingest),
                "categories": list(a.capability.categories),
                "note": a.capability.note,
            }
            for a in self._adapters.values()
        ]
