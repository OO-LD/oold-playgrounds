"""Static file server for the built site.

Plain HTTP, no server-side logic, so what is tested is what a docs host or an
object store would deliver.
"""

import argparse
import json
import functools
import http.server
import mimetypes
from pathlib import Path

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("application/octet-stream", ".whl")
mimetypes.add_type("application/octet-stream", ".zip")


STATS = {"requests": 0, "bytes": 0}


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/__stats"):
            if "reset" in self.path:
                STATS["requests"] = 0
                STATS["bytes"] = 0
            body = json.dumps(STATS).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        STATS["requests"] += 1
        super().do_GET()

    def copyfile(self, source, outputfile):
        # Counts what actually crosses the wire, including requests issued by
        # the pyodide web worker, which page-level resource timings never see.
        while True:
            chunk = source.read(65536)
            if not chunk:
                break
            STATS["bytes"] += len(chunk)
            outputfile.write(chunk)

    def log_message(self, fmt, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parents[1] / "_output"))
    args = ap.parse_args()

    handler = functools.partial(Handler, directory=args.dir)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"serving {args.dir} on http://127.0.0.1:{args.port}/", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
