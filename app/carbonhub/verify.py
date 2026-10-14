"""校验一份碳护照：改没改过，一跑就知道。

用法：
    python -m carbonhub.verify out/passport.json
    python -m carbonhub.verify out/passport.json --json

能验什么、不能验什么，说清楚：
  能验  凭证内部自洽（记账总额、条目指纹链没被动过手脚）
  不能验 原始单据本身是真的，也验不了签署方是谁
后两件事要靠人核对原件和线下信任，别把话说过头。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .kernel import entry_hash, merkle_root
from .models import PassportEntry


def verify(payload: dict) -> list[str]:
    """返回问题清单，空列表表示没发现问题。"""

    problems: list[str] = []

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        return ["这份 JSON 里没有 entries 字段，不像是一份碳护照"]

    entries: list[PassportEntry] = []
    for index, item in enumerate(raw_entries):
        try:
            entries.append(PassportEntry(
                activity_id=str(item["activityId"]),
                source_id=str(item.get("sourceId", "")),
                occurred_at=str(item.get("occurredAt", "")),
                category=str(item.get("category", "")),
                kgco2e=float(item["kgCO2e"]),
                scope=int(item["scope"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"第 {index + 1} 条记录读不出来：{exc}")
    if problems:
        return problems

    # 1) 总额对不对得上（防止只改总额）
    declared_total = payload.get("totalKgCO2e")
    computed_total = round(sum(e.kgco2e for e in entries), 3)
    if declared_total is None or abs(float(declared_total) - computed_total) > 0.01:
        problems.append(
            f"总额与明细对不上：凭证写 {declared_total}，逐条相加是 {computed_total}"
        )

    # 2) 指纹链对不对得上（防止改某一条的数量）
    declared_root = payload.get("ledgerRoot")
    if not declared_root:
        problems.append("这份凭证没有 ledgerRoot，无法校验内容是否被改动")
    else:
        actual_root = merkle_root([entry_hash(e) for e in entries])
        if actual_root != declared_root:
            problems.append(
                "指纹链对不上，说明明细被人改动过："
                f"凭证写 {declared_root[:16]}…，重算得到 {actual_root[:16]}…"
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验碳护照")
    parser.add_argument("passport", type=Path)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    payload = json.loads(args.passport.read_text(encoding="utf-8"))
    problems = verify(payload)

    if args.json:
        print(json.dumps(
            {"file": args.passport.name, "ok": not problems, "problems": problems},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"文件：{args.passport}")
        print(f"条目：{len(payload.get('entries', []))} 条")
        print(f"根哈希：{payload.get('ledgerRoot', '(缺)')[:32]}…")
        if problems:
            print("结果：有问题")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("结果：校验通过，内容自洽、未被改动")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
