"""端到端命令行：把示例素材（可选真实单据）跑成一份碳账本和一份碳护照。

用法（在 app 目录下）：
    python -m carbonhub.cli
    python -m carbonhub.cli --receipts ../fixtures/real_receipts_20261013.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import default_registry
from .advice import humanize, plan
from .quality import assess
from .factors import FactorLibrary
from .kernel import build_ledger, issue_passport
from .models import RawInput

MANUAL_ACTIVITIES = [
    {"id": "manual-elec-0920", "occurred_at": "2026-09-30", "category": "electricity",
     "quantity": 320, "unit": "kWh"},
    {"id": "manual-fuel-0918", "occurred_at": "2026-09-18", "category": "fuel",
     "quantity": 40, "unit": "L"},
]


def guess_fixtures() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="carbon-hub 端到端演示")
    parser.add_argument("--fixtures", type=Path, default=None, help="示例数据目录")
    parser.add_argument("--out", type=Path, default=Path("out"), help="输出目录")
    parser.add_argument("--subject", default="demo-user", help="账本主体标识")
    parser.add_argument("--receipts", type=Path, default=None,
                        help="已识别单据的 JSON；给了就只跑真实数据，不跑示例")
    parser.add_argument("--budget", type=float, default=None,
                        help="减排规划的一次性投入上限（元）；不给就不限制")
    parser.add_argument("--cap", type=float, default=0.4,
                        help="单个类别最多能砍掉该类年度排放的比例，默认 0.4")
    parser.add_argument("--kinds", default="设备,行为",
                        help="允许的动作类型：设备=一次性动作，行为=需要改日常习惯；逗号分隔")
    parser.add_argument("--max-actions", type=int, default=None,
                        help="减排规划最多选几项；不给就不限制")
    parser.add_argument("--images", type=Path, nargs="+", default=None,
                        help="单据图片；走多模态识别。需要 CARBONHUB_VISION_API_KEY")
    parser.add_argument("--period", default=None, help="账期，格式 2026-07-01:2026-09-30")
    args = parser.parse_args(argv)

    fixtures = args.fixtures or guess_fixtures()
    library = FactorLibrary.from_csv(fixtures / "factors_seed.csv", version="official-2021-2023")
    registry = default_registry()

    jobs = []
    if args.images:
        jobs.append(("utility_bill", [str(p) for p in args.images], "image"))
        period_from, period_to = ("2026-07-01", "2026-12-31")
        subject = "图片识别"
    elif args.receipts:
        jobs.append(("receipts_json", str(args.receipts), "json"))
        period_from, period_to = ("2026-07-01", "2026-10-31")
        subject = "示例用户（真实单据）"
    else:
        jobs.append(("manual", MANUAL_ACTIVITIES, "manual"))
        jobs.append(("alipay_bill", str(fixtures / "alipay_bill_sample.csv"), "csv"))
        period_from, period_to = ("2026-09-01", "2026-09-30")
        subject = args.subject

    if args.period:
        period_from, period_to = args.period.split(":")

    collected = []
    warnings: list[str] = []
    for adapter_id, payload, kind in jobs:
        result = registry.get(adapter_id).ingest(
            RawInput(source_id=adapter_id, kind=kind, payload=payload, captured_at="2026-10-14")
        )
        collected.extend(result.records)
        warnings.extend(f"[{adapter_id}] {w}" for w in result.warnings)

    ledger = build_ledger(
        subject=subject,
        activities=collected,
        library=library,
        period_from=period_from,
        period_to=period_to,
    )
    passport = issue_passport(ledger, collected, issued_at="2026-10-14T00:00:00+08:00")

    print(f"数据来源：{', '.join(j[0] for j in jobs)}")
    print(f"活动记录 {len(collected)} 条")
    print(f"排放合计 {ledger.totals['total']:.3f} kgCO2e"
          f"（范围一 {ledger.totals['scope1']:.3f} / 范围二 {ledger.totals['scope2']:.3f}"
          f" / 范围三 {ledger.totals['scope3']:.3f}）")
    print("分类目：")
    for category, value in ledger.by_category.items():
        print(f"  {category:<12}{value:>9.3f} kgCO2e")
    print("逐条：")
    by_id = {a.id: a for a in collected}
    for record in ledger.emissions:
        activity = by_id[record.activity_id]
        print(f"  {activity.occurred_at}  {activity.category:<12}"
              f"{activity.quantity:>8.2f} {activity.unit:<5}"
              f"{record.kgco2e:>9.3f} kg  scope{record.scope}  conf={activity.confidence}")

    if warnings:
        print("需要人工确认 / 已跳过：")
        for warning in warnings:
            print(f"  - {warning}")

    advice_plan = plan(
        ledger, collected,
        budget_yuan=args.budget, max_actions=args.max_actions,
        category_cap_ratio=args.cap,
        allowed_kinds=tuple(e.strip() for e in args.kinds.split(",") if e.strip()),
    )
    quality = assess(collected, ledger, warnings)

    print(f"数据质量分 {quality.score}/100（{quality.level}）")

    if advice_plan.profile:
        print("用量画像（参考区间为经验值）：")
        for note in advice_plan.profile:
            print(f"  {note.category:<12} 占比 {note.share_percent:>5}%  "
                  f"月均 {note.monthly:>8}  {note.verdict}  参考 {note.band}")

    for note in advice_plan.notes:
        print(f"  ！{note}")

    if advice_plan.chosen:
        print(f"减排规划：选中 {len(advice_plan.chosen)} 项，合计减 {advice_plan.total_reduction_kg} kg"
              f"（不加约束的上限 {advice_plan.ceiling_kg} kg）")
        for item in advice_plan.chosen:
            print(f"  - {item.title}：{item.reduction_kg:.1f} kg  "
                  f"边际成本 {item.marginal_cost:+.2f} 元/kg  [{item.kind}类]")
        direction = "省" if advice_plan.total_annual_delta < 0 else "多花"
        print(f"  合计一次性投入 {advice_plan.total_setup_cost:.0f} 元，"
              f"每年{direction} {abs(advice_plan.total_annual_delta):.0f} 元")
        print(f"  换算：{humanize(advice_plan.total_reduction_kg)}")

    args.out.mkdir(parents=True, exist_ok=True)
    outputs = {
        "passport.json": passport.to_dict(),
        "ledger.json": {
            "subject": ledger.subject,
            "period": {"from": ledger.period_from, "to": ledger.period_to},
            "totals": ledger.totals,
            "byCategory": ledger.by_category,
            "emissions": [
                {"activityId": e.activity_id, "kgCO2e": round(e.kgco2e, 3),
                 "scope": e.scope, "factorId": e.factor_id, "factorYear": e.factor_year}
                for e in ledger.emissions
            ],
            "methodology": {"standard": ledger.standard, "factorsVersion": ledger.factors_version},
        },
        "advice.json": {
            "profile": [
                {"category": n.category, "sharePercent": n.share_percent, "monthly": n.monthly,
                 "band": n.band, "verdict": n.verdict}
                for n in advice_plan.profile
            ],
            "chosen": [
                {"id": c.id, "title": c.title, "action": c.action, "category": c.category,
                 "reductionKgCO2e": round(c.reduction_kg, 2),
                 "setupCostYuan": c.setup_cost_yuan,
                 "annualDeltaYuan": round(c.annual_delta_yuan, 1),
                 "marginalCostPerKg": round(c.marginal_cost, 2),
                 "kind": c.kind, "assumption": c.assumption, "rationale": c.rationale}
                for c in advice_plan.chosen
            ],
            "ceilingKgCO2e": advice_plan.ceiling_kg,
            "totals": {
                "reductionKgCO2e": advice_plan.total_reduction_kg,
                "setupCostYuan": advice_plan.total_setup_cost,
                "annualDeltaYuan": advice_plan.total_annual_delta,
            },
            "quality": quality.as_dict(),
        },
    }
    for name, payload in outputs.items():
        path = args.out / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
