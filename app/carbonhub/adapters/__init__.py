"""适配器集合。"""

from ..models import AdapterCapability
from .alipay import AlipayBillAdapter
from .base import AdapterRegistry, CarbonAdapter
from .manual import ManualAdapter
from .mock import MockAdapter
from .receipts_json import ReceiptsJsonAdapter
from .vision_bill import VisionBillAdapter

__all__ = [
    "AdapterRegistry",
    "CarbonAdapter",
    "AlipayBillAdapter",
    "ManualAdapter",
    "MockAdapter",
    "ReceiptsJsonAdapter",
    "VisionBillAdapter",
    "default_registry",
]

# 只声明、不实现的来源：用来在界面上展示"接一个是一个"的延展路径
_DECLARED_ONLY: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], str], ...] = (
    ("ant_forest", "蚂蚁森林", ("api",), ("transport",), "无开放接口，等开放"),
    ("bank_carbon", "银行碳账户", ("api",), ("electricity",), "无开放接口，等开放"),
    ("city_carbon", "各地碳普惠平台", ("api",), ("other",), "无开放接口，等开放"),
)


class _DeclaredOnly(CarbonAdapter):
    """只声明能力的占位适配器，一旦真的调用就报错。"""

    def __init__(self, capability: AdapterCapability) -> None:
        self.capability = capability

    def ingest(self, raw):  # type: ignore[override]
        raise NotImplementedError(f"{self.capability.display_name} 尚未接入")


def default_registry() -> AdapterRegistry:
    """默认注册表：已接入的 + 待接入的占位，一起交给界面展示。"""

    registry = AdapterRegistry()
    registry.register(ManualAdapter())
    registry.register(AlipayBillAdapter())
    registry.register(MockAdapter())
    registry.register(ReceiptsJsonAdapter())
    registry.register(VisionBillAdapter())

    for adapter_id, name, ingest, categories, note in _DECLARED_ONLY:
        registry.register(
            _DeclaredOnly(
                AdapterCapability(
                    id=adapter_id,
                    display_name=name,
                    status="planned",
                    ingest=ingest,
                    categories=categories,
                    note=note,
                )
            )
        )

    return registry
