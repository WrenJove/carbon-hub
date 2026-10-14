"""平台账单 CSV 适配器（支付宝 / 微信 / 通用）。

账单是用户自己导出的 CSV，属于"可合法获取"的数据来源。

表头不写死：不同平台、不同版本导出来的列名都不一样（支付宝叫"交易分类"，
微信叫"交易类型"，换个版本还可能叫"类别"），这里用同义词匹配自动认列，
用户不需要切来切去。真认不出来时，会把实际读到的表头原样报出来，方便排查。

拿不到实物量的行（比如"电费 186.40 元"）不会硬猜，而是进 warnings，
提示改用拍照识别去拿真实用电量。
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ..models import ActivityRecord, AdapterCapability, IngestResult, RawInput
from .base import CarbonAdapter

# 标准字段 -> 可能的表头写法。匹配时先找完全相同，再找包含关系。
COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "occurred_at": ("交易时间", "交易日期", "创建时间", "付款时间", "发生时间", "日期", "时间"),
    "category": ("交易分类", "交易类型", "分类", "类别", "类型"),
    "description": ("商品说明", "商品名称", "商品", "说明", "摘要", "备注", "交易说明"),
    "counterparty": ("交易对方", "对方", "商户", "收款方"),
    "direction": ("收/支", "收支", "收/付", "资金方向", "收付", "收支类型"),
    "amount": ("金额", "交易金额"),
}

# 没有这两列就没法算：一条记录至少要有个日期和一个数
REQUIRED_FIELDS = ("occurred_at", "amount")

# 分类关键词。落在账单的"分类"列和"商品说明"列上一起匹配。
CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("交通", "出行", "打车", "地铁", "公交", "滴滴", "高德", "铁路", "火车", "高铁",
      "航空", "机票", "出租", "网约车", "单车", "骑行", "停车"), "transport"),
    (("餐饮", "美食", "外卖", "餐厅", "饭馆"), "diet"),
    (("购物", "商城", "超市", "淘宝", "京东", "拼多多", "天猫"), "shopping"),
)

# 只统计支出
EXPENSE_MARK = "支出"

# 网约车折里程：账单只有金额，按均价反推，属"估算"档，置信度压低
TRANSPORT_YUAN_PER_KM = 2.5

# 这些关键词的行需要实物量，账单给不了，交给拍照识别
NEEDS_PHOTO_KEYWORDS = ("电费", "水费", "燃气费", "天然气", "燃气")

_STRIP = re.compile(r"[\s\u3000()（）\[\]【】:：*·,，]")


def _norm(text: object) -> str:
    """表头归一化：去空白、去括号、去标点、转小写。"""

    return _STRIP.sub("", str(text)).lower()


def detect_columns(headers: list[str]) -> dict[str, str]:
    """把实际表头映射到标准字段。自动识别，认不出就不放进去。"""

    normalized = [(header, _norm(header)) for header in headers]
    mapping: dict[str, str] = {}
    taken: set[str] = set()

    for field, synonyms in COLUMN_SYNONYMS.items():
        for synonym in synonyms:
            needle = _norm(synonym)
            hit = next((h for h, n in normalized if n == needle and h not in taken), None)
            if hit is None:
                hit = next((h for h, n in normalized if needle in n and h not in taken), None)
            if hit is not None:
                mapping[field] = hit
                taken.add(hit)
                break

    return mapping


class AlipayBillAdapter(CarbonAdapter):
    capability = AdapterCapability(
        id="alipay_bill",
        display_name="平台账单 CSV",
        status="connected",
        ingest=("file-csv",),
        categories=("transport", "diet", "shopping"),
        note="自动识别表头，支付宝/微信/通用账单都能吃；只有金额的行业按均价估算并标低置信度",
    )

    def ingest(self, raw: RawInput) -> IngestResult:
        result = IngestResult()
        rows = self._read_rows(Path(str(raw.payload)))

        if not rows:
            result.warnings.append("账单为空或解析不出任何行")
            return result

        mapping = detect_columns(list(rows[0].keys()))
        missing = [f for f in REQUIRED_FIELDS if f not in mapping]
        if missing:
            actual = "、".join(list(rows[0].keys())[:12])
            result.warnings.append(
                f"这份账单认不出必需的表头（缺 {', '.join(missing)}）。"
                f"实际读到的表头是：{actual}。"
                "若确实换了版式，在 alipay.py 的 COLUMN_SYNONYMS 里补一个同义词即可"
            )
            return result

        for index, row in enumerate(rows, start=2):  # 第 1 行是表头
            direction = row.get(mapping.get("direction", ""), "").strip()
            if direction and direction != EXPENSE_MARK:
                continue

            description = row.get(mapping.get("description", ""), "").strip()
            if any(word in description for word in NEEDS_PHOTO_KEYWORDS):
                result.warnings.append(
                    f"第 {index} 行「{description}」需要实物量，请用拍照识别获取用量"
                )
                continue

            category = self._classify(row, mapping)
            if category is None:
                continue

            record = self._to_record(index, row, mapping, category, Path(str(raw.payload)).name)
            if record is None:
                result.warnings.append(f"第 {index} 行金额无法解析，已跳过")
                continue
            result.records.append(record)

        return result

    # --- 内部 ---------------------------------------------------------------

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, str]]:
        """账单导出常见 utf-8 与 gbk 两种编码，都试一遍。"""

        for encoding in ("utf-8-sig", "gbk"):
            try:
                with open(path, "r", encoding=encoding, newline="") as handle:
                    return list(csv.DictReader(handle))
            except UnicodeDecodeError:
                continue
        raise ValueError(f"无法解码账单文件（已试 utf-8/gbk）：{path}")

    @staticmethod
    def _classify(row: dict[str, str], mapping: dict[str, str]) -> str | None:
        text = " ".join(
            [
                row.get(mapping.get("category", ""), ""),
                row.get(mapping.get("description", ""), ""),
                row.get(mapping.get("counterparty", ""), ""),
            ]
        )
        for keywords, category in CATEGORY_RULES:
            if any(word in text for word in keywords):
                return category
        return None

    @staticmethod
    def _to_record(
        index: int,
        row: dict[str, str],
        mapping: dict[str, str],
        category: str,
        source_file: str = "",
    ) -> ActivityRecord | None:
        try:
            raw_amount = str(row[mapping["amount"]]).replace(",", "").replace("¥", "").strip()
            amount = float(raw_amount)
        except (KeyError, ValueError):
            return None

        occurred_at = str(row[mapping["occurred_at"]]).strip().split(" ")[0]
        base_id = f"bill-{occurred_at}-{index:03d}"

        if category == "transport":
            # 账单没有里程，按均价反推；raw 里写清楚这是推算
            quantity = round(amount / TRANSPORT_YUAN_PER_KM, 2)
            return ActivityRecord(
                id=base_id,
                source_id="alipay_bill",
                occurred_at=occurred_at,
                category="transport",
                quantity=quantity,
                unit="km",
                confidence=0.5,
                raw={"file": source_file, "amount_cny": amount,
                     "derived": f"按 {TRANSPORT_YUAN_PER_KM} 元/公里反推"},
            )

        if category == "diet":
            return ActivityRecord(
                id=base_id,
                source_id="alipay_bill",
                occurred_at=occurred_at,
                category="diet",
                quantity=1.0,
                unit="meal",
                confidence=0.6,
                raw={"file": source_file, "amount_cny": amount, "derived": "一行记一餐"},
            )

        return ActivityRecord(
            id=base_id,
            source_id="alipay_bill",
            occurred_at=occurred_at,
            category="shopping",
            quantity=round(amount, 2),
            unit="CNY",
            confidence=0.7,
            raw={"file": source_file, "amount_cny": amount},
        )
