"""Serve app/static on http://localhost:8000 with HTTP Range support.

The browser reads the database chunks in app/static/db/ with range requests; Python's built-in http.server
ignores the Range header, so this adds it. Usage:  python scripts/serve.py [port]
"""
import http.server, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start, end = int(m[1]), min(int(m[2] or size - 1), size - 1)
        if start >= size:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        n = getattr(self, "_remaining", None)
        if n is None:
            return super().copyfile(source, outputfile)
        outputfile.write(source.read(n))
        self._remaining = None

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"NatRep Explorer on http://localhost:{PORT}")
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()
