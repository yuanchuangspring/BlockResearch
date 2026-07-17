"""Deterministic research tools."""

import asyncio
import ast
import io
import math
import os
import re
import sys
import tempfile
import zipfile
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx


_serper_available = True


def _web_headers(url):
    agent = (os.environ.get("SEC_USER_AGENT", "BlockResearch academic research blockresearch@example.com")
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


def _expand_queries(queries):
    original = [str(query).strip() for query in queries if str(query).strip()][:16]
    expansions = []
    ontology = r"\b(?:alias|nickname|sobriquet|epithet|by-?name|appellation)\b"
    for query in original:
        quoted = re.findall(r'"([^"]+)"', query)
        if quoted:
            relaxed = re.sub(r'"([^"]+)"', r"\1", query)
            if relaxed != query:
                expansions.append(relaxed)
            mixed = re.sub(r'"([^"]+)"', lambda match: match.group(1) if len(match.group(1).split()) == 1 else match.group(0), query)
            expansions.extend([mixed, re.sub(ontology, "", mixed, flags=re.I)])
        anchors = [item for item in quoted if len(item.split()) == 1]
        phrases = [item for item in quoted if 2 <= len(item.split()) <= 5]
        prefix = query.split('"', 1)[0].strip()
        if not anchors and prefix and 1 <= len(prefix.split()) <= 3 and "site:" not in prefix:
            anchors = [prefix]
        if anchors and phrases:
            expansions.extend(
                f'"{anchors[0]}" "{token}"'
                for token in phrases[0].split() if len(token) > 3
            )
    return list(dict.fromkeys(" ".join(item.split()) for item in original + expansions if item.strip()))[:20]


async def _fallback_search(query: str, n: int) -> dict:
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        try:
            response = await client.get("https://search.yahoo.com/search", params={"p": query})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for row in soup.select("div.algo"):
                link = row.select_one("h3 a") or row.select_one(".compTitle a")
                if not link:
                    continue
                url = link.get("href", "")
                match = re.search(r"/RU=([^/]+)/RK=", url)
                if match:
                    url = unquote(match.group(1))
                snippet = row.select_one(".compText") or row.select_one("p")
                results.append({
                    "title": link.get_text(" ", strip=True), "url": url,
                    "snippet": snippet.get_text(" ", strip=True) if snippet else "",
                })
                if len(results) >= n:
                    break
            if results:
                return {"results": results, "backend": "yahoo"}
        except Exception:
            pass

        response = await client.get("https://lite.duckduckgo.com/lite/", params={"q": query})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for link in soup.select("a.result-link"):
            url = link.get("href", "")
            if "uddg=" in url:
                url = unquote(parse_qs(urlparse(url).query).get("uddg", [url])[0])
            row = link.find_parent("tr")
            snippet = row.find_next_sibling("tr") if row else None
            results.append({
                "title": link.get_text(" ", strip=True), "url": url,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            })
            if len(results) >= n:
                break
        if not results:
            raise RuntimeError("search backends returned no results")
        return {"results": results, "backend": "duckduckgo"}


async def search(query: str, n: int = 5) -> dict:
    global _serper_available
    if not query.strip():
        return {"error": "empty query", "results": []}
    try:
        if _serper_available and os.environ.get("SERPER_API_KEY"):
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    json={"q": query, "num": min(max(int(n), 1), 10)},
                    headers={"X-API-KEY": os.environ.get("SERPER_API_KEY", "")},
                )
                if response.status_code == 400:
                    _serper_available = False
                response.raise_for_status()
            return {"results": [
                {"title": item.get("title", ""), "url": item.get("link", ""), "snippet": item.get("snippet", "")}
                for item in response.json().get("organic", [])
            ], "backend": "serper"}
        return await _fallback_search(query, min(max(int(n), 1), 10))
    except Exception as primary:
        try:
            return await _fallback_search(query, min(max(int(n), 1), 10))
        except Exception as fallback:
            return {"error": f"primary: {primary}; fallback: {fallback}", "results": []}


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
        if "json" in response.headers.get("content-type", ""):
            return {"url": str(response.url), "data": response.json()}

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
        terms = [term.lower() for term in re.findall(r"[\w-]+", search) if len(term) > 2]
        hits = []
        for term in terms:
            for match in list(re.finditer(re.escape(term), text, re.I))[:4]:
                start = max(0, match.start() - 500)
                hits.append({"term": term, "text": text[start:match.end() + 2500]})
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
        terms = list(dict.fromkeys(term.lower() for term in re.findall(r"[\w-]+", search) if len(term) > 3))
        chunks, ranked, size = [], [], 0
        for page_no, page in enumerate(doc, 1):
            text = page.get_text()
            if size < 36000:
                chunks.append(text)
                size += len(text)
            score = sum(min(text.lower().count(term), 5) for term in terms)
            if score:
                ranked.append((score, page_no, text))
        result = {
            "url": str(response.url), "pages": len(doc), "text": "\n".join(chunks)[:36000],
            "first_page": doc[0].get_text()[:3000] if doc else "",
            "last_page": doc[-1].get_text()[:3000] if doc else "",
        }
        doc.close()
        if ranked:
            result["search_hits"] = [
                {"page": page, "text": text[:2200]} for _, page, text in sorted(ranked, reverse=True)[:16]
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


async def run_python(code: str) -> dict:
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
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", code, cwd=workdir, env={},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        return {"code": code, "stdout": stdout.decode()[:12000], "stderr": stderr.decode()[:3000], "exit_code": process.returncode}
    except Exception as exc:
        return {"code": code, "error": str(exc)}
