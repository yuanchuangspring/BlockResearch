"""Deterministic research tools."""

import asyncio
import ast
import io
import json
import math
import os
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import httpx
from dotenv import load_dotenv
from .runtime import env
from .recorder import record

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

def _get_brave_runtime():
    """HTTP clients and asyncio primitives belong to their creating event loop."""
    loop = asyncio.get_running_loop()
    runtime = getattr(loop, "_blockresearch_brave_runtime", None)
    if runtime is None or runtime[0].is_closed:
        runtime = (httpx.AsyncClient(timeout=20, follow_redirects=True), asyncio.Semaphore(4))
        setattr(loop, "_blockresearch_brave_runtime", runtime)
    return runtime


async def _brave_get(query, count):
    """Reuse connections and retry only timeouts, network errors, 429 and 5xx."""
    for attempt in range(1, 4):
        try:
            client, gate = _get_brave_runtime()
            async with gate:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": count, "extra_snippets": "true"},
                    headers={"X-Subscription-Token": env("BRAVE_API_KEY"), "Accept": "application/json"})
            if response.status_code == 429 or response.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {response.status_code}", request=response.request, response=response)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status is None or status == 429 or status >= 500
            if not transient or attempt == 3:
                raise
            detail = str(exc).strip() or str(status or "network error")
            record("search_retry", backend="brave", query=query, attempt=attempt,
                   error=f"{type(exc).__name__}: {detail}")
            print(f"[BRAVE RETRY] attempt {attempt}/3: {type(exc).__name__} {status or ''}", flush=True)
            await asyncio.sleep(attempt)


def _canonical_url(url):
    parsed = urlparse(str(url))
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), host, path, "", "", ""))


def _query_echo(item, query):
    terms = {x.lower() for x in re.findall(r"[\w-]+", query) if len(x) > 3}
    parsed = urlparse(item.get("url", ""))
    slug = set(re.findall(r"[\w-]+", unquote(f"{parsed.path} {parsed.query}").lower().replace("-", " ")))
    return len(terms) >= 4 and len(terms & slug) / len(terms) >= .55


def _merge_results(batches, query, limit):
    """Fuse independent indexes while bounding duplicates and one-domain floods."""
    merged, seen_urls, seen_titles, domains = [], set(), set(), {}
    candidates = []
    for backend, items in batches:
        for rank, raw in enumerate(items, 1):
            item = dict(raw)
            item["backend"] = backend
            item["backend_rank"] = rank
            item["query_echo"] = _query_echo(item, query)
            candidates.append(item)
    for item in sorted(candidates, key=lambda value: (value["query_echo"], value["backend_rank"])):
        url = _canonical_url(item.get("url", ""))
        title = re.sub(r"\W+", " ", item.get("title", "").lower()).strip()
        domain = urlparse(url).netloc
        if not url or url in seen_urls or (title and title in seen_titles) or domains.get(domain, 0) >= 3:
            continue
        seen_urls.add(url)
        if title: seen_titles.add(title)
        domains[domain] = domains.get(domain, 0) + 1
        merged.append(item)
        if len(merged) >= limit: break
    return merged


def _web_headers(url):
    agent = (env("SEC_USER_AGENT", "BlockResearch academic research blockresearch@example.com")
             if "sec.gov" in urlparse(url).netloc else "Mozilla/5.0 BlockResearch/3.0")
    return {"User-Agent": agent, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8"}


async def _download(url, timeout):
    """Fetch directly, then use a read-only text mirror for blocked public pages."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=_web_headers(url))
            response.raise_for_status()
            return response, False
    except Exception as direct_error:
        mirror = f"https://r.jina.ai/{url}"
        try:
            async with httpx.AsyncClient(timeout=max(timeout, 40), follow_redirects=True) as client:
                response = await client.get(mirror, headers={"User-Agent": "BlockResearch/3.0"})
                response.raise_for_status()
                text = response.text
                if len(text) < 200:
                    raise ValueError("mirror returned near-empty page")
                lower = text[:1200].lower()
                error_signals = ["the page could not be found", "404 not found", "access denied",
                                 "please enable javascript", "just a moment", "checking your browser"]
                if any(signal in lower for signal in error_signals):
                    raise ValueError("mirror returned error/captcha page")
                return response, True
        except Exception:
            raise direct_error


def _docx_text(content):
    if not content.startswith(b"PK"): return ""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "ignore")
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)).strip()
    except Exception:
        return ""


def _xlsx_text(content):
    """Extract worksheet cells as TSV without adding a heavyweight dependency."""
    if not content.startswith(b"PK"):
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            shared = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root]
            sheets = sorted(name for name in names
                            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))[:4]
            lines = []
            for sheet in sheets:
                root = ET.fromstring(archive.read(sheet))
                for row in root.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    values = []
                    for cell in row:
                        if not cell.tag.endswith("}c"):
                            continue
                        kind = cell.attrib.get("t")
                        raw = next((node.text or "" for node in cell.iter()
                                    if node.tag.endswith("}v") or node.tag.endswith("}t")), "")
                        if kind == "s" and raw.isdigit() and int(raw) < len(shared):
                            raw = shared[int(raw)]
                        values.append(_clean_cell(raw))
                    if any(values):
                        lines.append("\t".join(values))
                    if sum(len(line) for line in lines) > 60000:
                        break
            return "\n".join(lines)
    except Exception:
        return ""


def _clean_cell(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _passage(text, search, window=3000):
    """Return the window with the densest query-term coverage, centered on evidence."""
    terms = list(dict.fromkeys(term.lower() for term in re.findall(r"[\w-]+", search) if len(term) > 3))
    if not terms or not text:
        return "", 0
    lower = text.lower()
    positions = []
    for term in terms:
        positions.extend(match.start() for match in list(re.finditer(re.escape(term), lower))[:30])
    best, best_score = "", 0
    for position in positions:
        start = max(0, position - window // 3)
        chunk = text[start:start + window]
        chunk_lower = chunk.lower()
        coverage = sum(term in chunk_lower for term in terms)
        frequency = sum(min(chunk_lower.count(term), 3) for term in terms)
        score = coverage * 10 + frequency
        if score > best_score:
            best, best_score = chunk, score
    return best, best_score


def _expand_queries(queries):
    """Normalize and deduplicate only; never rewrite retrieval semantics."""
    return list(dict.fromkeys(" ".join(str(raw).split()) for raw in queries
                              if " ".join(str(raw).split())))[:16]


async def search(query: str, n: int = 5) -> dict:
    if not query.strip():
        return {"error": "empty query", "results": []}
    if not env("BRAVE_API_KEY"):
        return {"error": "BRAVE_API_KEY is not configured", "results": []}
    try:
        count = min(max(int(n), 1), 20)
        async def request(value):
            response = await _brave_get(value, count)
            rows = []
            for item in (response.json().get("web") or {}).get("results", []):
                excerpts = [item.get("description", ""), *(item.get("extra_snippets") or [])]
                rows.append({"title": item.get("title", ""), "url": item.get("url", ""),
                             "snippet": " ".join(x for x in excerpts if x)[:1000]})
            return rows
        exact = await request(query)
        relaxed_query = re.sub(r'"([^"]+)"', r"\1", query)
        relaxed = await request(relaxed_query) if relaxed_query != query and len(exact) < max(3, count // 2) else []
        batches = [("brave", exact)] + ([("brave_relaxed", relaxed)] if relaxed else [])
        return {"results": _merge_results(batches, query, count),
                "backend": "brave"}
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return {"error": f"brave {type(exc).__name__}: {detail}", "results": []}


async def browse(queries, n=5, fetch_per_query=1, search_terms="") -> dict:
    """Run diverse searches and fetch their leading unique pages in parallel."""
    if not isinstance(queries, list):
        queries = [queries]
    queries = _expand_queries(queries)
    if not queries:
        return {"error": "empty queries", "results": [], "pages": []}
    gate = asyncio.Semaphore(3)

    async def limited_search(query):
        async with gate:
            return await search(query, n)

    searches = await asyncio.gather(*(limited_search(query) for query in queries))
    results, urls, seen_urls = [], [], set()
    per_query = min(max(int(fetch_per_query), 0), 2)
    for rank in range(5):
        for query, result in zip(queries, searches):
            items = result.get("results", [])
            if rank >= len(items):
                continue
            item = items[rank]
            url = item.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append({**item, "query": query})
                if rank < per_query:
                    urls.append(url)
    urls = list(dict.fromkeys(urls))[:10]
    pages = await asyncio.gather(*(fetch_page(url, search_terms) for url in urls))
    errors = [result.get("error") for result in searches if result.get("error")]
    value = {"queries": queries, "results": results[:30], "pages": pages}
    if errors:
        value["search_errors"] = errors[:5]
        if not results:
            value["error"] = "; ".join(dict.fromkeys(errors))
    return value


async def search_many(queries, n=5) -> dict:
    """Search without guessing which result should be fetched."""
    if not isinstance(queries, list):
        queries = [queries]
    queries = _expand_queries(queries)
    if not queries:
        return {"error": "empty queries", "queries": [], "results": []}
    gate = asyncio.Semaphore(3)
    async def limited(query):
        async with gate:
            return await search(query, n)
    batches = await asyncio.gather(*(limited(query) for query in queries))
    results, seen_urls, seen_titles, domains = [], set(), set(), {}
    max_rank = min(max(int(n), 1), 20)
    for echo in (False, True):
        for rank in range(max_rank):
            for query, batch in zip(queries, batches):
                items = batch.get("results", [])
                if rank >= len(items) or bool(items[rank].get("query_echo")) != echo: continue
                item = items[rank]
                url = _canonical_url(item.get("url", ""))
                title = re.sub(r"\W+", " ", item.get("title", "").lower()).strip()
                domain = urlparse(url).netloc
                if not url or url in seen_urls or (title and title in seen_titles) or domains.get(domain, 0) >= 4:
                    continue
                seen_urls.add(url)
                if title: seen_titles.add(title)
                domains[domain] = domains.get(domain, 0) + 1
                results.append({**item, "query": query, "rank": rank + 1})
    errors = [batch.get("error") for batch in batches if batch.get("error")]
    value = {"queries": queries, "results": results[:min(100, len(queries) * max_rank)]}
    if errors:
        value["search_errors"] = errors[:5]
        if not results:
            value["error"] = "; ".join(dict.fromkeys(errors))
    return value


def _valid_url(url: str) -> bool:
    return urlparse(str(url)).scheme in {"http", "https"}


async def fetch_page(url: str, search: str = "") -> dict:
    if not _valid_url(url):
        return {"error": "invalid URL", "url": str(url), "text": "", "links": []}
    try:
        from bs4 import BeautifulSoup

        response, mirrored = await _download(url, 25)
        if response.content.startswith(b"%PDF") or "pdf" in response.headers.get("content-type", ""):
            return await read_pdf(str(response.url), search)
        docx = _docx_text(response.content)
        if docx:
            return {"url": str(response.url), "text": docx[:36000], "links": []}
        content_type = response.headers.get("content-type", "").lower()
        if urlparse(str(response.url)).path.lower().endswith(".xlsx") or "spreadsheet" in content_type:
            xlsx = _xlsx_text(response.content)
            if xlsx:
                result = {"url": str(response.url), "text": xlsx[:60000], "format": "xlsx-tsv", "links": []}
                passage, score = _passage(xlsx, search)
                if score:
                    result["search_hits"] = [{"term": "dense match", "text": passage}]
                return result
            return {"error": "unable to parse XLSX", "url": str(response.url), "text": "", "links": []}
        if "json" in response.headers.get("content-type", ""):
            return {"url": str(response.url), "data": response.json()}
        if "csv" in content_type or urlparse(str(response.url)).path.lower().endswith((".csv", ".tsv")):
            return {"url": str(response.url), "text": response.text[:60000], "format": "delimited-text", "links": []}

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            tag.decompose()
        links = [
            {"url": urljoin(str(response.url), a["href"]), "text": a.get_text(" ", strip=True)[:120]}
            for a in soup.find_all("a", href=True)
            if _valid_url(urljoin(str(response.url), a["href"]))
        ][:60]
        text = soup.get_text("\n", strip=True)
        result = {"url": url, "text": text[:20000], "links": links}
        if mirrored: result["fetched_via"] = "text_mirror"
        # If direct fetch returned thin/no text, try the mirror as a second chance
        if not mirrored and len(text) < 200:
            try:
                async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
                    mirror_resp = await client.get(f"https://r.jina.ai/{url}",
                                                   headers={"User-Agent": "BlockResearch/3.0"})
                    mirror_resp.raise_for_status()
                    mirror_text = mirror_resp.text
                    if len(mirror_text) >= 200:
                        text = mirror_text
                        result = {"url": url, "text": text[:20000], "links": links,
                                  "fetched_via": "text_mirror_thin_fallback"}
            except Exception:
                pass
        terms = sorted(set(term.lower() for term in re.findall(r"[\w-]+", search) if len(term) > 2),
                       key=len, reverse=True)
        hits, centers = [], []
        passage, score = _passage(text, search)
        if score:
            hits.append({"term": "dense match", "text": passage})
        for term in terms:
            for match in list(re.finditer(re.escape(term), text, re.I))[:4]:
                start = max(0, match.start() - 500)
                if any(abs(start - old) < 800 for old in centers): continue
                centers.append(start)
                hits.append({"term": term, "text": text[start:match.end() + 2500]})
                if len(hits) >= 16: break
            if len(hits) >= 16: break
        if hits:
            result["search_hits"] = hits[:16]
        return result
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "url": url, "text": "", "links": []}


async def read_pdf(url: str, search: str = "") -> dict:
    if not _valid_url(url):
        return {"error": "invalid URL", "url": str(url)}
    try:
        import fitz

        response, mirrored = await _download(url, 40)
        if mirrored:
            return {"url": url, "text": response.text[:36000], "fetched_via": "text_mirror"}
        if not (response.content.startswith(b"%PDF") or "pdf" in response.headers.get("content-type", "")):
            return {"error": "not a PDF", "url": str(response.url), "text": response.text[:3000]}

        doc = fitz.open(stream=response.content, filetype="pdf")
        chunks, ranked, size = [], [], 0
        for page_no, page in enumerate(doc, 1):
            text = page.get_text()
            if size < 36000:
                chunks.append(text)
                size += len(text)
            passage, score = _passage(text, search)
            if score:
                ranked.append((score, page_no, passage))
        result = {
            "url": str(response.url), "pages": len(doc), "text": "\n".join(chunks)[:36000],
            "first_page": doc[0].get_text()[:3000] if doc else "",
            "last_page": doc[-1].get_text()[:3000] if doc else "",
        }
        doc.close()
        if ranked:
            result["search_hits"] = [
                {"page": page, "text": text} for _, page, text in sorted(ranked, reverse=True)[:16]
            ]
        return result
    except Exception as exc:
        return {"error": f"PDF error: {exc}", "url": url}


def calculate(expression: str) -> dict:
    allowed = {"ceil": math.ceil, "floor": math.floor, "round": round, "sqrt": math.sqrt}
    pattern = r"[\d\s.eE+*/%(),_-]+|[\d\s.eE+*/%(),_-]*(?:ceil|floor|round|sqrt)[\d\s.eE+*/%(),_-]*"
    if not re.fullmatch(pattern, expression):
        return {"error": "unsafe expression"}
    try:
        return {"expression": expression, "value": eval(expression, {"__builtins__": {}}, allowed)}
    except Exception as exc:
        return {"error": str(exc)}


async def run_python(code: str, data=None) -> dict:
    safe_modules = {"math", "statistics", "fractions", "decimal", "itertools", "collections", "functools"}
    banned = {"open", "eval", "exec", "compile", "input", "breakpoint", "globals", "locals", "vars", "__import__"}
    try:
        if len(code) > 8000:
            raise ValueError("code too long")
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(item.name.split(".")[0] not in safe_modules for item in node.names):
                raise ValueError("unsafe import")
            if isinstance(node, ast.ImportFrom) and (not node.module or node.module.split(".")[0] not in safe_modules):
                raise ValueError("unsafe import")
            if isinstance(node, ast.Name) and node.id in banned:
                raise ValueError("unsafe name")
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise ValueError("unsafe attribute")
        with tempfile.TemporaryDirectory() as workdir:
            wrapper = "import json,sys\nDATA=json.load(sys.stdin)\n" + code
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", wrapper, cwd=workdir, env={},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            payload = json.dumps(data or {}, ensure_ascii=False, default=str).encode()
            stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=15)
        return {"code": code, "stdout": stdout.decode()[:12000], "stderr": stderr.decode()[:3000], "exit_code": process.returncode}
    except Exception as exc:
        return {"code": code, "error": str(exc)}
