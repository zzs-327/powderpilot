"""受限官方页面候选采集器（只产出链接级候选，不写知识库）。

manifest 为 JSON 数组，或含 ``candidates`` 与可选 ``allowed_domains`` 的对象。
允许域名必须显式配置，且按完整主机名精确匹配；脚本只接受 HTTPS/443，
只处理 robots 允许的 HTML 页面，不跟随重定向、不下载媒体。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 512 * 1024
DEFAULT_TIMEOUT = 10.0
DEFAULT_SUMMARY_CHARS = 500
USER_AGENT = "PowderPilot-official-check/1.0"


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: list[str] = []
        self.h1: list[str] = []
        self._capture: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._capture is None and tag in {"title", "h1"}:
            self._capture, self._depth = tag, 1
        elif self._capture is not None:
            self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._capture is None and tag in {"title", "h1"}:
            target = self.title if tag == "title" else self.h1
            target.append("")

    def handle_endtag(self, tag: str) -> None:
        if self._capture is None:
            return
        self._depth -= 1
        if self._depth <= 0:
            self._capture, self._depth = None, 0

    def handle_data(self, data: str) -> None:
        if self._capture == "title":
            self.title.append(data)
        elif self._capture == "h1":
            self.h1.append(data)


def _clean(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _resolve_public(hostname: str) -> bool:
    """解析目标主机；仅当所有解析地址均为公网单播地址时通过。"""
    try:
        answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        addresses = {ipaddress.ip_address(answer[4][0].split("%", 1)[0]) for answer in answers}
        return bool(addresses) and all(address.is_global for address in addresses)
    except (OSError, ValueError):
        return False


def _validate_url(url: str, allowed_domains: set[str]) -> tuple[bool, str]:
    if not isinstance(url, str) or not url or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return False, "URL is empty or contains whitespace/control characters"
    if "\\" in url:
        return False, "backslashes are not allowed in URLs"
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False, "URL has an invalid authority or port"
    if parsed.scheme.lower() != "https":
        return False, "only HTTPS URLs are allowed"
    if parsed.username is not None or parsed.password is not None:
        return False, "URL credentials are not allowed"
    if port not in (None, 443):
        return False, "only HTTPS port 443 is allowed"
    if not parsed.hostname:
        return False, "URL has no hostname"
    host = parsed.hostname.lower().rstrip(".")
    domains = {str(domain).lower().rstrip(".") for domain in allowed_domains}
    if not domains:
        return False, "allowed_domains must be explicitly configured"
    if host not in domains:
        return False, "hostname is not an exact allowed domain"
    if not _resolve_public(host):
        return False, "hostname does not resolve exclusively to public addresses"
    return True, ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """让 3xx 原样返回给调用者，所有跳转留待人工审核。"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _make_opener(url: str, allowed_domains: set[str]):
    # 遵循 utils/net.py：远程请求遵循系统代理；本机目标不继承代理。
    try:
        from utils.net import url_opener
        opener = url_opener(url)
    except ImportError:
        opener = urllib.request.build_opener()
    opener.handlers = [h for h in opener.handlers if not isinstance(h, urllib.request.HTTPRedirectHandler)]
    opener.add_handler(_NoRedirect())
    return opener


def _robots_allowed(url: str, timeout: float, opener: Any) -> bool:
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(("https", parsed.netloc, "/robots.txt", "", ""))
    req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                return False
            body = response.read(DEFAULT_MAX_BYTES + 1)
            if len(body) > DEFAULT_MAX_BYTES:
                return False
        text = body.decode("utf-8", "replace")
        if "<html" in text.lower() or not re.search(r"^\s*user-agent\s*:", text, re.I | re.M):
            return False
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        # robots 缺失、跳转或不可访问时保守拒绝。
        return False


def _candidate(url: str, note: str, *, status: int = 0) -> dict[str, Any]:
    return {"fetched_at": datetime.now(timezone.utc).isoformat(), "url": url,
            "final_url": url, "status": status, "title": "", "summary": "",
            "verification": "candidate", "rights": "link_only", "notes": note}


def fetch_page(url: str, allowed_domains: set[str], *, timeout: float = DEFAULT_TIMEOUT,
               max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    """抓取单页的受限摘要；任何策略失败都保留为待人工复核候选。"""
    result = _candidate(url, "")
    ok, reason = _validate_url(url, allowed_domains)
    if not ok:
        result["notes"] = reason
        return result
    opener = _make_opener(url, allowed_domains)
    if not _robots_allowed(url, timeout, opener):
        result["notes"] = "robots.txt disallows or could not be checked without redirects"
        return result
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with opener.open(req, timeout=timeout) as response:
            status = int(response.status)
            result["status"] = status
            if 300 <= status < 400:
                result["notes"] = "redirect requires manual review; not followed"
                return result
            if not 200 <= status < 300:
                result["notes"] = f"unexpected HTTP status {status}"
                return result
            final_url = response.geturl()
            ok, reason = _validate_url(final_url, allowed_domains)
            if not ok or final_url != url:
                result["notes"] = "final URL differs or is not allowed: " + (reason or "redirect detected")
                return result
            content_type = response.headers.get_content_type()
            if content_type != "text/html":
                result["notes"] = "content is not HTML"
                return result
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                result["notes"] = f"HTML exceeds {max_bytes} byte limit"
                return result
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                result["notes"] = f"HTML exceeds {max_bytes} byte limit"
                return result
        parser = _TextParser()
        parser.feed(raw.decode("utf-8", "replace"))
        title, h1 = _clean(parser.title), _clean(parser.h1)
        result.update(final_url=final_url, title=title,
                      summary=_clean([part for part in (title, h1) if part])[:DEFAULT_SUMMARY_CHARS],
                      verification="page_checked", notes="")
    except urllib.error.HTTPError as exc:
        result["status"] = int(exc.code)
        result["notes"] = ("redirect requires manual review; not followed" if 300 <= exc.code < 400
                            else f"HTTP error {exc.code}")
    except Exception as exc:  # 网络问题作为候选备注记录，不中止批次
        result["notes"] = f"fetch failed: {type(exc).__name__}"
    return result


def collect(manifest: list[dict[str, Any]], *, max_pages: int | None = None,
            timeout: float = DEFAULT_TIMEOUT, max_bytes: int = DEFAULT_MAX_BYTES,
            default_allowed_domains: set[str] | None = None) -> list[dict[str, Any]]:
    if max_pages is not None and max_pages < 0:
        raise ValueError("max_pages must be non-negative")
    candidates = manifest[:max_pages] if max_pages is not None else manifest
    output = []
    for candidate in candidates:
        url = str(candidate.get("url", ""))
        domains = {str(x).lower().rstrip(".") for x in candidate.get("allowed_domains", [])}
        if not domains and default_allowed_domains:
            domains = set(default_allowed_domains)
        result = fetch_page(url, domains, timeout=timeout, max_bytes=max_bytes)
        result.update(kind=candidate.get("kind", "official"), resort_id=candidate.get("resort_id"))
        output.append(result)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="检查官方页面并输出链接级候选摘要")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        manifest = payload.get("candidates")
        domains = payload.get("allowed_domains", [])
    else:
        manifest, domains = payload, []
    if not isinstance(manifest, list):
        raise SystemExit("manifest must be a JSON list or an object with candidates")
    default_domains = {str(domain).lower().rstrip(".") for domain in domains}
    results = collect(manifest, max_pages=args.max_pages, default_allowed_domains=default_domains)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
