"""假数据适配器：给"待接入"的平台生成合理量级的演示数据。

用途只有一个：比赛现场展示架构延展性。产出必须带标记，绝不能被
当成真实数据混进账本。
"""

from __future__ import annotations

import random

from ..models import ActivityRecord, AdapterCapability, IngestResult, RawInput
from .base import CarbonAdapter

_MOCK_SOURCES: tuple[tuple[str, str, str, float, float], ...] = (
    ("wechat_sport", "wx_metro", "transport", 6.0, 18.0),   # 地铁通勤 公里
    ("ant_forest", "af_metro_ride", "transport", 4.0, 12.0),
    ("ride_sharing", "rs_bike", "transport", 1.0, 6.0),
    ("bank_carbon", "bc_green_energy", "electricity", 8.0, 30.0),
)


class MockAdapter(CarbonAdapter):
    capability = AdapterCapability(
        id="mock",
        display_name="演示用假数据",
        status="planned",
        ingest=("api",),
        categories=("electricity", "transport"),
        note="不是真实数据，仅用于演示「待接入」平台的架构延展性",
    )

    def __init__(self, seed: int = 20261031, days: int = 7) -> None:
        self.seed = seed
        self.days = days

    def ingest(self, raw: RawInput) -> IngestResult:
        rng = random.Random(self.seed)
        result = IngestResult()

        for source_id, prefix, category, low, high in _MOCK_SOURCES:
            for day in range(self.days):
                quantity = round(rng.uniform(low, high), 2)
                result.records.append(
                    ActivityRecord(
                        id=f"{prefix}-{day:02d}",
                        source_id=source_id,
                        occurred_at=f"2026-10-{day + 1:02d}",
                        category=category,
                        quantity=quantity,
                        unit="km" if category == "transport" else "kWh",
                        confidence=0.5,
                        raw={"mock": True, "quantity": quantity, "unit": "km/kWh"},
                    )
                )

        result.warnings.append("以上为演示用假数据，不要计入真实账本")
        return result
