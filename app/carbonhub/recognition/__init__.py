"""识别层：单据图片 -> 结构化字段 -> 活动记录。

三块分开，便于替换与单测：
  prompt.py  票种模板与提示词（要反复调的部分，改这里不动别的）
  client.py  多模态调用（只用标准库，配置走环境变量）
  parse.py   解析 + 交叉验算（纯函数，离线可测）

铁律：模型只负责"读"，不负责"算"。抽不到就填 null，绝不推测。
"""

from .client import VisionUnavailable, call_vision
from .parse import extract_json, to_records, validate
from .prompt import BILL_PROMPT

__all__ = [
    "BILL_PROMPT",
    "VisionUnavailable",
    "call_vision",
    "extract_json",
    "to_records",
    "validate",
]
