"""数据探查：拿到一份陌生的数据，几分钟内看清它长什么样。

为什么单独做这个：赛题现场公布，开赛头两小时就是摸数据。那时候手写 pandas
最容易乱，不如固化成一个工具，输入文件、输出一份能直接念的报告。

零依赖（标准库），支持 CSV / TSV / JSON。不做任何假设，只描述事实。

用法：
    python -m carbonhub.profile_data 数据.csv
    python -m carbonhub.profile_data 数据.json --out out/profile.md
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_ROWS_FOR_SAMPLE = 20000
TIME_HINTS = ("time", "date", "时间", "日期", "时刻", "开始", "结束", "创建")
ID_HINTS = ("id", "编号", "单号", "流水", "序列", "序号")
ENERGY_HINTS = ("kwh", "度", "电量", "功率", "kw", "电压", "电流", "费用", "金额", "价格")


@dataclass
class ColumnProfile:
    name: str
    kind: str  # 数值 / 时间 / 文本
    filled: int
    total: int
    unique: int
    samples: list[str] = field(default_factory=list)
    low: float | None = None
    median: float | None = None
    high: float | None = None

    @property
    def fill_rate(self) -> float:
        return self.filled / self.total if self.total else 0.0


def _looks_like_number(text: str) -> bool:
    cleaned = text.strip().replace(",", "").replace("¥", "").replace("%", "")
    if not cleaned:
        return False
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _looks_like_time(text: str) -> bool:
    sample = text.strip()[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            datetime.strptime(sample, fmt)
            return True
        except ValueError:
            continue
    return False


def profile_rows(rows: list[dict[str, str]]) -> list[ColumnProfile]:
    if not rows:
        return []
    names: list[str] = []
    for row in rows[:200]:
        for key in row:
            if key not in names:
                names.append(key)

    total = len(rows)
    profiles: list[ColumnProfile] = []
    for name in names:
        values = [str(row.get(name, "") or "").strip() for row in rows]
        filled = [v for v in values if v != ""]
        if not filled:
            profiles.append(ColumnProfile(name, "空", 0, total, 0, []))
            continue

        sample = filled[:200]
        numeric = sum(1 for v in sample if _looks_like_number(v))
        temporal = sum(1 for v in sample if _looks_like_time(v))
        if numeric >= len(sample) * 0.9:
            kind = "数值"
        elif temporal >= len(sample) * 0.9:
            kind = "时间"
        else:
            kind = "文本"

        profile = ColumnProfile(
            name=name, kind=kind, filled=len(filled), total=total,
            unique=len(set(filled)), samples=list(dict.fromkeys(filled))[:3],
        )

        if kind == "数值":
            numbers = []
            for v in filled:
                try:
                    numbers.append(float(v.replace(",", "").replace("¥", "")))
                except ValueError:
                    continue
            if numbers:
                profile.low = min(numbers)
                profile.high = max(numbers)
                profile.median = statistics.median(numbers)
        elif kind == "时间":
            stamps = sorted(v for v in filled if _looks_like_time(v))
            if stamps:
                profile.samples = [stamps[0], stamps[-1]]
        profiles.append(profile)
    return profiles


def _hints(profiles: list[ColumnProfile], rows: list[dict[str, str]]) -> list[str]:
    hints: list[str] = []
    for p in profiles:
        lowered = p.name.lower()
        if p.kind == "时间" and len(p.samples) >= 2:
            hints.append(f"「{p.name}」是时间列，跨度 {p.samples[0]} 到 {p.samples[-1]}，适合做时间轴分析。")
        if p.kind == "数值" and any(h in lowered for h in ENERGY_HINTS):
            hints.append(f"「{p.name}」看起来是量或金额（{p.low:g} ~ {p.high:g}），可能是核算的输入。")
        if p.fill_rate < 0.8:
            hints.append(f"「{p.name}」有 {(1 - p.fill_rate) * 100:.0f}% 是空的，用到它要小心。")
        if any(h in lowered for h in ID_HINTS) and p.unique == p.filled:
            hints.append(f"「{p.name}」值全不重复，像是主键，可以用来去重。")

    if rows:
        duplicated = len(rows) - len({tuple(sorted(r.items())) for r in rows})
        if duplicated:
            hints.append(f"整行完全重复的有 {duplicated} 条，先确认是重复记录还是正常业务。")
    return hints


def _read(path: Path) -> tuple[list[dict[str, str]], str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    payload = value
                    break
        if not isinstance(payload, list):
            raise ValueError("JSON 里找不到记录列表")
        rows = [{k: ("" if v is None else v) for k, v in item.items()} for item in payload
                if isinstance(item, dict)]
        return rows, "json"

    for encoding in ("utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|") if sample else csv.excel
                return list(csv.DictReader(handle, dialect=dialect)), encoding
        except (UnicodeDecodeError, csv.Error):
            continue
    raise ValueError("读不出来，编码或分隔符都不认识")


def render(rows: list[dict[str, str]], profiles: list[ColumnProfile], source: str, encoding: str) -> str:
    lines = [
        f"# 数据探查：{source}",
        "",
        f"- 记录数：{len(rows):,}",
        f"- 列数：{len(profiles)}",
        f"- 编码/格式：{encoding}",
        "",
        "## 列清单",
        "",
        "| 列名 | 类型 | 非空率 | 唯一值 | 范围 / 示例 |",
        "|---|---|---|---|---|",
    ]
    for p in profiles:
        if p.kind == "数值" and p.low is not None:
            detail = f"{p.low:g} ~ {p.high:g}（中位 {p.median:g}）"
        else:
            detail = " / ".join(str(s)[:28] for s in p.samples[:3])
        lines.append(f"| {p.name} | {p.kind} | {p.fill_rate:.0%} | {p.unique:,} | {detail} |")

    hints = _hints(profiles, rows)
    lines += ["", "## 一眼看点", ""]
    lines += [f"- {h}" for h in hints] if hints else ["- 没发现明显异常，字段也比较干净。"]

    lines += [
        "",
        "## 下一步",
        "",
        "1. 确认时间列和量列，判断这份数据能不能算碳排放（电量 × 电网因子）。",
        "2. 找主键，确认去重口径。",
        "3. 缺得厉害的列先别用。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据探查")
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--rows", type=int, default=MAX_ROWS_FOR_SAMPLE)
    args = parser.parse_args(argv)

    rows, encoding = _read(args.source)
    rows = rows[: args.rows]
    report = render(rows, profile_rows(rows), args.source.name, encoding)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"已写出：{args.out.resolve()}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
