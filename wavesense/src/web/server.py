"""本地 HTTP 服务器（标准库实现，零额外依赖）。

路由：
  GET  /                     前端页面
  GET  /api/state            最新状态 + 曲线 + 模式 + 分析
  GET  /api/events           事件历史
  POST /api/events/clear     清空事件
  GET  /api/events/export    导出 CSV（带 BOM，Excel 可直接打开）
  GET  /api/serial/ports     列出可用串口
  POST /api/mode/live        切换到实时串口
  POST /api/upload           上传 .gz/.dat，切换到文件回放并分析
"""
import csv
import io
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ALLOWED_EXT = (".gz", ".dat")


class WebServer:
    def __init__(self, engine, host, port, static_dir, upload_dir):
        self.engine = engine
        self.host = host
        self.port = port
        self.static_dir = static_dir
        self.upload_dir = upload_dir
        self.httpd = None

    def start(self):
        engine = self.engine
        static_dir = self.static_dir
        upload_dir = self.upload_dir
        os.makedirs(upload_dir, exist_ok=True)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, content_type, code=200, extra=None):
                if isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if extra:
                    for k, v in extra.items():
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, obj, code=200):
                self._send(json.dumps(obj, ensure_ascii=False),
                           "application/json; charset=utf-8", code)

            def _read_body(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                return self.rfile.read(length) if length else b""

            def do_GET(self):
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    self._serve_index()
                elif path == "/api/state":
                    self._send_json(engine.snapshot())
                elif path == "/api/events":
                    self._send_json(engine.get_events())
                elif path == "/api/events/export":
                    self._serve_csv()
                elif path == "/api/serial/ports":
                    self._send_json({"ports": self._list_ports()})
                else:
                    self._send("未找到", "text/plain; charset=utf-8", 404)

            def do_POST(self):
                path = urlparse(self.path).path
                if path == "/api/events/clear":
                    self._read_body()
                    engine.clear_events()
                    self._send_json({"ok": True})
                elif path == "/api/mode/live":
                    self._switch_live()
                elif path == "/api/upload":
                    self._handle_upload()
                else:
                    self._send("未找到", "text/plain; charset=utf-8", 404)

            def _list_ports(self):
                try:
                    from src.data_source import list_serial_ports
                    out = []
                    for d, desc in list_serial_ports():
                        low = d.lower()
                        if any(k in low for k in ("debug-console", "bluetooth", "incoming-port")):
                            continue
                        out.append({"device": d, "desc": desc})
                    return out
                except Exception:
                    return []

            def _switch_live(self):
                raw = self._read_body()
                port = None
                if raw:
                    try:
                        port = json.loads(raw.decode("utf-8")).get("port")
                    except ValueError:
                        port = None
                if not port:
                    ports = self._list_ports()
                    if not ports:
                        self._send_json({"ok": False, "error": "没找到可用串口，请先接好雷达"}, 400)
                        return
                    port = ports[0]["device"]
                from src.data_source import SerialSource
                engine.switch_source(SerialSource(port), False, "live", port)
                self._send_json({"ok": True, "port": port, "mode": "live"})

            def _handle_upload(self):
                ctype = self.headers.get("Content-Type", "")
                if "boundary=" not in ctype:
                    self._send_json({"ok": False, "error": "不是有效的表单上传"}, 400)
                    return
                boundary = ctype.split("boundary=", 1)[1].strip().encode("utf-8")
                body = self._read_body()
                parsed = self._parse_multipart(body, boundary)
                if not parsed:
                    self._send_json({"ok": False, "error": "没有解析到文件"}, 400)
                    return
                filename, content = parsed
                if not filename.lower().endswith(ALLOWED_EXT):
                    self._send_json({"ok": False, "error": "只支持 .gz / .dat 文件"}, 400)
                    return
                safe_name = os.path.basename(filename).replace("/", "_").replace("\\", "_")
                save_path = os.path.join(upload_dir, safe_name)
                with open(save_path, "wb") as f:
                    f.write(content)
                from src.data_source import FileReplaySource
                engine.switch_source(FileReplaySource(save_path), True, "replay", safe_name)
                self._send_json({"ok": True, "filename": safe_name,
                                 "bytes": len(content), "mode": "replay"})

            def _parse_multipart(self, body, boundary):
                delim = b"--" + boundary
                for part in body.split(delim):
                    if b"Content-Disposition" not in part:
                        continue
                    header_end = part.find(b"\r\n\r\n")
                    if header_end < 0:
                        continue
                    headers = part[:header_end].decode("utf-8", "ignore")
                    content = part[header_end + 4:]
                    if content.endswith(b"\r\n"):
                        content = content[:-2]
                    filename = ""
                    m = re.search(r'filename="([^"]*)"', headers)
                    if not m:
                        m = re.search(r"filename=([^;\r\n]+)", headers)
                    if m:
                        filename = m.group(1).strip().strip('"')
                    if filename:
                        return filename, content
                return None

            def _serve_index(self):
                index = os.path.join(static_dir, "index.html")
                try:
                    with open(index, "rb") as f:
                        body = f.read()
                    self._send(body, "text/html; charset=utf-8")
                except OSError:
                    self._send("前端页面缺失", "text/plain; charset=utf-8", 500)

            def _serve_csv(self):
                events = engine.get_events()
                buf = io.StringIO()
                buf.write("\ufeff")
                writer = csv.writer(buf)
                writer.writerow(["时间", "事件", "帧号", "在场", "动作分级"])
                for e in events:
                    writer.writerow([e["time"], e["kind"], e["frame"],
                                     "有人" if e["presence"] else "无人", e["label"]])
                self._send(buf.getvalue(), "text/csv; charset=utf-8",
                           extra={"Content-Disposition":
                                  'attachment; filename="wavesense_events.csv"'})

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.httpd.server_address[1]
        self.httpd.serve_forever()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
