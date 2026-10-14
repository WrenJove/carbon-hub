"""数据探查工具的验收用例。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from carbonhub.profile_data import _hints, _read, profile_rows, render


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _read_text(self, name: str, text: str, encoding: str = "utf-8"):
        path = self.dir / name
        path.write_bytes(text.encode(encoding))
        return _read(path)

    def test_reads_csv_and_reports_encoding(self):
        rows, encoding = self._read_text("a.csv", "时间,电量\n2026-09-01,10\n2026-09-02,20\n")
        self.assertEqual(len(rows), 2)
        self.assertIn("utf", encoding)

    def test_reads_gbk_csv(self):
        """国内平台导出的表格常见 GBK，读不出来这工具在现场就是废的。"""

        rows, _ = self._read_text("gbk.csv", "时间,电量\n2026-09-01,10\n", encoding="gbk")
        self.assertEqual(rows[0]["电量"], "10")

    def test_reads_json_list(self):
        rows, _ = self._read_text("a.json", '[{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]')
        self.assertEqual(len(rows), 2)

    def test_reads_json_nested_list(self):
        rows, _ = self._read_text("b.json", '{"data": [{"a": 1}, {"a": 2}]}')
        self.assertEqual(len(rows), 2)

    def test_column_kinds_are_inferred(self):
        rows = [
            {"t": "2026-09-01 10:00:00", "kwh": "10.5", "name": "站A"},
            {"t": "2026-09-02 11:00:00", "kwh": "20", "name": "站B"},
        ]
        profiles = {p.name: p for p in profile_rows(rows)}
        self.assertEqual(profiles["t"].kind, "时间")
        self.assertEqual(profiles["kwh"].kind, "数值")
        self.assertEqual(profiles["name"].kind, "文本")
        self.assertEqual(profiles["kwh"].median, 15.25)

    def test_missing_rate_and_hints(self):
        rows = [{"a": "1", "b": ""}, {"a": "2", "b": ""}, {"a": "3", "b": "x"}]
        profiles = profile_rows(rows)
        hints = _hints(profiles, rows)
        self.assertTrue(any("是空的" in h for h in hints))

    def test_duplicate_rows_are_flagged(self):
        rows = [{"a": "1"}, {"a": "1"}]
        self.assertTrue(any("完全重复" in h for h in _hints(profile_rows(rows), rows)))

    def test_render_contains_table_and_hints(self):
        rows = [{"时间": "2026-09-01", "电量": "12.5"}]
        text = render(rows, profile_rows(rows), "sample.csv", "utf-8")
        self.assertIn("列清单", text)
        self.assertIn("一眼看点", text)
        self.assertIn("电量", text)

    def test_empty_file_does_not_crash(self):
        rows, _ = self._read_text("empty.csv", "a,b\n")
        self.assertEqual(rows, [])
        self.assertIn("记录数：0", render(rows, profile_rows(rows), "empty.csv", "utf-8"))


if __name__ == "__main__":
    unittest.main()
