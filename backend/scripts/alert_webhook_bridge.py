"""Alertmanager → 飞书 告警桥（文档 10.4）。

Alertmanager 原生不支持飞书机器人，故用本服务接收其 webhook，转换成飞书
自定义机器人要求的 {"msg_type": "text", "content": {"text": "..."}} 格式后转发。

纯标准库实现，无第三方依赖，直接复用后端镜像运行：
    python scripts/alert_webhook_bridge.py

环境变量：
    PORT=8080                    # 监听端口
    FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
                                 # 留空则只在日志打印，不外发（便于本地联调）
"""

from __future__ import annotations

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "").strip()

_STATUS_ICON = {"firing": "🚨", "resolved": "✅"}


def _labels_text(labels: dict) -> str:
    keys = [k for k in ("source", "severity", "job", "instance") if labels.get(k)]
    return " ".join(f"{k}={labels[k]}" for k in keys)


def format_alerts(payload: dict) -> str:
    """把 Alertmanager 载荷格式化为飞书文本消息。"""
    alerts = payload.get("alerts") or []
    lines = [f"AlleyBite 告警 · {len(alerts)} 条"]
    for alert in alerts:
        status = alert.get("status", "firing")
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        icon = _STATUS_ICON.get(status, "ℹ️")
        name = labels.get("alertname", "Alert")
        head = f"{icon} [{status.upper()}] {name}"
        extra = _labels_text(labels)
        if extra:
            head += f"（{extra}）"
        lines.append(head)
        if annotations.get("summary"):
            lines.append(f"  {annotations['summary']}")
        if annotations.get("description"):
            lines.append(f"  {annotations['description']}")
        if alert.get("startsAt"):
            lines.append(f"  开始时间：{alert['startsAt']}")
    return "\n".join(lines)


def send_to_feishu(text: str) -> None:
    if not FEISHU_WEBHOOK_URL:
        print("[alert-bridge] 未配置 FEISHU_WEBHOOK_URL，仅打印：\n" + text, flush=True)
        return
    body = json.dumps(
        {"msg_type": "text", "content": {"text": text}}, ensure_ascii=False
    ).encode("utf-8")
    req = urllib.request.Request(
        FEISHU_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[alert-bridge] 已转发飞书，HTTP {resp.status}", flush=True)
    except Exception as exc:  # noqa: BLE001  转发失败不得让 Alertmanager 重试风暴
        print(f"[alert-bridge] 转发飞书失败：{exc}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: bytes = b"ok") -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802  标准库接口名
        if self.path == "/health":
            self._reply(200)
        else:
            self._reply(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/alert":
            self._reply(404, b"not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"[alert-bridge] 载荷解析失败：{exc}", flush=True)
            self._reply(400, b"bad request")
            return
        send_to_feishu(format_alerts(payload))
        self._reply(200)

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        print(f"[alert-bridge] {fmt % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[alert-bridge] 监听 :{PORT}，飞书={'已配置' if FEISHU_WEBHOOK_URL else '未配置'}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
