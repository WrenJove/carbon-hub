"""手输适配器：用户在界面上直接填一条活动。

置信度恒为 1.0（人自己填的），是最可靠的来源之一。
"""

from __future__ import annotations

from ..models import ActivityRecord, AdapterCapability, IngestResult, RawInput
from .base import CarbonAdapter


class ManualAdapter(CarbonAdapter):
    capability = AdapterCapability(
        id="manual",
        display_name="手动录入",
        status="connected",
        ingest=("manual",),
        categories=("electricity", "gas", "fuel", "transport", "diet", "shopping", "other"),
        note="用户在界面直接填写，置信度 1.0",
    )

    def ingest(self, raw: RawInput) -> IngestResult:
        """payload 期望是 list[dict]，每个 dict 至少含 occurred_at/category/quantity/unit。"""

        result = IngestResult()
        items = raw.payload if isinstance(raw.payload, list) else [raw.payload]

        for index, item in enumerate(items):
            try:
                record = ActivityRecord(
                    id=str(item.get("id") or f"{raw.source_id}-{index:03d}"),
                    source_id=raw.source_id,
                    occurred_at=str(item["occurred_at"]),
                    category=str(item["category"]),
                    quantity=float(item["quantity"]),
                    unit=str(item["unit"]),
                    confidence=1.0,
                    raw=dict(item),
                )
            except (KeyError, TypeError, ValueError) as exc:
                result.warnings.append(f"第 {index + 1} 条无法录入：{exc}")
                continue
            result.records.append(record)

        return result
