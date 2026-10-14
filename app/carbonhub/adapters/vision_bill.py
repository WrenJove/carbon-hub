"""电费 / 加油单据的拍照识别适配器。

流程：图片 ->（多模态调用 或 本地缓存）-> 解析校验 -> 活动记录

断网兜底：每次识别成功都按图片内容哈希存一份缓存；现场没网就读缓存，
演示照样能走完。缓存里存的是模型原文，重新解析不必再调模型。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from ..models import AdapterCapability, IngestResult, RawInput
from ..recognition import (
    BILL_PROMPT,
    VisionUnavailable,
    call_vision,
    extract_json,
    to_records,
    validate,
)
from .base import CarbonAdapter


def cache_key(path: str | Path) -> str:
    """图片内容哈希，同一张图重复上传命中同一份缓存。"""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


class VisionBillAdapter(CarbonAdapter):
    capability = AdapterCapability(
        id="utility_bill",
        display_name="电费 / 加油单据（拍照识别）",
        status="available",
        ingest=("file-image",),
        categories=("electricity", "fuel"),
        note="需要 CARBONHUB_VISION_API_KEY；无网时自动读本地缓存",
    )

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else Path("out/recognition_cache")

    def ingest(self, raw: RawInput) -> IngestResult:
        result = IngestResult()
        items = raw.payload if isinstance(raw.payload, (list, tuple)) else [raw.payload]

        for item in items:
            path = Path(str(item))
            if not path.exists():
                result.warnings.append(f"找不到文件：{path}")
                continue

            key = cache_key(path)
            text = self._recognize(path, key, result)
            if text is None:
                continue

            try:
                payload = extract_json(text)
            except ValueError as exc:
                result.warnings.append(f"{path.name}：{exc}")
                continue

            doc = validate(payload)
            fallback = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
            if doc.doc_type == "electricity" and not (
                doc.electricity.get("periodEnd") or doc.electricity.get("billDate")
            ):
                doc.warnings.append("单据上没有计费周期，暂按图片日期归账")
            if doc.doc_type == "fuel" and not doc.fuel.get("date"):
                doc.warnings.append("单据上没有日期，暂按图片日期归账")

            records, warnings = to_records(doc, self.capability.id, key, fallback, path.name)
            result.records.extend(records)
            result.warnings.extend(f"{path.name}：{w}" for w in warnings)

        return result

    # --- 内部 ---------------------------------------------------------------

    def _recognize(self, path: Path, key: str, result: IngestResult) -> str | None:
        cache_file = self.cache_dir / f"{key}.json"
        try:
            text = call_vision(path, BILL_PROMPT)
        except VisionUnavailable as exc:
            if cache_file.exists():
                result.warnings.append(f"{path.name}：{exc}；已改用本地缓存结果")
                return json.loads(cache_file.read_text(encoding="utf-8"))["text"]
            result.warnings.append(f"{path.name}：{exc}，且无缓存，这条跳过")
            return None

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {"file": path.name, "text": text, "savedAt": datetime.now().isoformat(timespec="seconds")},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return text
