"""核算内核：L3 核算 / L4 记账 / L5 出具。

纯函数，不联网、不读文件、不依赖模型。给同样的输入永远得到同样的输出，
这是整个项目里唯一需要"绝对可信"的部分。
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Sequence

from .factors import FactorLibrary
from .models import (
    ActivityRecord,
    CarbonLedger,
    CarbonPassport,
    EmissionRecord,
    PassportEntry,
)

STANDARD = "GHG Protocol (Scope 1/2/3 简化归集)"
PASSPORT_SPEC_VERSION = "carbon-passport/0.2"


def _year_of(stamp: str) -> int | None:
    """从 '2017-07-31' 里取出 2017；取不出就返回 None（让因子库按最新值兜底）。"""

    head = str(stamp)[:4]
    return int(head) if head.isdigit() else None

def calculate(activities: Iterable[ActivityRecord], library: FactorLibrary) -> list[EmissionRecord]:
    """活动量 × 排放因子。一行公式，但因子必须匹配到，匹配不到就抛错。"""

    results: list[EmissionRecord] = []
    for activity in activities:
        # 按活动发生的年份找因子：2017 年的电该配 2017 年前后的电网因子，
        # 而不是库里的最新值。库里缺那一年就就近取，年份照实记下来。
        factor = library.match(
            activity.category, activity.unit, year=_year_of(activity.occurred_at)
        )
        results.append(
            EmissionRecord(
                activity_id=activity.id,
                kgco2e=activity.quantity * factor.factor,
                scope=factor.scope,
                factor_id=factor.id,
                factor_year=factor.year,
            )
        )
    return results


def build_ledger(
    subject: str,
    activities: Sequence[ActivityRecord],
    library: FactorLibrary,
    period_from: str,
    period_to: str,
) -> CarbonLedger:
    """汇总成账本：分范围、分类目，每条都能回溯到活动与因子。"""

    emissions = calculate(activities, library)
    by_id = {a.id: a for a in activities}

    totals = {"scope1": 0.0, "scope2": 0.0, "scope3": 0.0, "total": 0.0}
    by_category: dict[str, float] = {}

    for record in emissions:
        totals[f"scope{record.scope}"] += record.kgco2e
        totals["total"] += record.kgco2e
        category = by_id[record.activity_id].category
        by_category[category] = by_category.get(category, 0.0) + record.kgco2e

    return CarbonLedger(
        subject=subject,
        period_from=period_from,
        period_to=period_to,
        totals={k: round(v, 3) for k, v in totals.items()},
        by_category={k: round(v, 3) for k, v in sorted(by_category.items())},
        emissions=tuple(emissions),
        standard=STANDARD,
        factors_version=library.version,
    )


def _evidence_hash(activity: ActivityRecord) -> str:
    """对活动记录做指纹，回答"凭什么信这份账"。"""

    material = "|".join(
        [
            activity.id,
            activity.source_id,
            activity.occurred_at,
            activity.category,
            f"{activity.quantity:g}",
            activity.unit,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def entry_hash(entry: PassportEntry) -> str:
    """一条凭证记录的内容指纹。字段顺序固定，改任何一个都会变。"""

    material = "|".join([
        entry.activity_id,
        entry.source_id,
        entry.occurred_at,
        entry.category,
        f"{entry.kgco2e:.6f}",
        str(entry.scope),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def merkle_root(hashes: Sequence[str]) -> str:
    """把多条记录的指纹折成一个根。任何一条被改，根就对不上。

    相邻两两合并，落单的直接升到上一层（比补齐一对更好解释）。
    """

    layer = list(hashes)
    if not layer:
        return hashlib.sha256(b"empty").hexdigest()
    while len(layer) > 1:
        merged: list[str] = []
        for i in range(0, len(layer), 2):
            if i + 1 < len(layer):
                merged.append(hashlib.sha256((layer[i] + layer[i + 1]).encode("utf-8")).hexdigest())
            else:
                merged.append(layer[i])
        layer = merged
    return layer[0]


def issue_passport(
    ledger: CarbonLedger,
    activities: Sequence[ActivityRecord],
    issued_at: str,
) -> CarbonPassport:
    """把账本折成一份可对外交换的凭证。"""

    by_id = {a.id: a for a in activities}
    entries = tuple(
        PassportEntry(
            activity_id=record.activity_id,
            source_id=by_id[record.activity_id].source_id,
            occurred_at=by_id[record.activity_id].occurred_at,
            category=by_id[record.activity_id].category,
            # 这里就四舍五入到 3 位：凭证是要序列化出去的，指纹必须按
            # 序列化后的值算，否则签发的凭证自己都验不过
            kgco2e=round(record.kgco2e, 3),
            scope=record.scope,
        )
        for record in ledger.emissions
    )

    return CarbonPassport(
        spec_version=PASSPORT_SPEC_VERSION,
        subject=ledger.subject,
        period_from=ledger.period_from,
        period_to=ledger.period_to,
        total_kgco2e=ledger.totals["total"],
        entries=entries,
        evidence_hashes=tuple(_evidence_hash(a) for a in activities),
        issued_at=issued_at,
        ledger_root=merkle_root([entry_hash(e) for e in entries]),
    )
