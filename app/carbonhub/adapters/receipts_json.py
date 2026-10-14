"""已识别单据适配器：把「识别结果 JSON」变成活动记录。

这是识别与核算之间的那道门。上游（PDF 文字层解析 / 多模态识别）先把单据读成
这个 JSON，本适配器只做三件事：映射分类、统一单位、按业务键去重。

去重键：电费 = 户号 + 计费周期；加油 = 发票号。
同一户同一月既给了 App 截图又给了电子发票时，只算一次。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import ActivityRecord, AdapterCapability, IngestResult, RawInput
from .base import CarbonAdapter


def _period_end(period: str) -> str:
    """'2026-07-01/2026-07-31' -> '2026-07-31'（账期归到期末）。"""

    parts = [p for p in str(period).split("/") if p]
    return parts[-1] if parts else str(period)


class ReceiptsJsonAdapter(CarbonAdapter):
    capability = AdapterCapability(
        id="receipts_json",
        display_name="已识别单据（JSON）",
        status="connected",
        ingest=("file-json",),
        categories=("electricity", "fuel"),
        note="承接识别结果；同一户同月重复时只计一次",
    )

    def ingest(self, raw: RawInput) -> IngestResult:
        result = IngestResult()
        payload = json.loads(Path(str(raw.payload)).read_text(encoding="utf-8"))
        docs = payload.get("own_documents", payload)
        seen: set[str] = set()

        for entry in docs.get("fuel_receipts", []):
            key = "fuel:" + str(entry.get("invoice_no") or entry.get("file"))
            if key in seen:
                result.warnings.append(f"重复单据已跳过：{key}")
                continue
            seen.add(key)
            result.records.append(
                ActivityRecord(
                    id=key,
                    source_id=self.capability.id,
                    occurred_at=str(entry["date"]),
                    category="fuel",
                    quantity=round(float(entry["quantity"]), 2),
                    unit="L",
                    confidence=1.0,
                    raw={
                        "file": entry.get("file"),
                        "item": entry.get("item"),
                        "amount": entry.get("amount"),
                        "form": entry.get("form"),
                    },
                )
            )

        # 电子发票是文字层解析（准），App 截图是视觉识别（略低）
        for group, confidence in (("utility_bills", 1.0), ("app_screenshots", 0.9)):
            for entry in docs.get(group, []):
                account = str(entry.get("account_no") or "unknown")
                period = str(entry.get("period") or "")
                key = f"elec:{account}:{period}"
                if key in seen:
                    result.warnings.append(f"同一户同月重复，已跳过：{account} {period}")
                    continue
                seen.add(key)
                result.records.append(
                    ActivityRecord(
                        id=key,
                        source_id=self.capability.id,
                        occurred_at=_period_end(period),
                        category="electricity",
                        quantity=float(entry["kwh"]),
                        unit="kWh",
                        confidence=confidence,
                        raw={
                            "file": entry.get("file"),
                            "account_no": account,
                            "bill_amount": entry.get("bill_amount"),
                            "source_form": entry.get("form") or entry.get("note"),
                        },
                    )
                )

        return result
