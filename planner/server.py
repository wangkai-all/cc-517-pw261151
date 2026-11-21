"""HTTP server (standard library only): JSON API + static files.

Endpoints
---------
GET  /                     -> the single-page app
GET  /static/<file>        -> frontend assets
GET  /samples/<file>       -> sample datasets
POST /api/calibrate        -> run calibration, return plan or conflict info
POST /api/publish          -> re-run calibration server-side and store an
                              immutable snapshot (input + result + hash)
GET  /api/published        -> list published plans
GET  /api/published/<id>   -> fetch one published plan
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .calibrate import calibrate, ValidationError
from . import storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(ROOT, "static")
SAMPLES_DIR = os.path.join(ROOT, "samples")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".ico": "image/x-icon",
}

MAX_CELLS = 256  # pure-python solver: keep grids within a sane size


def _reject_nan(obj):
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise ValidationError("数值必须是有限数字")
    elif isinstance(obj, dict):
        for v in obj.values():
            _reject_nan(v)
    elif isinstance(obj, list):
        for v in obj:
            _reject_nan(v)


class Handler(BaseHTTPRequestHandler):
    server_version = "ForecastPlanner/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ------------------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 10 * 1024 * 1024:
            raise ValidationError("请求体为空或过大")
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError("JSON 解析失败：%s" % exc)
        _reject_nan(obj)
        return obj

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send_file(os.path.join(STATIC_DIR, "index.html"))
            return
        if path == "/api/published":
            self._send_json({"items": storage.list_published(self.server.db_path)})
            return
        if path.startswith("/api/published/"):
            try:
                pid = int(path.rsplit("/", 1)[1])
            except ValueError:
                self._send_json({"error": "无效的发布编号"}, 400)
                return
            rec = storage.get_published(self.server.db_path, pid)
            if rec is None:
                self._send_json({"error": "发布记录不存在"}, 404)
            else:
                self._send_json(rec)
            return
        for prefix, base in (("/static/", STATIC_DIR), ("/samples/", SAMPLES_DIR)):
            if path.startswith(prefix):
                rel = path[len(prefix):]
                full = os.path.normpath(os.path.join(base, rel))
                if not full.startswith(base + os.sep):
                    self._send_json({"error": "forbidden"}, 403)
                    return
                self._send_file(full)
                return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/calibrate":
                payload = self._read_json()
                self._check_size(payload)
                self._send_json(calibrate(payload))
            elif path == "/api/publish":
                self._handle_publish()
            else:
                self._send_json({"error": "not found"}, 404)
        except ValidationError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json({"error": "服务器内部错误：%s" % exc}, 500)

    # -- handlers -----------------------------------------------------------
    def _check_size(self, payload):
        nr = len(payload.get("regions") or [])
        nc = len(payload.get("categories") or [])
        if nr * nc > MAX_CELLS:
            raise ValidationError(
                "网格过大（%d×%d=%d 个单元格，上限 %d）；请减少地区或品类数量"
                % (nr, nc, nr * nc, MAX_CELLS))

    def _handle_publish(self):
        body = self._read_json()
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("发布名称不能为空")
        name = name.strip()[:120]
        payload = body.get("input")
        if not isinstance(payload, dict):
            raise ValidationError("缺少计划输入")
        self._check_size(payload)
        # The server re-solves from the frozen input so the published record
        # is guaranteed to match its own data, not whatever the client saw.
        result = calibrate(payload)
        if result.get("status") != "optimal":
            self._send_json({
                "error": "没有可行的校准方案，不能发布",
                "calibration": result,
            }, 409)
            return
        rec = storage.insert_published(self.server.db_path, name, payload, result)
        self._send_json({"ok": True, "record": rec})


def make_server(host="127.0.0.1", port=8000, db_path=None):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_path = db_path or storage.DEFAULT_DB
    return httpd


def main():
    import argparse

    parser = argparse.ArgumentParser(description="地区×品类预测校准计划表")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="SQLite 数据库路径")
    args = parser.parse_args()
    httpd = make_server(args.host, args.port, args.db)
    print("服务已启动: http://%s:%d  (数据库: %s)" % (args.host, args.port, httpd.db_path))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
