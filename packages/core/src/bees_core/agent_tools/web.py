"""Leitura da web pública a partir do worker, sem cookies, credenciais ou rede privada.

Cada salto de redirecionamento é validado: somente http(s), portas comuns e
endereços globais. O endereço efetivamente conectado é conferido antes de ler o
corpo. Proxy explícito da instalação (``BEES_WEB_PROXY``) é opcional; proxies
do ambiente nunca são herdados implicitamente.
"""

import asyncio
import io
import ipaddress
import json
import os
import re
import socket
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

import httpx

MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) BeesAssistant/0.1 (+https://github.com/L0tus-Program/Bees)"
)
ALLOWED_PORTS = {80, 443, 8080, 8443}
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
)


class WebError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _blocked_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast or ip in ipaddress.ip_network("100.64.0.0/10")


async def _resolve(host: str, port: int, *, allow_private: bool) -> list[str]:
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            raise WebError("dns_failed") from None
        addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise WebError("dns_failed")
    if not allow_private and any(_blocked_address(address) for address in addresses):
        raise WebError("private_address_blocked")
    return addresses


def _checked_url(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError("unsupported_url")
    if parts.username or parts.password:
        raise WebError("credentials_in_url")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise WebError("port_blocked")
    return parts.scheme, parts.hostname.lower(), port


class _Extractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "canvas"}
    BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "footer",
        "blockquote",
        "pre",
        "main",
        "aside",
        "nav",
    }

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.title = ""
        self.description = ""
        self.links: list[dict] = []
        self._skip = 0
        self._in_title = False
        self._link: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (attrs.get("name") or attrs.get("property", "")).lower() in (
            "description",
            "og:description",
        ):
            self.description = self.description or (attrs.get("content") or "").strip()
        elif tag == "a" and attrs.get("href"):
            href = urljoin(self.base, attrs["href"])
            if href.startswith(("http://", "https://")):
                self._link = {"url": href.split("#")[0], "text": ""}
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link is not None:
            text = " ".join(self._link["text"].split())
            if text and len(self.links) < 400:
                self.links.append({"url": self._link["url"], "text": text[:160]})
            self._link = None
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._link is not None:
            self._link["text"] += data

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        out, blank = [], False
        for line in lines:
            if not line:
                if not blank and out:
                    out.append("")
                blank = True
                continue
            out.append(line)
            blank = False
        return "\n".join(out).strip()


def extract_html(html: str, base: str) -> dict:
    parser = _Extractor(base)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    seen, links = set(), []
    for link in parser.links:
        if link["url"] not in seen:
            seen.add(link["url"])
            links.append(link)
    return {
        "title": " ".join(parser.title.split())[:300],
        "description": parser.description[:500],
        "text": parser.text(),
        "links": links,
    }


def extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = []
    for index, page in enumerate(reader.pages[:200]):
        try:
            pages.append(f"[página {index + 1}]\n" + (page.extract_text() or ""))
        except Exception:
            pages.append(f"[página {index + 1}] (texto indisponível)")
    return "\n\n".join(pages).strip()


class WebClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        proxy: str | None = None,
        allow_private: bool = False,
        timeout: float = 20.0,
        resolver=None,
    ):
        self.resolver = resolver
        self.transport = transport
        self.proxy = proxy if proxy is not None else os.environ.get("BEES_WEB_PROXY") or None
        self.allow_private = allow_private
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self.transport,
            proxy=self.proxy,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
        )

    async def get(self, url: str, *, method="GET", data=None, accept=None) -> dict:
        async with self._client() as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                _, host, port = _checked_url(current)
                if self.resolver is not None:
                    addresses = await self.resolver(host, port)
                    if not self.allow_private and any(map(_blocked_address, addresses)):
                        raise WebError("private_address_blocked")
                else:
                    await _resolve(host, port, allow_private=self.allow_private)
                request = client.build_request(
                    method,
                    current,
                    data=data,
                    headers={"Accept": accept or "text/html,application/xhtml+xml,*/*;q=0.8"},
                )
                try:
                    response = await client.send(request, stream=True)
                except httpx.TimeoutException:
                    raise WebError("timeout") from None
                except httpx.HTTPError:
                    raise WebError("connection_failed") from None
                try:
                    if self.transport is None and self.proxy is None and not self.allow_private:
                        stream = response.extensions.get("network_stream")
                        peer = stream.get_extra_info("server_addr") if stream else None
                        if peer and _blocked_address(peer[0]):
                            raise WebError("private_address_blocked")
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise WebError("redirect_invalid")
                        current = urljoin(current, location)
                        if response.status_code == 303:
                            method, data = "GET", None
                        continue
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_BYTES:
                            raise WebError("response_too_large")
                    return {
                        "url": url,
                        "final_url": str(response.url),
                        "status": response.status_code,
                        "content_type": response.headers.get("content-type", "")
                        .split(";")[0]
                        .strip()
                        .lower(),
                        "encoding": response.encoding or "utf-8",
                        "content": bytes(content),
                    }
                finally:
                    await response.aclose()
            raise WebError("too_many_redirects")

    async def fetch(self, url: str, max_chars: int = 12000) -> dict:
        raw = await self.get(url)
        kind = raw["content_type"]
        result = {
            "url": raw["url"],
            "final_url": raw["final_url"],
            "status": raw["status"],
            "content_type": kind,
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if kind == "application/pdf" or raw["content"][:5] == b"%PDF-":
            try:
                text = extract_pdf(raw["content"])
            except Exception:
                raise WebError("pdf_unreadable") from None
            result |= {"title": "", "text": text, "links": []}
        elif kind in TEXT_TYPES or kind.startswith("text/") or not kind:
            try:
                decoded = raw["content"].decode(raw["encoding"], errors="replace")
            except LookupError:
                decoded = raw["content"].decode("utf-8", errors="replace")
            if kind in ("text/html", "application/xhtml+xml") or (
                not kind and "<html" in decoded[:2000].lower()
            ):
                page = extract_html(decoded, raw["final_url"])
                result |= {
                    "title": page["title"],
                    "description": page["description"],
                    "text": page["text"],
                    "links": page["links"][:40],
                }
            else:
                result |= {"title": "", "text": decoded, "links": []}
        else:
            raise WebError("unsupported_content_type", kind[:100])
        text = result["text"]
        result["truncated"] = len(text) > max_chars
        result["text"] = text[:max_chars]
        result["total_chars"] = len(text)
        return result

    async def search(self, query: str, max_results: int = 8) -> dict:
        provider = os.environ.get("BEES_SEARCH_PROVIDER", "duckduckgo").lower()
        if provider == "searxng" and os.environ.get("BEES_SEARXNG_URL"):
            results = await self._searxng(query, max_results)
        elif provider == "brave" and os.environ.get("BEES_BRAVE_API_KEY"):
            results = await self._brave(query, max_results)
        else:
            provider = "duckduckgo"
            results = await self._duckduckgo(query, max_results)
        return {
            "query": query,
            "provider": provider,
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "results": results[:max_results],
        }

    async def _duckduckgo(self, query: str, max_results: int) -> list[dict]:
        base = os.environ.get("BEES_DUCKDUCKGO_URL", "https://html.duckduckgo.com/html/")
        raw = await self.get(base, method="POST", data={"q": query, "kl": "wt-wt"})
        if raw["status"] != 200:
            raise WebError("search_failed", f"status {raw['status']}")
        return parse_duckduckgo(raw["content"].decode("utf-8", errors="replace"), max_results)

    async def _searxng(self, query: str, max_results: int) -> list[dict]:
        base = os.environ["BEES_SEARXNG_URL"].rstrip("/")
        client = WebClient(transport=self.transport, proxy=self.proxy, allow_private=True)
        raw = await client.get(
            f"{base}/search?q={quote_plus(query)}&format=json", accept="application/json"
        )
        data = json.loads(raw["content"] or b"{}")
        return [
            {
                "title": item.get("title", "")[:300],
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:500],
            }
            for item in data.get("results", [])[:max_results]
            if str(item.get("url", "")).startswith(("http://", "https://"))
        ]

    async def _brave(self, query: str, max_results: int) -> list[dict]:
        async with self._client() as client:
            try:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": max_results},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": os.environ["BEES_BRAVE_API_KEY"],
                    },
                )
            except httpx.HTTPError:
                raise WebError("search_failed") from None
        if response.status_code != 200:
            raise WebError("search_failed", f"status {response.status_code}")
        data = response.json()
        return [
            {
                "title": item.get("title", "")[:300],
                "url": item.get("url", ""),
                "snippet": re.sub(r"<[^>]+>", "", item.get("description", ""))[:500],
            }
            for item in data.get("web", {}).get("results", [])[:max_results]
        ]


class _DuckParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.results.append(
                {"title": "", "url": _ddg_target(attrs.get("href", "")), "snippet": ""}
            )
            self._field = "title"
        elif "result__snippet" in classes and self.results:
            self._field = "snippet"

    def handle_endtag(self, tag):
        if tag in ("a", "td", "div"):
            self._field = None

    def handle_data(self, data):
        if self._field and self.results:
            self.results[-1][self._field] += data


def _ddg_target(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.path.startswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [""])[0]
        return unescape(target)
    return href


def parse_duckduckgo(html: str, max_results: int) -> list[dict]:
    parser = _DuckParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    results = []
    for item in parser.results:
        url = item["url"]
        if not url.startswith(("http://", "https://")) or "duckduckgo.com/y.js" in url:
            continue
        results.append(
            {
                "title": " ".join(item["title"].split())[:300],
                "url": url,
                "snippet": " ".join(item["snippet"].split())[:500],
            }
        )
        if len(results) >= max_results:
            break
    return results
