"""RP5 라이브·저장 결과 청취. 표준 라이브러리 HTTP와 기존 SSH 터널을 사용한다."""

import argparse
import json
import mimetypes
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from edge_asr.qwen.viewer.runs import Library

ASSETS = Path(__file__).parent / "web"


def handler(library):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def json(self, value, status=200):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def file(self, path):
            size = path.stat().st_size
            self.send_response(200)
            self.send_header(
                "Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream"
            )
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with path.open("rb") as stream:
                while block := stream.read(65536):
                    self.wfile.write(block)

        def do_GET(self):
            received = time.perf_counter()
            try:
                if urlparse("http://" + self.headers.get("Host", "")).hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                ):
                    return self.json({"error": "localhost 접속만 지원합니다."}, 403)
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path == "/api/clock":
                    return self.json({"received_s": received, "sent_s": time.perf_counter()})
                if url.path == "/api/catalog":
                    return self.json(library.catalog())
                if url.path == "/api/devices":
                    from edge_asr.microphone import devices

                    return self.json({"devices": devices()})
                if url.path in ("/api/state", "/api/audio"):
                    run = library.runs[query["id"][0]]
                    if url.path == "/api/audio":
                        return self.file(library.audio(run))
                    cursor = int(query.get("cursor", ["0"])[0])
                    if cursor < 0:
                        raise ValueError("음수 cursor")
                    value = run.snapshot(cursor)
                    value["can_stop"] = run.id == library.active_id and bool(
                        library.process and library.process.poll() is None
                    )
                    return self.json(value)
                static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                if url.path in static:
                    return self.file(ASSETS / static[url.path])
                self.json({"error": "찾을 수 없습니다."}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                self.json({"error": str(error)}, 400)

        def do_POST(self):
            try:
                if urlparse("http://" + self.headers.get("Host", "")).hostname not in (
                    "localhost",
                    "127.0.0.1",
                    "::1",
                ):
                    return self.json({"error": "localhost 접속만 지원합니다."}, 403)
                # localhost 전용. 다른 웹 사이트가 브라우저를 통해 실행을 요청하지 못하게 한다.
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).netloc != self.headers.get("Host"):
                    return self.json({"error": "같은 출처 요청만 지원합니다."}, 403)
                if self.headers.get("Content-Type") != "application/json":
                    return self.json({"error": "application/json이 필요합니다."}, 415)
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ValueError("요청 크기 오류")
                data = json.loads(self.rfile.read(size))
                if self.path == "/api/launch":
                    return self.json(
                        {
                            "id": library.launch(
                                data["profile"],
                                data.get("selection", "all"),
                                microphone=data.get("microphone"),
                            )
                        }
                    )
                if self.path == "/api/finish":
                    return self.json(library.finish(data["id"]))
                if self.path == "/api/start":
                    return self.json(library.start(data["id"]))
                if self.path == "/api/stop":
                    return self.json(library.stop(data["id"]))
                self.json({"error": "찾을 수 없습니다."}, 404)
            except Exception as error:
                self.json({"error": str(error)}, 400)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--run", type=Path, action="append", default=[])
    parser.add_argument("--profile", type=Path, action="append", default=[])
    parser.add_argument("--manifest", type=Path, action="append", default=[])
    parser.add_argument("--ssh", help="개발 PC에서 RP5 서버 실행·localhost 포트 전달")
    parser.add_argument("--remote-root", default="/home/deepet/projects/edge-asr")
    parser.add_argument("--stdio-lifetime", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("포트는 1024~65535 범위입니다.")
    if args.ssh:
        if args.ssh.startswith("-"):
            parser.error("SSH 별칭 오류")
        argv = [
            ".venv/bin/python",
            "scripts/edge_asr.py",
            "tools",
            "listen-qwen3",
            "--port",
            str(args.port),
            "--stdio-lifetime",
        ]
        for key in ("run", "profile", "manifest"):
            for value in getattr(args, key):
                argv.extend(["--" + key, str(value)])
        remote = "cd " + shlex.quote(args.remote_root) + " && exec " + shlex.join(argv)
        command = [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-L",
            f"127.0.0.1:{args.port}:127.0.0.1:{args.port}",
            args.ssh,
            remote,
        ]
        return subprocess.call(command)
    root = Path(__file__).resolve().parents[4]
    library = Library(
        root,
        [root / p for p in args.run],
        [root / p for p in args.profile],
        [root / p for p in args.manifest],
    )
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(library))
    print(f"청취 도구: http://127.0.0.1:{server.server_port} — 종료 Ctrl+C", flush=True)
    if args.stdio_lifetime:

        def disconnected():
            while sys.stdin.buffer.read(1):
                pass
            server.shutdown()

        threading.Thread(target=disconnected, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
