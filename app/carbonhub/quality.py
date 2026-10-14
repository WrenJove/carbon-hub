"""数据质量分：把内部的置信度机制变成用户看得见的一个数。

分数只回答一个问题：这份账本有多可信、补什么能更可信。
它不参与排放量计算，纯粹是给用户看的判据。

纯函数，可离线测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .models import ActivityRecord, CarbonLedger

LOW_CONFIDENCE = 0.7


@dataclass(frozen=True)
class QualityPart:
    name: str
    earned: float
    full: float
    note: str


@dataclass(frozen=True)
class QualityReport:
    score: int
    level: str
    parts: tuple[QualityPart, ...] = ()
    hints: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "level": self.level,
            "parts": [
                {"name": p.name, "earned": round(p.earned, 1), "full": p.full, "note": p.note}
                for p in self.parts
            ],
            "hints": list(self.hints),
        }


def assess(
    activities: Sequence[ActivityRecord],
    ledger: CarbonLedger,
    warnings: Sequence[str] = (),
) -> QualityReport:
    if not activities:
        return QualityReport(score=0, level="无数据", hints=("先载入至少一份单据。",))

    parts: list[QualityPart] = []
    hints: list[str] = []

    # 1) 平均置信度，权重 50
    mean_conf = sum(a.confidence for a in activities) / len(activities)
    parts.append(QualityPart("识别置信度", mean_conf * 50, 50.0, f"平均 {mean_conf:.2f}"))
    if mean_conf < 0.9:
        hints.append("有几条记录的置信度偏低，点开「查看原件」对着原图核对一下能提分。")

    # 2) 低置信度占比，权重 30
    low = [a for a in activities if a.confidence < LOW_CONFIDENCE]
    low_ratio = len(low) / len(activities)
    parts.append(QualityPart(
        "待复核比例", (1 - low_ratio) * 30, 30.0, f"{len(low)}/{len(activities)} 条待复核"
    ))
    if low:
        hints.append(f"有 {len(low)} 条待复核记录，多半来自按金额反推的估算，补一张原始单据就能消掉。")

    # 3) 因子年份匹配，权重 10
    matched = 0
    for record in ledger.emissions:
        activity = next((a for a in activities if a.id == record.activity_id), None)
        if activity is None:
            continue
        year = str(activity.occurred_at)[:4]
        if year.isdigit() and record.factor_year == int(year):
            matched += 1
    match_ratio = matched / len(ledger.emissions) if ledger.emissions else 1.0
    parts.append(QualityPart(
        "因子年份匹配", match_ratio * 10, 10.0, f"{matched}/{len(ledger.emissions)} 条同年匹配"
    ))
    if match_ratio < 1:
        hints.append("部分单据的年份在因子库里没有对应值，报告里要注明用了哪一版因子。")

    # 4) 数据完整性，权重 10
    earned = 10.0 if not warnings else max(4.0, 10.0 - 2.0 * len(warnings))
    parts.append(QualityPart("数据完整性", earned, 10.0, f"{len(warnings)} 条提示"))
    if warnings:
        hints.append("有单据没被完整读取，看一下「需要人工确认」那一段。")

    score = int(round(sum(p.earned for p in parts)))
    level = "高" if score >= 85 else ("中" if score >= 65 else "低")
    return QualityReport(score=score, level=level, parts=tuple(parts), hints=tuple(hints))
