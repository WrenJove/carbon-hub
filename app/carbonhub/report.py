"""静态报告：把一份账本渲染成单个自包含的 HTML 文件。

为什么要它：让不在同一网络的人看到结果，最省事的办法不是开公网，而是发个文件。
不依赖服务器、不依赖网络、不依赖任何外部资源（CSS 内联、图表用 div 画），
微信里发过去双击就能看，也可以直接打印成 PDF 交给评审。

用法：
    python -m carbonhub.report --receipts ../fixtures/real_receipts_20261013.json
"""

from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path

from .advice import frontier, humanize, plan
from .factors import FactorLibrary
from .models import ActivityRecord, CarbonLedger, CarbonPassport
from .quality import QualityReport

CATEGORY_LABEL = {
    "electricity": "用电", "gas": "燃气", "fuel": "汽油", "transport": "出行",
    "flight": "航空", "delivery": "快递外卖", "diet": "饮食", "water": "用水",
    "shopping": "购物", "waste": "垃圾回收", "other": "其他",
}

CSS = """
:root { --ink:#1f2a24; --muted:#63736a; --line:#e3e8e4; --brand:#2f6f4e; --warn:#b26a00; }
* { box-sizing: border-box; }
body { margin:0; padding:36px 24px; background:#f7f9f7; color:var(--ink);
  font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
.wrap { max-width:940px; margin:0 auto; }
h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:17px; margin:32px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--line); }
.sub { color:var(--muted); font-size:13px; margin-bottom:4px; }
.cards { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0 4px; }
.card { flex:1 1 180px; border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px 14px; }
.card .k { color:var(--muted); font-size:12px; }
.card .v { font-size:22px; font-weight:600; margin-top:2px; }
table { width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line);
  border-radius:8px; overflow:hidden; font-size:13px; }
th,td { padding:7px 10px; text-align:left; border-bottom:1px solid var(--line); }
th { background:#f2f5f3; font-weight:600; color:var(--muted); }
tr:last-child td { border-bottom:none; }
.flag { background:#fff6e5; }
.bar { height:12px; background:var(--brand); border-radius:2px; }
.barwrap { display:flex; align-items:center; gap:10px; margin:6px 0; }
.barlabel { width:70px; color:var(--muted); font-size:13px; }
.barval { color:var(--muted); font-size:13px; width:96px; }
.note { color:var(--muted); font-size:12px; }
.item { border:1px solid var(--line); border-radius:8px; background:#fff; padding:12px 14px; margin:10px 0; }
.tag { display:inline-block; font-size:11px; padding:1px 7px; border-radius:10px;
  background:#eef4f0; color:var(--brand); margin-left:6px; }
.mono { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:var(--muted); word-break:break-all; }
footer { margin-top:36px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); padding-top:12px; }
"""


def _esc(text: object) -> str:
    return html.escape(str(text))


def _bars(by_category: dict[str, float]) -> str:
    if not by_category:
        return "<p class='note'>没有分类数据。</p>"
    top = max(by_category.values()) or 1.0
    rows = []
    for category, value in sorted(by_category.items(), key=lambda kv: -kv[1]):
        width = max(2, round(value / top * 320))
        rows.append(
            f"<div class='barwrap'><div class='barlabel'>{_esc(CATEGORY_LABEL.get(category, category))}</div>"
            f"<div class='bar' style='width:{width}px'></div>"
            f"<div class='barval'>{value:.1f} kg</div></div>"
        )
    return "".join(rows)


def render_html(
    ledger: CarbonLedger,
    activities: list[ActivityRecord],
    quality: QualityReport,
    plan_result,
    passport: CarbonPassport,
    warnings: list[str],
    generated_at: str,
) -> str:
    by_id = {a.id: a for a in activities}

    rows = "".join(
        f"<tr class='{'flag' if by_id[e.activity_id].confidence < 0.7 else ''}'>"
        f"<td>{_esc(by_id[e.activity_id].occurred_at)}</td>"
        f"<td>{_esc(CATEGORY_LABEL.get(by_id[e.activity_id].category, by_id[e.activity_id].category))}</td>"
        f"<td>{by_id[e.activity_id].quantity:.2f} {_esc(by_id[e.activity_id].unit)}</td>"
        f"<td>{e.kgco2e:.3f}</td><td>{e.scope}</td>"
        f"<td>{by_id[e.activity_id].confidence:.2f}</td>"
        f"<td>{_esc(e.factor_id)}</td><td>{e.factor_year}</td></tr>"
        for e in ledger.emissions
    )

    steps = "".join(
        f"<tr><td>{s.cost_yuan:,.0f} 元</td><td>{s.reduction_kg:.1f} kg</td>"
        f"<td>{', '.join(s.ids)}</td></tr>"
        for s in frontier(ledger, activities, max_actions=8)
    )

    items = "".join(
        f"<div class='item'><b>{_esc(c.title)}</b><span class='tag'>{_esc(c.kind)}类</span>"
        f"<div>{_esc(c.action)}</div>"
        f"<div class='note'>每年减 {c.reduction_kg:.1f} kgCO2e · 边际成本 {c.marginal_cost:+.2f} 元/kg ·"
        f" 为什么推荐：{_esc(c.rationale)}</div>"
        f"<div class='note'>假设：{_esc(c.assumption)}</div></div>"
        for c in plan_result.chosen
    )

    profile_rows = "".join(
        f"<tr><td>{_esc(CATEGORY_LABEL.get(p.category, p.category))}</td><td>{p.share_percent}%</td>"
        f"<td>{p.monthly}</td><td>{_esc(p.band)}</td><td>{_esc(p.verdict)}</td></tr>"
        for p in plan_result.profile
    )

    warn_block = (
        "<h2>需要人工确认</h2><ul class='note'>"
        + "".join(f"<li>{_esc(w)}</li>" for w in warnings) + "</ul>"
        if warnings else ""
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>carbon-hub 碳账本报告</title><style>{CSS}</style></head>
<body><div class="wrap">

<h1>carbon-hub 碳账本报告</h1>
<div class="sub">主体：{_esc(ledger.subject)} · 账期：{_esc(ledger.period_from)} 至 {_esc(ledger.period_to)}
 · 生成于 {_esc(generated_at)}</div>

<h2>一、排放总览</h2>
<div class="cards">
  <div class="card"><div class="k">合计</div><div class="v">{ledger.totals['total']:.1f} kgCO2e</div></div>
  <div class="card"><div class="k">范围一 · 自己烧的</div><div class="v">{ledger.totals['scope1']:.1f}</div></div>
  <div class="card"><div class="k">范围二 · 买的电与热</div><div class="v">{ledger.totals['scope2']:.1f}</div></div>
  <div class="card"><div class="k">范围三 · 供应链</div><div class="v">{ledger.totals['scope3']:.1f}</div></div>
</div>
<div class="note">换算：{_esc(humanize(ledger.totals['total']))}</div>

<h2>二、分类目</h2>
{_bars(ledger.by_category)}

<h2>三、明细</h2>
<table><thead><tr><th>日期</th><th>类别</th><th>数量</th><th>排放 kgCO2e</th><th>范围</th>
<th>置信度</th><th>因子</th><th>因子年份</th></tr></thead><tbody>{rows}</tbody></table>
<div class="note">浅黄底表示置信度低于 0.7，建议人工核对原件。</div>

<h2>四、数据质量</h2>
<div class="cards"><div class="card"><div class="k">质量分</div>
<div class="v">{quality.score}/100（{_esc(quality.level)}）</div></div></div>
<ul class="note">{''.join(f'<li>{_esc(p.name)}：{p.earned:.0f}/{p.full:.0f} · {_esc(p.note)}</li>' for p in quality.parts)}</ul>

<h2>五、用量画像</h2>
<table><thead><tr><th>类别</th><th>占比</th><th>月均</th><th>参考区间</th><th>判断</th></tr></thead>
<tbody>{profile_rows}</tbody></table>

<h2>六、减排规划</h2>
<div class="note">钱只在能买下某件东西的几个价格点上起作用，中间是平台区。</div>
<table><thead><tr><th>投入档位</th><th>每年可减</th><th>动作</th></tr></thead><tbody>{steps}</tbody></table>
{items}
<div class="note">{''.join(_esc(n) + '　' for n in plan_result.notes)}</div>

{warn_block}

<h2>七、凭证指纹</h2>
<div class="note">规范版本 {_esc(passport.spec_version)} · 条目 {len(passport.entries)} 条 ·
签发 {_esc(passport.issued_at)}</div>
<div class="mono">root = {_esc(passport.ledger_root)}</div>
<div class="note">改任何一个数字，重算的根就对不上。校验命令：python -m carbonhub.verify out/passport.json</div>

<footer>
方法学：{_esc(ledger.standard)} · 因子库 {_esc(ledger.factors_version)}<br>
排放因子取自生态环境部与国家统计局的年度公告（电力）及公开排放系数集；参考区间为经验值，非官方标准。<br>
报告由 carbon-hub 生成，单文件离线可用。
</footer>
</div></body></html>"""


def build_report(
    receipts: Path | None, out: Path, factors: Path, subject: str, period: tuple[str, str]
) -> Path:
    from .adapters import default_registry
    from .kernel import build_ledger, issue_passport
    from .models import RawInput

    library = FactorLibrary.from_csv(factors, version="official-2021-2023")
    adapter = default_registry().get("receipts_json")
    result = adapter.ingest(RawInput(source_id="receipts_json", kind="json",
                                     payload=str(receipts), captured_at=datetime.now().strftime("%Y-%m-%d")))
    activities = list(result.records)
    ledger = build_ledger(subject, activities, library, period[0], period[1])
    passport = issue_passport(ledger, activities, issued_at=datetime.now().isoformat(timespec="seconds"))

    from .quality import assess
    quality = assess(activities, ledger, result.warnings)
    plan_result = plan(ledger, activities, max_actions=8)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_html(ledger, activities, quality, plan_result, passport, list(result.warnings),
                    datetime.now().strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8",
    )
    return out


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="生成静态 HTML 报告")
    parser.add_argument("--receipts", type=Path, default=root / "fixtures" / "real_receipts_20261013.json")
    parser.add_argument("--factors", type=Path, default=root / "fixtures" / "factors_seed.csv")
    parser.add_argument("--out", type=Path, default=Path("out/report.html"))
    parser.add_argument("--subject", default="我的碳账本")
    parser.add_argument("--period", default="2026-07-01:2026-10-31")
    args = parser.parse_args(argv)

    path = build_report(args.receipts, args.out, args.factors, args.subject,
                        tuple(args.period.split(":")))
    print(f"报告已生成：{path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
