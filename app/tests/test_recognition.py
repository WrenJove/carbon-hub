"""识别层的验收用例。全部离线跑，用假的模型回复当输入。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from carbonhub.adapters.vision_bill import VisionBillAdapter, cache_key
from carbonhub.models import RawInput
from carbonhub.recognition import extract_json, to_records, validate

GOOD_ELECTRICITY = {
    "docType": "electricity",
    "electricity": {
        "accountNo": "EXAMPLE-ACCOUNT-A",
        "periodStart": "2026-09-01",
        "periodEnd": "2026-09-30",
        "kwh": 72,
        "amountCny": 34,  # 价税合计，含增值税与代收基金
        "unitPrice": 0.4118,
        "peakKwh": 41,
        "valleyKwh": 31,
    },
    "confidence": 0.95,
    "notes": "",
}

GOOD_FUEL = {
    "docType": "fuel",
    "fuel": {"date": "2026-10-11", "fuelGrade": "95号汽油", "liters": 28.23,
             "unitPrice": 8.07, "amountCny": 227.8},
    "confidence": 0.9,
}


class ExtractJsonTest(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(extract_json('{"a":1}')["a"], 1)

    def test_fenced_json(self) -> None:
        self.assertEqual(extract_json('```json\n{"a":1}\n```')["a"], 1)

    def test_json_with_prose(self) -> None:
        self.assertEqual(extract_json('好的，结果如下：{"a":1} 以上。')["a"], 1)

    def test_no_json_raises(self) -> None:
        with self.assertRaises(ValueError):
            extract_json("这张图我看不清")


class ValidateTest(unittest.TestCase):
    def test_good_electricity_passes(self) -> None:
        doc = validate(GOOD_ELECTRICITY)
        self.assertEqual(doc.doc_type, "electricity")
        self.assertEqual(doc.electricity["kwh"], 72.0)
        self.assertEqual(doc.warnings, [])
        self.assertGreaterEqual(doc.confidence, 0.9)

    def test_arithmetic_mismatch_lowers_confidence(self) -> None:
        payload = json.loads(json.dumps(GOOD_ELECTRICITY))
        payload["electricity"]["amountCny"] = 900  # 与实际对不上
        doc = validate(payload)
        self.assertTrue(any("比值异常" in w for w in doc.warnings))
        self.assertLess(doc.confidence, 0.9)

    def test_missing_kwh_is_reported_not_guessed(self) -> None:
        payload = json.loads(json.dumps(GOOD_ELECTRICITY))
        payload["electricity"].update({"kwh": None, "peakKwh": None, "valleyKwh": None})
        doc = validate(payload)
        self.assertIsNone(doc.electricity["kwh"])
        self.assertTrue(any("没读到用电量" in w for w in doc.warnings))
        records, _ = to_records(doc, "src", "k", "2026-10-01")
        self.assertEqual(records, [])

    def test_peak_plus_valley_derives_kwh_with_penalty(self) -> None:
        payload = json.loads(json.dumps(GOOD_ELECTRICITY))
        payload["electricity"]["kwh"] = None
        doc = validate(payload)
        self.assertEqual(doc.electricity["kwh"], 72.0)
        self.assertTrue(any("峰谷相加" in w for w in doc.warnings))

    def test_unknown_doc_type_warns(self) -> None:
        doc = validate({"docType": "unknown", "confidence": 0.2})
        self.assertTrue(any("认不出票种" in w for w in doc.warnings))

    def test_fuel_arithmetic(self) -> None:
        doc = validate(GOOD_FUEL)
        self.assertEqual(doc.fuel["liters"], 28.23)
        self.assertEqual(doc.warnings, [])


class ToRecordsTest(unittest.TestCase):
    def test_electricity_record(self) -> None:
        records, warnings = to_records(validate(GOOD_ELECTRICITY), "utility_bill", "doc1", "2026-10-01")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].category, "electricity")
        self.assertEqual(records[0].unit, "kWh")
        self.assertEqual(records[0].occurred_at, "2026-09-30")
        self.assertEqual(warnings, [])

    def test_fuel_record_uses_document_date(self) -> None:
        records, _ = to_records(validate(GOOD_FUEL), "utility_bill", "doc2", "2026-10-01")
        self.assertEqual(records[0].category, "fuel")
        self.assertEqual(records[0].occurred_at, "2026-10-11")


    def test_bill_date_used_when_no_period(self) -> None:
        """像电费通知单、收费收据这类没有计费周期的，退回到单据日期。"""

        payload = {
            "docType": "electricity",
            "electricity": {"kwh": 2525, "amountCny": 1843.69, "billDate": "2026-08-01"},
            "confidence": 0.9,
        }
        records, warnings = to_records(validate(payload), "utility_bill", "shop", "2026-10-14")
        self.assertEqual(records[0].occurred_at, "2026-08-01")
        self.assertEqual(warnings, [])

    def test_falls_back_to_image_date_only_when_nothing_else(self) -> None:
        payload = {"docType": "electricity", "electricity": {"kwh": 100}, "confidence": 0.8}
        records, _ = to_records(validate(payload), "utility_bill", "k", "2026-10-14")
        self.assertEqual(records[0].occurred_at, "2026-10-14")

class VisionBillAdapterOfflineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.image = Path(self.tmp.name) / "bill.jpg"
        self.image.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 32)
        self.cache_dir = Path(self.tmp.name) / "cache"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _raw(self) -> RawInput:
        return RawInput(source_id="utility_bill", kind="image", payload=str(self.image), captured_at="2026-10-14")

    def test_without_key_and_cache_reports_and_skips(self) -> None:
        adapter = VisionBillAdapter(cache_dir=self.cache_dir)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CARBONHUB_VISION_API_KEY", None)
            result = adapter.ingest(self._raw())
        self.assertEqual(result.records, [])
        self.assertTrue(any("无缓存" in w for w in result.warnings))

    def test_falls_back_to_cache(self) -> None:
        """断网兜底：有缓存时不用联网也能出记录。"""

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / f"{cache_key(self.image)}.json").write_text(
            json.dumps({"file": "bill.jpg", "text": json.dumps(GOOD_ELECTRICITY, ensure_ascii=False)}),
            encoding="utf-8",
        )
        adapter = VisionBillAdapter(cache_dir=self.cache_dir)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CARBONHUB_VISION_API_KEY", None)
            result = adapter.ingest(self._raw())

        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].unit, "kWh")
        self.assertTrue(any("已改用本地缓存" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
