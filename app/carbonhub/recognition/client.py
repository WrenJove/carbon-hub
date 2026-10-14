"""多模态调用：图片 -> 模型 -> 文本。

只用标准库（urllib / base64 / json），不引第三方包。
配置全部来自环境变量，密钥不写进代码：

    CARBONHUB_VISION_API_KEY   必填，没有就抛 VisionUnavailable
    CARBONHUB_VISION_BASE_URL  默认 https://api.deepseek.com
    CARBONHUB_VISION_MODEL     默认 deepseek-flash（原生多模态）

接口是 OpenAI 兼容格式，换服务商只改这两个环境变量。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 45.0


class VisionUnavailable(RuntimeError):
    """没配密钥、或者调用失败。调用方应该回落到本地缓存。"""


def image_to_data_url(path: str | Path) -> str:
    """图片转 data URL（base64 内联，不用先上传）。"""

    data = Path(path).read_bytes()
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def build_request_body(image_path: str | Path, prompt: str, model: str) -> dict:
    return {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
                ],
            }
        ],
    }


def call_vision(
    image_path: str | Path,
    prompt: str,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 1,
) -> str:
    """把图片发出去，拿回模型的文字回复。失败抛 VisionUnavailable。"""

    api_key = os.environ.get("CARBONHUB_VISION_API_KEY")
    if not api_key:
        raise VisionUnavailable("未配置 CARBONHUB_VISION_API_KEY")

    base_url = os.environ.get("CARBONHUB_VISION_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("CARBONHUB_VISION_MODEL", DEFAULT_MODEL)
    payload = json.dumps(build_request_body(image_path, prompt, model)).encode("utf-8")

    last_error: Exception | None = None
    for _ in range(retries + 1):
        request = urllib.request.Request(
            url=f"{base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc

    raise VisionUnavailable(f"多模态调用失败：{last_error}")
