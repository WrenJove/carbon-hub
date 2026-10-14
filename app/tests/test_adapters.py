"""适配器的验收用例：能不能把素材读成活动记录，认不出的是否老实交代。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from carbonhub.adapters import AdapterRegistry, AlipayBillAdapter, ManualAdapter, MockAdapter, default_registry
from carbonhub.models import RawInput

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
BILL = FIXTURES / "alipay_bill_sample.csv"


class ManualAdapterTest(unittest.TestCase):
    def test_good_and_bad_rows(self) -> None:
        adapter = ManualAdapter()
        result = adapter.ingest(
            RawInput(
                source_id="manual",
                kind="manual",
                captured_at="2026-10-13",
                payload=[
                    {"occurred_at": "2026-09-30", "category": "electricity", "quantity": 320, "unit": "kWh"},
                    {"occurred_at": "2026-09-18", "category": "fuel", "quantity": "四十", "unit": "L"},
                ],
            )
        )

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].confidence, 1.0)
        self.assertEqual(len(result.warnings), 1)


class AlipayBillAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = AlipayBillAdapter().ingest(
            RawInput(source_id="alipay_bill", kind="csv", payload=str(BILL), captured_at="2026-10-13")
        )

    def test_records_extracted(self) -> None:
        self.assertEqual(len(self.result.records), 5)
        for record in self.result.records:
            self.assertGreater(record.quantity, 0)
            self.assertLessEqual(record.confidence, 1.0)

    def test_transport_derived_from_amount_is_low_confidence(self) -> None:
        trips = [r for r in self.result.records if r.category == "transport"]
        self.assertTrue(trips)
        for trip in trips:
            self.assertEqual(trip.unit, "km")
            self.assertLess(trip.confidence, 0.7)
            self.assertIn("derived", trip.raw or {})

    def test_electricity_row_asks_for_photo(self) -> None:
        """电费只有金额，账单给不了用电量，必须老实说。"""

        self.assertTrue(any("电费" in w for w in self.result.warnings))

    def test_income_rows_ignored(self) -> None:
        self.assertFalse(any(r.category == "shopping" and r.quantity == 0 for r in self.result.records))


class AdaptiveHeaderTest(unittest.TestCase):
    """表头自动识别：不该让用户为了换个平台去改代码。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ingest(self, name: str, text: str):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return AlipayBillAdapter().ingest(
            RawInput(source_id="alipay_bill", kind="csv", payload=str(path), captured_at="2026-10-14")
        )

    def test_wechat_style_headers(self) -> None:
        text = (
            "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态\n"
            "2026-09-03 08:12:00,交通出行,滴滴出行,滴滴快车,支出,23.50,零钱,支付成功\n"
            "2026-09-05 19:40:00,餐饮美食,美团外卖,外卖订单,支出,32.00,零钱,支付成功\n"
        )
        result = self._ingest("wechat.csv", text)
        self.assertEqual(len(result.records), 2)
        self.assertEqual({r.category for r in result.records}, {"transport", "diet"})

    def test_minimal_renamed_headers(self) -> None:
        text = "日期,类别,金额\n2026-09-03,交通出行,23.50\n"
        result = self._ingest("minimal.csv", text)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].category, "transport")
        self.assertEqual(result.records[0].occurred_at, "2026-09-03")

    def test_amount_with_currency_symbol(self) -> None:
        text = "交易时间,交易分类,商品说明,收/支,金额\n2026-09-15,购物,京东商城,支出,\"¥59.90\"\n"
        result = self._ingest("symbol.csv", text)
        self.assertEqual(len(result.records), 1)
        self.assertAlmostEqual(result.records[0].quantity, 59.90, places=2)

    def test_unrecognizable_headers_reports_actual(self) -> None:
        text = "A,B,C\n1,2,3\n"
        result = self._ingest("weird.csv", text)
        self.assertEqual(result.records, [])
        self.assertTrue(any("实际读到的表头" in w for w in result.warnings))

class MockAdapterTest(unittest.TestCase):
    def test_deterministic_for_same_seed(self) -> None:
        raw = RawInput(source_id="mock", kind="api", payload=None, captured_at="2026-10-13")
        first = MockAdapter(seed=1).ingest(raw).records
        second = MockAdapter(seed=1).ingest(raw).records

        self.assertEqual([r.quantity for r in first], [r.quantity for r in second])
        self.assertTrue(all(r.raw and r.raw.get("mock") for r in first))


class RegistryTest(unittest.TestCase):
    def test_catalogue_reports_planned_sources(self) -> None:
        registry = default_registry()
        catalogue = {item["id"]: item for item in registry.catalogue()}

        self.assertEqual(catalogue["alipay_bill"]["status"], "connected")
        self.assertEqual(catalogue["ant_forest"]["status"], "planned")

    def test_planned_adapter_refuses_to_run(self) -> None:
        registry = default_registry()
        with self.assertRaises(NotImplementedError):
            registry.get("ant_forest").ingest(
                RawInput(source_id="ant_forest", kind="api", payload=None, captured_at="2026-10-13")
            )

    def test_unknown_adapter(self) -> None:
        with self.assertRaises(KeyError):
            AdapterRegistry().get("nope")


if __name__ == "__main__":
    unittest.main()
