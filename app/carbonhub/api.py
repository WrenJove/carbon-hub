"""只读的账本接口：让第三方（比如碳普惠平台）能直接读到你这份账。

零依赖，标准库 http.server。默认只绑本机，不鉴权，也不要暴露到公网。

跑法：
    python -m carbonhub.api                 # http://127.0.0.1:8787
    python -m carbonhub.api --port 9000

先跑一次 cli 把账本写出来，接口才有东西可读：
    python -m carbonhub.cli --receipts ../fixtures/real_receipts_20261013.json

接口：
    GET /health    存活检查
    GET /ledger    账本（分范围、分类目、逐条明细）
    GET /advice    减排建议
    GET /passport  对外凭证（含指纹链）
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

OUT = Path(__file__).resolve().parents[1] / "out"

ROUTES = {
    "/health": None,
    "/ledger": "ledger.json",
    "/advice": "advice.json",
    "/passport": "passport.json",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "carbon-hub/0.1"

    def _send(self, code: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 允许网页端直接读，方便现场演示第三方调用
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (标准库要求的大小写)
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/":
            self._send(200, {
                "service": "carbon-hub",
                "endpoints": sorted(ROUTES),
                "note": "只读；账本细目见 /ledger",
            })
            return

        if path not in ROUTES:
            self._send(404, {"error": "没有这个接口", "endpoints": sorted(ROUTES)})
            return

        if path == "/health":
            self._send(200, {"ok": True})
            return

        target = OUT / str(ROUTES[path])
        if not target.exists():
            self._send(503, {"error": f"账本还没生成（缺 {target.name}），先跑一次 carbonhub.cli"})
            return
        self._send(200, json.loads(target.read_text(encoding="utf-8")))

    def log_message(self, fmt, *args):  # 保持安静，只留一行访问记录
        print(f"[api] {self.address_string()} {fmt % args}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="carbon-hub 只读接口")
    parser.add_argument("--host", default="127.0.0.1", help="默认只绑本机")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"carbon-hub 接口已启动：http://{args.host}:{args.port}")
    print("  GET /ledger   账本")
    print("  GET /advice   减排建议")
    print("  GET /passport 对外凭证")
    print("Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
