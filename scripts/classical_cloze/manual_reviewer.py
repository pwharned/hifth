from __future__ import annotations

import argparse
import importlib.util
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.classical_cloze.manual_campaign import Campaign


STATIC_ROOT = Path(__file__).with_name("manual_reviewer")


class Handler(BaseHTTPRequestHandler):
    campaign: Campaign

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/current":
            self.send_json(self.campaign.current())
            return
        filename = "index.html" if path == "/" else path.lstrip("/")
        file_path = (STATIC_ROOT / filename).resolve()
        if STATIC_ROOT.resolve() not in file_path.parents or not file_path.is_file():
            self.send_error(404)
            return
        payload = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/search":
                result: Any = self.campaign.search(int(body["note_id"]), body["target"])
            elif path == "/api/skip":
                self.campaign.skip(int(body["note_id"]), body.get("reason", ""))
                result = self.campaign.current()
            elif path == "/api/approve":
                replacement = self.campaign.approve(
                    int(body["note_id"]), body["candidate_id"], body.get("translation", ""),
                )
                result = {"replacement_note_id": replacement, "current": self.campaign.current()}
            else:
                self.send_error(404)
                return
            self.send_json(result)
        except Exception as error:
            self.send_json({"error": str(error)}, status=400)

    def send_json(self, payload: Any, status: int = 200) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        print(format % args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--state", type=Path,
        default=Path("data/classical_cloze/manual-campaign.json"),
    )
    parser.add_argument(
        "--archive", type=Path,
        default=Path("data/classical_cloze/archive/manual-campaign-originals.json"),
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("manual reviewer may only bind to loopback")
    missing = [
        package for package in ("torch", "transformers", "scipy")
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        parser.error(
            "audio dependencies are missing ("
            + ", ".join(missing)
            + "); run with .venv/bin/python"
        )
    Handler.campaign = Campaign(args.state, args.archive)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Manual migration reviewer: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
