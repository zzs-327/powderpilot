import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from scripts import collect_official_sources as collector


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/page":
            body = b"<html><head><title>Snow Resort</title></head><body><h1>Official resort</h1><img src='x.jpg'></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):
        pass


def server():
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_fetch_extracts_title_and_h1_without_media(monkeypatch):
    httpd = server()
    try:
        monkeypatch.setattr(collector, "_robots_allowed", lambda *args: True)
        monkeypatch.setattr(collector, "_validate_url", lambda url, domains: (True, ""))
        monkeypatch.setattr(collector, "_make_opener", lambda url, domains: __import__("urllib.request", fromlist=["build_opener"]).build_opener(__import__("urllib.request", fromlist=["ProxyHandler"]).ProxyHandler({}), collector._NoRedirect()))
        url = f"http://127.0.0.1:{httpd.server_port}/page"
        item = collector.fetch_page(url, {"127.0.0.1"})
        assert item["status"] == 200
        assert item["title"] == "Snow Resort"
        assert item["summary"] == "Snow Resort Official resort"
        assert item["verification"] == "page_checked"
        assert "img" not in item["summary"]
    finally:
        httpd.shutdown()


def test_strict_url_policy(monkeypatch):
    monkeypatch.setattr(collector, "_resolve_public", lambda host: True)
    for url, domains, expected in [
        ("http://example.test/", {"example.test"}, "HTTPS"),
        ("https://user:pass@example.test/", {"example.test"}, "credentials"),
        ("https://example.test:8443/", {"example.test"}, "443"),
        ("https://sub.example.test/", {"example.test"}, "exact"),
        ("https://example.test/", set(), "explicitly"),
        ("https://example.test/a\\b", {"example.test"}, "backslashes"),
    ]:
        ok, reason = collector._validate_url(url, domains)
        assert not ok and expected.lower() in reason.lower()


def test_redirect_is_not_followed(monkeypatch):
    httpd = server()
    try:
        monkeypatch.setattr(collector, "_resolve_public", lambda host: True)
        monkeypatch.setattr(collector, "_validate_url", lambda url, domains: (True, ""))
        monkeypatch.setattr(collector, "_make_opener", lambda url, domains: __import__("urllib.request", fromlist=["build_opener"]).build_opener(__import__("urllib.request", fromlist=["ProxyHandler"]).ProxyHandler({}), collector._NoRedirect()))
        monkeypatch.setattr(collector, "_robots_allowed", lambda *args: True)
        url = f"http://127.0.0.1:{httpd.server_port}/redirect"
        item = collector.fetch_page(url, {"127.0.0.1"})
        assert item["verification"] == "candidate"
        assert "manual review" in item["notes"]
    finally:
        httpd.shutdown()


def test_non_public_dns_is_rejected(monkeypatch):
    monkeypatch.setattr(collector, "_resolve_public", lambda host: False)
    ok, reason = collector._validate_url("https://example.test/", {"example.test"})
    assert not ok
    assert "public" in reason


def test_collect_manifest_and_max_pages(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(collector, "fetch_page", lambda url, domains, **kwargs: {
        "fetched_at": "now", "url": url, "final_url": url, "status": 200,
        "title": "T", "summary": "T H", "notes": "", "verification": "page_checked", "rights": "link_only",
    })
    manifest = [{"url": "https://one.test", "resort_id": "one", "kind": "official"},
                {"url": "https://two.test", "resort_id": "two"}]
    out = collector.collect(manifest, max_pages=1)
    assert len(out) == 1
    assert out[0]["verification"] == "page_checked"
    assert out[0]["rights"] == "link_only"
    assert out[0]["resort_id"] == "one"


def test_cli_writes_json(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(collector, "collect", lambda manifest, max_pages=None, **kwargs: [{
        "fetched_at": "now", "url": "https://example.test", "final_url": "https://example.test",
        "status": 200, "title": "T", "summary": "T", "kind": "official", "resort_id": "x",
        "verification": "page_checked", "rights": "link_only", "notes": "",
    }])
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "out.json"
    manifest.write_text(json.dumps([{"url": "https://example.test"}]), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["collect", "--manifest", str(manifest), "--output", str(output)])
    collector.main()
    assert json.loads(output.read_text(encoding="utf-8"))[0]["rights"] == "link_only"



def test_robots_requires_real_rules():
    import io

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def __init__(self, body):
            self.body = body

        def open(self, *args, **kwargs):
            return Response(self.body)

    url = 'https://example.test/page'
    for body in (b'', b'<html><title>Error</title></html>', b'Not found'):
        assert not collector._robots_allowed(url, 1, Opener(body))
    assert collector._robots_allowed(url, 1, Opener(b'User-agent: *\nAllow: /'))
    assert not collector._robots_allowed(url, 1, Opener(b'User-agent: *\nDisallow: /'))

