"""carbon-hub 数据契约。

对应 ../../types.ts 的 0.1 契约，这里是能跑的 Python 版。只放受控词表与
数据结构，不放业务逻辑。

约定：
  - 分类与单位用字符串常量约束，写入时校验，越界直接报错；
  - 所有记录都是不可变的（frozen），核算内核不做原地修改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --- 受控词表 ---------------------------------------------------------------

CATEGORIES: tuple[str, ...] = (
    "electricity",  # 用电
    "gas",          # 天然气 / 燃气
    "fuel",         # 汽油 / 柴油
    "transport",    # 地面出行
    "flight",       # 航空
    "delivery",     # 快递 / 外卖配送
    "diet",         # 饮食
    "water",        # 用水
    "shopping",     # 购物消费
    "waste",        # 垃圾 / 回收
    "other",
)

UNITS: tuple[str, ...] = ("kWh", "m3", "L", "km", "person-km", "kg", "CNY", "count", "meal")

SCOPES: tuple[int, ...] = (1, 2, 3)

ADAPTER_STATUS: tuple[str, ...] = ("connected", "available", "planned")

INGEST_KINDS: tuple[str, ...] = ("api", "file-csv", "file-json", "file-image", "manual")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


# --- 采集层 -----------------------------------------------------------------

@dataclass(frozen=True)
class RawInput:
    """一次可读的原始素材。"""

    source_id: str
    kind: str  # csv | image | json | text | manual
    payload: Any
    captured_at: str


@dataclass(frozen=True)
class ActivityRecord:
    """系统的原子单位：一条可核算的活动。"""

    id: str
    source_id: str
    occurred_at: str  # 建议 YYYY-MM-DD 或 ISO8601
    category: str
    quantity: float
    unit: str
    confidence: float = 1.0
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _require(self.id != "", "id 不能为空")
        _require(self.category in CATEGORIES, f"未知分类: {self.category!r}")
        _require(self.unit in UNITS, f"未知单位: {self.unit!r}")
        _require(self.quantity >= 0, f"quantity 不能为负: {self.quantity}")
        _require(0.0 <= self.confidence <= 1.0, f"confidence 越界: {self.confidence}")


@dataclass(frozen=True)
class AdapterCapability:
    """适配器能力声明，驱动界面上的"已接入 / 可接入 / 待接入"。"""

    id: str
    display_name: str
    status: str
    ingest: tuple[str, ...]
    categories: tuple[str, ...]
    note: str = ""

    def __post_init__(self) -> None:
        _require(self.status in ADAPTER_STATUS, f"未知状态: {self.status!r}")
        for kind in self.ingest:
            _require(kind in INGEST_KINDS, f"未知采集方式: {kind!r}")
        for category in self.categories:
            _require(category in CATEGORIES, f"未知分类: {category!r}")


@dataclass
class IngestResult:
    """适配器输出：记录 + 没能认出来的行（不静默丢弃）。"""

    records: list[ActivityRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- 核算层 -----------------------------------------------------------------

@dataclass(frozen=True)
class EmissionFactor:
    """排放因子，来自独立因子库，可整体替换升级。"""

    id: str
    category: str
    unit: str
    factor: float  # kgCO2e / unit
    scope: int
    year: int
    source: str
    region: str = "CN"

    def __post_init__(self) -> None:
        _require(self.category in CATEGORIES, f"因子分类越界: {self.category!r}")
        _require(self.unit in UNITS, f"因子单位越界: {self.unit!r}")
        _require(self.scope in SCOPES, f"因子范围越界: {self.scope}")
        _require(self.factor >= 0, f"因子不能为负: {self.factor}")


@dataclass(frozen=True)
class EmissionRecord:
    """一条活动对应的排放。

    factor_year 是这条排放所用因子的年份。它和活动发生年份不一定相同——
    因子库缺那一年的数据时只能就近取，把年份记下来是为了在报告里讲清楚。
    """

    activity_id: str
    kgco2e: float
    scope: int
    factor_id: str
    factor_year: int = 0


# --- 记账层 -----------------------------------------------------------------

@dataclass(frozen=True)
class CarbonLedger:
    """一段时期内的碳账本。"""

    subject: str
    period_from: str
    period_to: str
    totals: dict[str, float]  # scope1 / scope2 / scope3 / total
    by_category: dict[str, float]
    emissions: tuple[EmissionRecord, ...]
    standard: str
    factors_version: str


# --- 出具层 -----------------------------------------------------------------

@dataclass(frozen=True)
class PassportEntry:
    activity_id: str
    source_id: str
    occurred_at: str
    category: str
    kgco2e: float
    scope: int


@dataclass(frozen=True)
class CarbonPassport:
    """对外交换的碳数据凭证（互通层）。"""

    spec_version: str
    subject: str
    period_from: str
    period_to: str
    total_kgco2e: float
    entries: tuple[PassportEntry, ...]
    evidence_hashes: tuple[str, ...]
    issued_at: str
    reduction_kgco2e: float | None = None
    # 对全部 entries 求的默克尔根。改一个数字，根就对不上。
    ledger_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "specVersion": self.spec_version,
            "subject": self.subject,
            "period": {"from": self.period_from, "to": self.period_to},
            "totalKgCO2e": round(self.total_kgco2e, 3),
            "reductionKgCO2e": self.reduction_kgco2e,
            "entries": [
                {
                    "activityId": e.activity_id,
                    "sourceId": e.source_id,
                    "occurredAt": e.occurred_at,
                    "category": e.category,
                    "kgCO2e": round(e.kgco2e, 3),
                    "scope": e.scope,
                }
                for e in self.entries
            ],
            "ledgerRoot": self.ledger_root,
            "evidenceHashes": list(self.evidence_hashes),
            "issuedAt": self.issued_at,
        }
