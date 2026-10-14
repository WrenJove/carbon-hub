"""排放因子库：加载与匹配。

因子库独立于代码，用 CSV 维护。匹配规则写在 FactorLibrary.match 里：
按 分类 + 单位 找，地区与年份就近取，找不到就报错（绝不默认 0）。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import EmissionFactor


class FactorNotFound(LookupError):
    """因子库里没有能匹配的因子。宁可报错，也不要悄悄按 0 记。"""


def load_factors(path: str | Path) -> list[EmissionFactor]:
    """从 CSV 读因子。列：id,category,unit,factor,scope,region,year,source"""

    factors: list[EmissionFactor] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            factors.append(
                EmissionFactor(
                    id=row["id"].strip(),
                    category=row["category"].strip(),
                    unit=row["unit"].strip(),
                    factor=float(row["factor"]),
                    scope=int(row["scope"]),
                    year=int(row["year"]),
                    source=row["source"].strip(),
                    region=(row.get("region") or "CN").strip(),
                )
            )
    return factors


class FactorLibrary:
    """按 分类 + 单位 匹配因子。"""

    def __init__(self, factors: list[EmissionFactor], version: str = "seed") -> None:
        self._factors = list(factors)
        self.version = version

    @classmethod
    def from_csv(cls, path: str | Path, version: str = "seed") -> "FactorLibrary":
        return cls(load_factors(path), version=version)

    @property
    def factors(self) -> tuple[EmissionFactor, ...]:
        return tuple(self._factors)

    def match(
        self,
        category: str,
        unit: str,
        region: str = "CN",
        year: int | None = None,
    ) -> EmissionFactor:
        """找不到就抛 FactorNotFound，让上游去标"待补充"，而不是当 0 算。"""

        candidates = [f for f in self._factors if f.category == category and f.unit == unit]
        if not candidates:
            raise FactorNotFound(f"因子库缺少 {category} / {unit} 的因子")

        # 地区优先精确匹配，其次接受通用（CN）
        exact = [f for f in candidates if f.region == region]
        pool = exact or candidates

        # 年份：不指定取最新；指定则取不超过该年的最新，没有就往回退到最新的。
        # 同一年有多个口径时（例如"全国电力平均"和"不含市场化交易非化石电量"），
        # 因子库里写在前面的一条优先——文件顺序就是优先级，请把主口径写在前面。
        if year is not None:
            not_newer = [f for f in pool if f.year <= year]
            if not_newer:
                return max(not_newer, key=lambda f: f.year)
        return max(pool, key=lambda f: f.year)
