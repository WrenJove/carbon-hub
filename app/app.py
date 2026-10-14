"""carbon-hub 演示界面。

跑法（在 app 目录下）：
    .venv\\Scripts\\streamlit run app.py --server.address 127.0.0.1 --server.port 8501

界面只负责"组织流程"和"把结果显示清楚"，所有计算都调 carbonhub 里的函数，
这里不做任何业务逻辑，免得界面和内核各算一套。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from carbonhub.advice import KIND_RULE, frontier, humanize, plan  # noqa: E402
from carbonhub.adapters import default_registry  # noqa: E402
from carbonhub.quality import assess  # noqa: E402
from carbonhub.report import render_html  # noqa: E402
from carbonhub.factors import FactorLibrary  # noqa: E402
from carbonhub.kernel import build_ledger, issue_passport  # noqa: E402
from carbonhub.models import RawInput  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
FIXTURES = ROOT / "fixtures"
SAMPLES = FIXTURES / "samples"
UPLOADS = APP_DIR / "out" / "uploads"
LOW_CONFIDENCE = 0.7
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".webp"}

CATEGORY_LABEL = {
    "electricity": "用电",
    "gas": "燃气",
    "fuel": "汽油",
    "transport": "出行",
    "flight": "航空",
    "delivery": "快递外卖",
    "diet": "饮食",
    "water": "用水",
    "shopping": "购物",
    "waste": "垃圾回收",
    "other": "其他",
}

SCOPE_LABEL = {1: "范围一 · 自己烧的", 2: "范围二 · 买的电与热", 3: "范围三 · 供应链"}

st.set_page_config(page_title="carbon-hub", page_icon="🌱", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1200px;}
      h1 {font-size: 1.9rem; margin-bottom: .2rem;}
      h2, h3 {margin-top: 1.2rem;}
      [data-testid="stMetric"] {border: 1px solid #e6eae7; border-radius: 8px;
                                padding: .7rem .9rem; background: #fbfcfb;}
      [data-testid="stMetricLabel"] {font-size: .82rem; color: #5b6b63;}
      .ch-sub {color: #5b6b63; font-size: .88rem; margin-bottom: .6rem;}
      .stTabs [data-baseweb="tab"] {padding: .4rem .9rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 加载数据

def registry():
    return default_registry()


def collect_from_images(paths: list[str]) -> tuple[list, list[str]]:
    result = registry().get("utility_bill").ingest(
        RawInput(source_id="utility_bill", kind="image", payload=paths, captured_at=today())
    )
    return list(result.records), list(result.warnings)


def collect_from_csv(path: str) -> tuple[list, list[str]]:
    result = registry().get("alipay_bill").ingest(
        RawInput(source_id="alipay_bill", kind="csv", payload=path, captured_at=today())
    )
    return list(result.records), list(result.warnings)


def collect_from_json(path: str) -> tuple[list, list[str]]:
    result = registry().get("receipts_json").ingest(
        RawInput(source_id="receipts_json", kind="json", payload=path, captured_at=today())
    )
    return list(result.records), list(result.warnings)


def dispatch(paths: list[str]) -> tuple[list, list[str], list[str]]:
    """按扩展名把文件分派给对应适配器，用户不用先选类别。"""

    by_kind: dict[str, list[str]] = {"image": [], "csv": [], "json": []}
    skipped: list[str] = []
    for raw_path in paths:
        suffix = Path(raw_path).suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            by_kind["image"].append(raw_path)
        elif suffix == ".csv":
            by_kind["csv"].append(raw_path)
        elif suffix == ".json":
            by_kind["json"].append(raw_path)
        else:
            skipped.append(Path(raw_path).name)

    records: list = []
    warnings: list[str] = []
    for path in by_kind["image"]:
        got, noted = collect_from_images([path])
        records += got
        warnings += noted
    for path in by_kind["csv"]:
        got, noted = collect_from_csv(path)
        records += got
        warnings += noted
    for path in by_kind["json"]:
        got, noted = collect_from_json(path)
        records += got
        warnings += noted
    if skipped:
        warnings.append("认不出这些文件的类型，已跳过：" + "、".join(skipped))
    return records, warnings, skipped


def store_uploads(files) -> list[str]:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    saved = []
    for uploaded in files:
        target = UPLOADS / uploaded.name
        target.write_bytes(uploaded.getbuffer())
        saved.append(str(target))
    return saved


def run_pipeline(records: list, warnings: list[str], subject: str, period: tuple[str, str]) -> dict:
    library = FactorLibrary.from_csv(FIXTURES / "factors_seed.csv", version="official-2021-2023")
    ledger = build_ledger(subject, records, library, period[0], period[1])
    passport = issue_passport(ledger, records, issued_at=datetime.now().isoformat(timespec="seconds"))
    return {"records": records, "warnings": warnings, "ledger": ledger,
            "passport": passport, "library": library}


# ---------------------------------------------------------------- 结果渲染

def render_metrics(ledger) -> None:
    totals = ledger.totals
    columns = st.columns(4)
    labels = [("合计", f"{totals['total']:.1f}"), ("范围一 · 自己烧的", f"{totals['scope1']:.1f}"),
              ("范围二 · 买的电与热", f"{totals['scope2']:.1f}"), ("范围三 · 供应链", f"{totals['scope3']:.1f}")]
    for column, (label, value) in zip(columns, labels):
        column.metric(label, f"{value} kgCO2e")


def render_breakdown(ledger) -> None:
    if not ledger.by_category:
        return
    frame = pd.DataFrame(
        [{"类别": CATEGORY_LABEL.get(k, k), "kgCO2e": round(v, 1)}
         for k, v in sorted(ledger.by_category.items(), key=lambda kv: -kv[1])]
    )
    chart = (
        alt.Chart(frame)
        .mark_bar(color="#2f6f4e", size=22)
        .encode(
            x=alt.X("kgCO2e:Q", title=None),
            y=alt.Y("类别:N", sort="-x", title=None),
            tooltip=["类别:N", "kgCO2e:Q"],
        )
        .properties(height=max(110, 40 * len(frame)))
    )
    st.altair_chart(chart, use_container_width=True)


def build_rows(result: dict) -> list[dict]:
    by_id = {a.id: a for a in result["records"]}
    rows = []
    for record in result["ledger"].emissions:
        activity = by_id[record.activity_id]
        rows.append({
            "日期": activity.occurred_at,
            "类别": CATEGORY_LABEL.get(activity.category, activity.category),
            "数量": round(activity.quantity, 2),
            "单位": activity.unit,
            "排放 kgCO2e": round(record.kgco2e, 3),
            "范围": record.scope,
            "置信度": activity.confidence,
            "待复核": "需复核" if activity.confidence < LOW_CONFIDENCE else "",
            "来源": activity.source_id,
            "因子": record.factor_id,
            "因子年份": record.factor_year,
            "原件": (activity.raw or {}).get("file") or (activity.raw or {}).get("source_form") or "",
        })
    return rows


def render_table(rows: list[dict]) -> None:
    frame = pd.DataFrame(rows)
    styled = frame.style.apply(
        lambda row: ["background-color: #fff3cd" if row["待复核"] else "" for _ in row], axis=1
    ).format({"数量": "{:.2f}", "排放 kgCO2e": "{:.3f}", "置信度": "{:.2f}"})
    st.dataframe(styled, width="stretch", hide_index=True)

    pending = int((frame["待复核"] != "").sum())
    if pending:
        st.warning(f"有 {pending} 条记录置信度偏低，已在表里标黄，建议人工确认。")

    # 用更新的因子去算更早的账，才是口径问题；新单子用最新因子属于正常做法
    gaps = [
        row for row in rows
        if row["因子年份"] and str(row["日期"])[:4].isdigit()
        and row["因子年份"] > int(str(row["日期"])[:4])
    ]
    if gaps:
        st.info(
            f"有 {len(gaps)} 条记录用的是比单据年份更新的排放因子（见表里「因子年份」列）。"
            "因子库覆盖 2021 到 2023 年，更早的年份没有官方值、只能就近取，"
            "这类跨年推算建议在报告里注明。"
        )


def render_source_viewer(rows: list[dict]) -> None:
    """点开看原件：能读到的图片直接显示，读不到的说明原因。"""

    labelled = [r for r in rows if r["原件"]]
    if not labelled:
        return

    with st.expander(f"查看原件（{len(set(r['原件'] for r in labelled))} 份）", expanded=False):
        options = sorted({r["原件"] for r in labelled})
        chosen = st.selectbox("选择要核对的原件", options, key="source-pick")
        candidates = [Path(chosen), SAMPLES / "own" / chosen, SAMPLES / "extra" / chosen,
                      SAMPLES / "public" / chosen, UPLOADS / chosen, FIXTURES / chosen]
        found = next((p for p in candidates if p.exists()), None)

        related = [r for r in labelled if r["原件"] == chosen]
        st.caption("来自这份原件的记录：" + "；".join(
            f"{r['日期']} {r['类别']} {r['数量']}{r['单位']}" for r in related
        ))

        if found is None:
            st.info(f"「{chosen}」没有随项目一起保存，原件还在原始导出目录里。")
            return

        if found.suffix.lower() in IMAGE_SUFFIXES:
            st.image(str(found), width=460, caption=found.name)
        else:
            payload = found.read_bytes()
            st.download_button(f"下载 {found.name}", data=payload, file_name=found.name,
                               mime="application/octet-stream", key=f"dl-{found.name}")


def render_quality(report) -> None:
    """把置信度那套内部机制，折成一个用户看得懂的分数。"""

    left, right = st.columns([1, 3])
    left.metric("数据质量分", f"{report.score}/100", report.level)
    with right:
        for part in report.parts:
            st.caption(f"{part.name} {part.earned:.0f}/{part.full:.0f} · {part.note}")
        for hint in report.hints:
            st.caption(f"提示：{hint}")


def render_planning(ledger, records) -> None:
    """减排规划：先定约束，再看台阶，最后给方案。"""

    if not ledger.by_category:
        st.info("账本里还没有可以给建议的类别。")
        return

    st.markdown(f'<div class="ch-sub">{KIND_RULE}</div>', unsafe_allow_html=True)
    control = st.columns([2, 2, 2])
    kind_label = control[1].radio(
        "要不要改日常习惯", ["不改，只换东西或调设置", "愿意持续调整习惯"],
        index=0, key="kind-level",
    )
    allowed = ("设备",) if kind_label.startswith("不改") else ("设备", "行为")
    max_actions = control[2].slider("最多改几项", 1, 8, 4, key="max-actions")

    steps = frontier(ledger, records, max_actions=max_actions, allowed_kinds=allowed)
    if not steps:
        st.info("按当前选择，没有可选的动作。把「要不要改日常习惯」放宽试试。")
        return

    labels = [
        f"{s.cost_yuan:,.0f} 元 · 减 {s.reduction_kg:.0f} kg" for s in steps
    ]
    picked = control[0].selectbox(
        "投入档位（只有这几个数有意义，中间是平台区）",
        range(len(steps)), format_func=lambda i: labels[i],
        index=min(1, len(steps) - 1), key="step-pick",
    )
    chosen_step = steps[picked]

    with st.expander("为什么投入只有几档有意义", expanded=False):
        chart = (
            alt.Chart(pd.DataFrame([{"投入": s.cost_yuan, "减排": s.reduction_kg} for s in steps]))
            .mark_line(interpolate="step-after", color="#2f6f4e")
            .encode(x=alt.X("投入:Q", title="一次性投入（元）"),
                    y=alt.Y("减排:Q", title="每年减排 kgCO2e"))
            .properties(height=220)
        )
        st.altair_chart(chart, use_container_width=True)
        st.caption(
            "钱不是连续起作用的：只有「够不够买下某件东西」的那几个价格点会改变结果，"
            "两档之间多花的钱不会带来任何额外减排。"
        )
        st.dataframe(
            pd.DataFrame([
                {"档位投入（元）": s.cost_yuan, "每年减排（kgCO2e）": s.reduction_kg,
                 "比上一档多减": round(s.reduction_kg - (steps[i - 1].reduction_kg if i else 0.0), 1)}
                for i, s in enumerate(steps)
            ]),
            width="stretch", hide_index=True,
        )

    result = plan(ledger, records, budget_yuan=chosen_step.cost_yuan,
                  max_actions=max_actions, allowed_kinds=allowed)

    st.markdown(
        f'<div class="ch-sub">选中 {len(result.chosen)} 项，每年可减约 {result.total_reduction_kg} kgCO2e；'
        f'投入 {result.total_setup_cost:.0f} 元，'
        f'每年{"省" if result.total_annual_delta < 0 else "多花"} '
        f'{abs(result.total_annual_delta):.0f} 元。'
        f'{humanize(result.total_reduction_kg)}</div>',
        unsafe_allow_html=True,
    )

    with st.expander("先看你这份账的形状", expanded=False):
        st.dataframe(
            pd.DataFrame([
                {"类别": CATEGORY_LABEL.get(p.category, p.category),
                 "占比": f"{p.share_percent}%", "月均": p.monthly,
                 "参考区间": p.band, "判断": p.verdict}
                for p in result.profile
            ]),
            width="stretch", hide_index=True,
        )

    for item in result.chosen:
        with st.container(border=True):
            st.markdown(f"**{item.title}**")
            st.write(item.action)
            st.caption(f"每年减 {item.reduction_kg:.1f} kgCO2e · 边际成本 {item.marginal_cost:+.2f} 元/kg · {item.kind}类")
            st.caption(f"为什么推荐给你：{item.rationale}")
            st.caption(f"假设：{item.assumption}")

    skipped = [c for c in result.candidates if c not in result.chosen]
    if skipped:
        with st.expander(f"还有 {len(skipped)} 项没被选中", expanded=False):
            for item in skipped:
                st.write(f"- {item.title}：每年可减 {item.reduction_kg:.1f} kg，投入 {item.setup_cost_yuan:.0f} 元")

    for note in result.notes:
        st.caption(f"说明：{note}")

def render_scope_note() -> None:
    with st.expander("这三个范围是怎么分的", expanded=False):
        st.markdown(
            """
| 范围 | 意思 | 本项目里对应 |
|---|---|---|
| 范围一 | 自己烧掉的燃料 | 天然气、汽油、柴油、制冷剂逸散 |
| 范围二 | 外购的电和热 | 电费单的用电量、集中供暖 |
| 范围三 | 上下游与消费 | 出行、外卖、快递、购物、用水、垃圾 |

按 GHG Protocol 的通用口径划分。判据就一条：**这把火是不是你自己点的**——
天然气是你家灶上烧的，算范围一；电是电厂烧的，你只是买，所以算范围二。
            """
        )
        st.caption(
            "三个范围之外还有两类：碳汇（种树吸收）和避免排放（比如自家光伏替掉火电）。"
            "这两类不并进总量，本项目的凭证里留了 reductionKgCO2e 字段单独记。"
        )


def render_export(result: dict) -> None:
    columns = st.columns(3)
    passport_text = json.dumps(result["passport"].to_dict(), ensure_ascii=False, indent=2)
    columns[0].download_button(
        "下载碳护照（对外凭证）", data=passport_text,
        file_name=f"carbon-passport-{today()}.json", mime="application/json",
    )

    raw_path = st.session_state.get("last_json")
    if raw_path and Path(raw_path).exists():
        columns[1].download_button(
            "下载已识别清单（原始 JSON）", data=Path(raw_path).read_bytes(),
            file_name=Path(raw_path).name, mime="application/json",
        )

    # 单文件 HTML 报告：发给不在同一网络的人，双击就能看，不用连你这台机器
    report = render_html(
        result["ledger"], result["records"],
        assess(result["records"], result["ledger"], result["warnings"]),
        plan(result["ledger"], result["records"], max_actions=8),
        result["passport"], result["warnings"],
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    columns[2].download_button(
        "下载静态报告（单文件 HTML）", data=report.encode("utf-8"),
        file_name=f"carbon-report-{today()}.html", mime="text/html",
    )
    st.caption(
        "凭证可用命令行独立校验：`python -m carbonhub.verify out/passport.json`；"
        "账本可用接口对外提供：`python -m carbonhub.api`。"
    )
    with st.expander("看一眼凭证内容", expanded=False):
        st.json(result["passport"].to_dict())


# ---------------------------------------------------------------- 侧栏

def render_sidebar() -> dict:
    st.sidebar.header("设置")

    key = st.sidebar.text_input("DeepSeek API Key", type="password",
                                help="只存在本次会话的内存里，不写盘。也可用环境变量配好。")
    if key:
        os.environ["CARBONHUB_VISION_API_KEY"] = key
    if os.environ.get("CARBONHUB_VISION_API_KEY"):
        st.sidebar.success("识别通道已就绪")
    else:
        st.sidebar.warning("没配 Key：图片会读本地缓存，读不到就跳过")

    st.sidebar.divider()
    subject = st.sidebar.text_input("账本主体", value="我的碳账本")

    st.sidebar.caption("账期")
    left, right = st.sidebar.columns(2)
    period_from = left.text_input("起", value="2026-07-01", label_visibility="collapsed")
    period_to = right.text_input("止", value="2026-12-31", label_visibility="collapsed")

    st.sidebar.divider()
    st.sidebar.caption("适配器")
    for item in registry().catalogue():
        mark = {"connected": "已接入", "available": "已实现", "planned": "待接入"}[item["status"]]
        st.sidebar.write(f"**{item['name']}** · {mark}")

    st.sidebar.divider()
    st.sidebar.caption("排放因子 official-2021-2023 · 出处见 fixtures/factors_seed.csv")
    return {
        "subject": subject,
        "period": (period_from, period_to),

    }


# ---------------------------------------------------------------- 载入数据区

def render_source_picker() -> tuple[list, list[str]]:
    st.subheader("① 载入数据")
    st.markdown('<div class="ch-sub">拖文件进来就行，图片、账单 CSV、已识别 JSON 会自动分辨。</div>',
                unsafe_allow_html=True)

    uploaded = st.file_uploader(
        "上传文件", type=["png", "jpg", "jpeg", "jpe", "jfif", "webp", "csv", "json"],
        accept_multiple_files=True, label_visibility="collapsed",
    )
    if uploaded and st.button("开始核算", type="primary"):
        with st.spinner("识别与核算中…"):
            records, warnings, _ = dispatch(store_uploads(uploaded))
            json_files = [str(p) for p in uploaded if Path(p.name).suffix.lower() == ".json"]
            st.session_state.update(
                extra_records=records, extra_warnings=warnings,
                last_json=next((str(UPLOADS / n) for n in json_files), st.session_state.get("last_json")),
            )

    with st.expander("没有文件？用仓库里的内置样本试试", expanded=False):
        groups = (
            ("自有单据", "own", True),
            ("其他单据", "extra", False),
            ("公开样本（他人单据，演示需注明来源）", "public", False),
        )
        picked: list[str] = []
        for label, folder, default in groups:
            files = sorted(p for p in SAMPLES.glob(f"{folder}/*") if p.suffix.lower() in IMAGE_SUFFIXES)
            if not files:
                continue
            st.caption(label)
            for item in files:
                if st.checkbox(item.name, value=default, key=f"s-{folder}-{item.name}"):
                    picked.append(str(item))
        if picked and st.button("识别选中的样本", type="primary"):
            with st.spinner(f"识别 {len(picked)} 张…"):
                records, warnings, _ = dispatch(picked)
            st.session_state.update(extra_records=records, extra_warnings=warnings)

        sample_json = FIXTURES / "real_receipts_20261013.json"
        st.caption("已识别好的清单（不走模型，秒出）")
        if st.button("载入已识别 JSON 样本"):
            records, warnings = collect_from_json(str(sample_json))
            st.session_state.update(extra_records=records, extra_warnings=warnings,
                                    last_json=str(sample_json))

    records = list(st.session_state.get("extra_records", []))
    warnings = list(st.session_state.get("extra_warnings", []))
    if records:
        st.success(f"当前载入 {len(records)} 条活动记录。")
    return records, warnings


def main() -> None:
    st.title("carbon-hub")
    st.markdown('<div class="ch-sub">把散在各处的能源单据，合成一份能带走的碳账本。</div>',
                unsafe_allow_html=True)

    settings = render_sidebar()
    records, warnings = render_source_picker()

    st.subheader("② 核算结果")
    if not records:
        st.info("还没有数据。上面拖个文件进来，或者展开内置样本点一个。")
        return

    result = run_pipeline(records, warnings, settings["subject"], settings["period"])
    ledger = result["ledger"]
    render_metrics(ledger)
    render_breakdown(ledger)

    rows = build_rows(result)
    render_table(rows)
    render_quality(assess(result["records"], ledger, result["warnings"]))
    render_source_viewer(rows)
    render_scope_note()

    if result["warnings"]:
        with st.expander(f"需要人工确认 / 已跳过（{len(result['warnings'])} 条）", expanded=False):
            for warning in result["warnings"]:
                st.write(f"- {warning}")

    st.subheader("③ 减排规划")
    render_planning(ledger, result["records"])

    st.subheader("④ 导出与校验")
    render_export(result)


if __name__ == "__main__":
    main()
