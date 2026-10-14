"""解析与校验：模型输出 -> 可信字段 -> 活动记录。

全是纯函数，不联网，可以离线单测。
这里承担两件事：
  1. 把模型那段文字抠成 JSON（模型偶尔会加代码块或废话）；
  2. 用单据自身的算术关系交叉验算，对不上的压低置信度并留下警告。

验算用的是"比值带"而不是等号：中国单据上 数量×单价 与 实付金额 天生不相等
（中间夹着增值税和代收基金），所以只判断比值是否落在一个合理的带子里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..models import ActivityRecord
from .prompt import ARITHMETIC_BAND, ELECTRICITY_KWH_RANGE, FUEL_LITERS_RANGE

MIN_TRUSTWORTHY = 0.7


def extract_json(text: str) -> dict:
    """把模型回复抠成 dict。容忍 ```json 代码块和前后废话。"""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        cleaned = cleaned.removeprefix("json").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"模型回复里找不到 JSON：{text[:120]!r}")
    return json.loads(cleaned[start : end + 1])


def _num(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("元", "").replace("度", "")
        if cleaned in ("", "null", "None", "-"):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


@dataclass
class RecognizedDoc:
    doc_type: str
    confidence: float
    electricity: dict = field(default_factory=dict)
    fuel: dict = field(default_factory=dict)
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


def _check_ratio(computed: float, reported: float, label: str, warnings: list[str]) -> bool:
    """computed = 数量×单价；reported = 单据金额。只看比值是否落在合理带内。"""

    if computed <= 0 or reported <= 0:
        return True
    ratio = reported / computed
    low, high = ARITHMETIC_BAND
    if not (low <= ratio <= high):
        warnings.append(
            f"{label}比值异常：数量×单价={computed:.2f}，单据金额={reported:.2f}，"
            f"比值 {ratio:.2f} 超出合理区间 {low}~{high}，请人工核对"
        )
        return False
    return True


def validate(payload: dict) -> RecognizedDoc:
    """交叉验算 + 置信度打分。不做任何推测，缺就缺。"""

    doc_type = str(payload.get("docType") or "unknown").strip()
    confidence = _num(payload.get("confidence"))
    confidence = 0.5 if confidence is None else max(0.0, min(1.0, confidence))
    notes = str(payload.get("notes") or "").strip()

    doc = RecognizedDoc(doc_type=doc_type, confidence=confidence, notes=notes)

    if doc_type == "electricity":
        block = payload.get("electricity") or {}
        kwh = _num(block.get("kwh"))
        amount = _num(block.get("amountCny"))
        unit_price = _num(block.get("unitPrice"))
        peak = _num(block.get("peakKwh"))
        valley = _num(block.get("valleyKwh"))

        if kwh is None and peak is not None and valley is not None:
            kwh = peak + valley
            doc.confidence *= 0.9
            doc.warnings.append("用电量由峰谷相加得到，非直接读出")

        if kwh is None:
            doc.warnings.append("没读到用电量。金额无法反推用电量，需要人工补录或换张更清楚的图")
        elif not (ELECTRICITY_KWH_RANGE[0] <= kwh <= ELECTRICITY_KWH_RANGE[1]):
            doc.warnings.append(f"用电量 {kwh:g} 度超出常见区间，疑似读错数量级")
            doc.confidence *= 0.6

        if kwh is not None and unit_price is not None and amount is not None:
            if not _check_ratio(kwh * unit_price, amount, "用电量×电价与电费", doc.warnings):
                doc.confidence *= 0.6

        doc.electricity = {
            "accountNo": block.get("accountNo"),
            "periodStart": block.get("periodStart"),
            "periodEnd": block.get("periodEnd"),
            "billDate": block.get("billDate"),
            "kwh": kwh,
            "amountCny": amount,
            "unitPrice": unit_price,
            "peakKwh": peak,
            "valleyKwh": valley,
        }

    elif doc_type == "fuel":
        block = payload.get("fuel") or {}
        liters = _num(block.get("liters"))
        amount = _num(block.get("amountCny"))
        unit_price = _num(block.get("unitPrice"))

        if liters is None:
            doc.warnings.append("没读到升数。金额无法反推升数，需要人工补录")
        elif not (FUEL_LITERS_RANGE[0] <= liters <= FUEL_LITERS_RANGE[1]):
            doc.warnings.append(f"升数 {liters:g} 超出常见区间，疑似读错")
            doc.confidence *= 0.6

        if liters is not None and unit_price is not None and amount is not None:
            if not _check_ratio(liters * unit_price, amount, "升数×单价与金额", doc.warnings):
                doc.confidence *= 0.6

        doc.fuel = {
            "date": block.get("date"),
            "fuelGrade": block.get("fuelGrade"),
            "liters": liters,
            "unitPrice": unit_price,
            "amountCny": amount,
        }

    else:
        doc.warnings.append("认不出票种，已跳过；可在 prompt.py 里补这个票种的模板")

    doc.confidence = round(max(0.0, min(1.0, doc.confidence)), 2)
    if doc.confidence < MIN_TRUSTWORTHY:
        doc.warnings.append(f"整体置信度 {doc.confidence}，建议人工复核")
    return doc


def to_records(
    doc: RecognizedDoc,
    source_id: str,
    doc_key: str,
    fallback_date: str,
    source_file: str = "",
) -> tuple[list[ActivityRecord], list[str]]:
    """可信字段 -> 活动记录。置信度低照样产出，但带警告，交给界面标黄。"""

    records: list[ActivityRecord] = []

    if doc.doc_type == "electricity" and doc.electricity.get("kwh") is not None:
        occurred = (
            doc.electricity.get("periodEnd")
            or doc.electricity.get("billDate")
            or fallback_date
        )
        records.append(
            ActivityRecord(
                id=f"elec:{doc_key}",
                source_id=source_id,
                occurred_at=str(occurred),
                category="electricity",
                quantity=float(doc.electricity["kwh"]),
                unit="kWh",
                confidence=doc.confidence,
                raw={
                    "source": "vision",
                    "file": source_file,
                    "account_no": doc.electricity.get("accountNo"),
                    "amount_cny": doc.electricity.get("amountCny"),
                },
            )
        )

    if doc.doc_type == "fuel" and doc.fuel.get("liters") is not None:
        occurred = doc.fuel.get("date") or fallback_date
        records.append(
            ActivityRecord(
                id=f"fuel:{doc_key}",
                source_id=source_id,
                occurred_at=str(occurred),
                category="fuel",
                quantity=float(doc.fuel["liters"]),
                unit="L",
                confidence=doc.confidence,
                raw={
                    "source": "vision",
                    "file": source_file,
                    "grade": doc.fuel.get("fuelGrade"),
                    "amount_cny": doc.fuel.get("amountCny"),
                },
            )
        )

    return records, list(doc.warnings)
