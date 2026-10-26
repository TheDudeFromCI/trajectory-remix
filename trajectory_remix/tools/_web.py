"""Shared document, budgeting, and tick semantics for synthetic web tools."""

import copy
import hashlib
import json
import math
import re
from collections import Counter
from urllib.parse import urldefrag, urljoin, urlsplit

from ._access import io_failure, permission
from ._cancellation import remove_pending
from ._clock import elapsed_seconds, now
from ._protocol import ToolError
from ._simulation import failure
from .runtime import execution_context

COMMON = {
    "max_output_bytes": {"type": "integer", "minimum": 1024, "maximum": 16000},
    "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 86400},
}
SOURCE = {
    "url": {"type": "string", "minLength": 1, "maxLength": 2048},
    "ref": {"type": "string", "minLength": 1, "maxLength": 68},
}
SOURCE_CHOICE = [{"required": ["url"]}, {"required": ["ref"]}]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def size(value):
    return len(encoded(value).encode("utf-8"))


def envelope(value):
    return {"ok": True, "result": value}


def fits(value, budget):
    return size(envelope(value)) <= budget


def check_quota(web, pending):
    if size([web, pending]) > execution_context.get().max_bytes:
        raise ToolError("Web fixture/pending-request quota exceeded")


def url_key(value, base=None):
    value = urljoin(base, value) if base else value
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(char.isspace() or ord(char) < 32 for char in value)):
            raise ValueError()
        parts.port
    except ValueError:
        raise ToolError("URL must use http/https, with a host, valid port and no credentials or whitespace") from None
    value = urldefrag(value)[0]
    if len(value) > 2048:
        raise ToolError("Resolved web URL exceeds 2048 characters")
    return value


def query_key(query):
    return " ".join(query.casefold().split())


def terms(text):
    return re.findall(r"\w+", text.casefold())


def source(state, args):
    web = state.get("web", {})
    ref = args.get("ref")
    if ref is None:
        ref = web.get("pages", {}).get(url_key(args["url"]))
        if ref is None:
            raise ToolError("No simulated web page for this URL")
    page = web.get("snapshots", {}).get(ref)
    if page is None:
        raise ToolError("Unknown web source reference")
    return ref, page


def metadata(ref, page):
    value = {"ref": ref, "url": page["url"], "title": page["title"]}
    if "published_at" in page:
        value["published_at"] = page["published_at"]
    return value


def seed(args, state):
    previous = state.get("web", {})
    web = {"pages": {}, "snapshots": dict(previous.get("snapshots", {})),
           "searches": {}, "search_latency_seconds": args.get("search_latency_seconds", 0)}
    for fixture in args["pages"]:
        page = copy.deepcopy(fixture)
        page["url"] = url_key(page["url"])
        page["text"] = page["text"].replace("\r\n", "\n").replace("\r", "\n")
        page.setdefault("status", 200)
        page.setdefault("latency_seconds", 0)
        page.setdefault("links", [])
        ids = set()
        for link in page["links"]:
            if link["id"] in ids:
                raise ToolError("Duplicate web link id")
            ids.add(link["id"])
            link["url"] = url_key(link["url"], page["url"])
        if page["url"] in web["pages"]:
            raise ToolError("Duplicate web page URL")
        canonical = json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ref = "web-" + hashlib.sha256(canonical.encode()).hexdigest()
        web["snapshots"][ref] = page
        web["pages"][page["url"]] = ref
    for fixture in args.get("searches", []):
        query = query_key(fixture["query"])
        if not query or query in web["searches"]:
            raise ToolError("Empty or duplicate web search fixture query")
        urls = [url_key(url) for url in fixture["urls"]]
        if len(set(urls)) != len(urls) or any(url not in web["pages"] for url in urls):
            raise ToolError("Search fixtures require unique URLs from the active corpus")
        web["searches"][query] = urls
    check_quota(web, state.get("web_pending", {}))
    state["web"] = web
    return {"pages": len(web["pages"]), "searches": len(web["searches"])}


def ranked_pages(web, query):
    """BM25 over body tokens with a repeated-title boost; URL breaks ties."""
    words = set(terms(query))
    if not words:
        raise ToolError("Search query must contain a word or number")
    documents = []
    for url, ref in sorted(web.get("pages", {}).items()):
        page = web["snapshots"][ref]
        counts = Counter(terms(page["title"]) * 3 + terms(page["text"]))
        documents.append((url, ref, counts, sum(counts.values())))
    average = sum(row[3] for row in documents) / max(1, len(documents)) or 1
    frequencies = {word: sum(word in row[2] for row in documents) for word in words}
    scored = []
    for url, ref, counts, length in documents:
        score = 0
        for word in sorted(words):
            frequency = counts[word]
            if frequency:
                inverse = math.log(1 + (len(documents) - frequencies[word] + 0.5)
                                   / (frequencies[word] + 0.5))
                score += inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average))
        if score:
            scored.append((-score, url, ref))
    return [row[2] for row in sorted(scored)]


def snippet(text, query):
    words = set(terms(query))
    match = next((m for m in re.finditer(r"\w+", text) if m.group().casefold() in words), None)
    start = max(0, match.start() - 80) if match else 0
    return text[start:start + 240]


def search(args, state):
    web = state.get("web", {})
    query = query_key(args["query"])
    if not query:
        raise ToolError("Search query cannot be blank")
    override = web.get("searches", {}).get(query)
    refs = ([web["pages"][url] for url in override] if override is not None
            else ranked_pages(web, query))
    if not execution_context.get().invisible:
        refs = [ref for ref in refs if permission(state, "web_search",
                {"url": web["snapshots"][ref]["url"]}, consume_rule=False)[0] is None]
    offset, limit = args.get("offset", 0), args.get("max_results", 5)
    value = {"results": [], "total_results": len(refs), "truncated": offset < len(refs),
             "next_offset": offset if offset < len(refs) else None}
    for index in range(offset, min(len(refs), offset + limit)):
        ref = refs[index]
        page = web["snapshots"][ref]
        item = {**metadata(ref, page), "snippet": snippet(page["text"], query)}
        candidate = {**value, "results": value["results"] + [item],
                     "truncated": index + 1 < len(refs),
                     "next_offset": index + 1 if index + 1 < len(refs) else None}
        if not fits(candidate, args.get("max_output_bytes", 6000)):
            if not value["results"]:
                excerpt = item["snippet"]
                candidate = longest_prefix(excerpt, lambda count: {
                    **candidate, "results": [{**item, "snippet": excerpt[:count]}]
                }, args.get("max_output_bytes", 6000))
            else:
                break
        value = candidate
    return value, {"latency_seconds": web.get("search_latency_seconds", 0)}


def longest_prefix(text, build, budget):
    """Fit the entire JSON envelope, including escaped text and continuation metadata."""
    if not fits(build(0), budget):
        raise ToolError("Web result metadata exceeds output budget; increase max_output_bytes")
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(build(middle), budget):
            low = middle
        else:
            high = middle - 1
    # Position metadata can shrink at EOF; test that boundary independently.
    if fits(build(len(text)), budget):
        low = len(text)
    if text and not low:
        raise ToolError("Web result metadata leaves no room for text; increase max_output_bytes")
    return build(low)


def open_page(args, state):
    ref, page = source(state, args)
    lines = page["text"].splitlines(keepends=True)
    start, column = args.get("start_line", 1), args.get("start_column", 0)
    if column and (start > len(lines) or column >= len(lines[start - 1])):
        raise ToolError("start_column must point within start_line")
    selected = lines[start - 1:start - 1 + args.get("max_lines", 40)]
    text = "".join(selected)[column:]
    link_offset = args.get("link_offset", 0)
    links = page["links"][link_offset:link_offset + args.get("max_links", 5)]
    base = {**metadata(ref, page), "status": page["status"], "total_lines": len(lines),
            "start_line": start, "start_column": column, "links": links,
            "next_link_offset": link_offset + len(links)
            if link_offset + len(links) < len(page["links"]) else None}

    def build(count):
        remaining, index, next_column = count, start - 1, column
        while index < len(lines) and remaining >= len(lines[index]) - next_column:
            remaining -= len(lines[index]) - next_column
            index, next_column = index + 1, 0
        next_column += remaining
        more = index < len(lines)
        return {**base, "content": text[:count],
                "end_line": (index + 1 if next_column else index) if count else None,
                "truncated": more, "next_line": index + 1 if more else None,
                "next_column": next_column if more else None}

    budget = args.get("max_output_bytes", 6000)
    # Leave space for an excerpt even when the page has many long link labels.
    reserve = min(512, budget // 4) if text else 0
    while base["links"] and size(envelope(build(0))) > budget - reserve:
        base["links"] = base["links"][:-1]
        base["next_link_offset"] = link_offset + len(base["links"])
    value = longest_prefix(text, build, budget)
    return value, page


def find(args, state):
    ref, page = source(state, args)
    if not args["text"].strip():
        raise ToolError("Find text cannot be blank")
    pattern = re.compile(re.escape(args["text"]), 0 if args.get("case_sensitive", False) else re.IGNORECASE)
    lines = page["text"].splitlines()
    start, limit = args.get("start_line", 1), args.get("max_results", 10)
    context = args.get("context_lines", 1)
    matching = [index for index in range(start - 1, len(lines)) if pattern.search(lines[index])]
    value = {**metadata(ref, page), "status": page["status"], "matches": [],
             "truncated": bool(matching), "next_line": matching[0] + 1 if matching else None}
    budget = args.get("max_output_bytes", 6000)
    for position, index in enumerate(matching[:limit]):
        match = pattern.search(lines[index])
        snippet_start = max(0, match.start() - 120)
        # Bound every line as well as the aggregate, including single-line pages.
        item = {"line": index + 1, "column": match.start(),
                "text": lines[index][snippet_start:snippet_start + 360],
                "text_start_column": snippet_start,
                "before": [line[:240] for line in lines[max(0, index - context):index]],
                "after": [line[:240] for line in lines[index + 1:index + 1 + context]]}
        candidate = {**value, "matches": value["matches"] + [item],
                     "truncated": position + 1 < len(matching),
                     "next_line": matching[position + 1] + 1 if position + 1 < len(matching) else None}
        if not fits(candidate, budget):
            if not value["matches"]:
                # Context is optional; keep a useful hit when it fills the budget.
                item["before"], item["after"] = [], []
                if not fits(candidate, budget):
                    excerpt = item["text"]
                    candidate = longest_prefix(excerpt, lambda count: {
                        **candidate, "matches": [{**item, "text": excerpt[:count]}]
                    }, budget)
            else:
                break
        value = candidate
    return value, page


def run_web(name, args, state, build):
    context = execution_context.get()
    if context.cancelled:
        return remove_pending(state, "web_pending", context.call_id)
    if context.elapsed == 0:
        failed = io_failure(state, name, args)
        if failed is not None:
            return failed
    if context.elapsed < context.duration:
        return "", False, False
    pending = state.get("web_pending", {})
    entry = pending.get(context.call_id)
    modified = False
    if entry is None:
        try:
            value, timing = build(args, state)
            if not fits(value, args.get("max_output_bytes", 6000)):
                raise ToolError("Web result metadata exceeds output budget; increase max_output_bytes")
            entry = {"output": encoded(envelope(value)), "started_iso": now(state, context).isoformat(),
                     "latency_seconds": timing.get("latency_seconds", 0), "error": timing.get("error")}
            check_quota(state.get("web", {}), {**pending, context.call_id: entry})
        except ToolError as error:
            return failure(str(error))
        state.setdefault("web_pending", {})[context.call_id] = entry
        modified = True
    elapsed = max(0, elapsed_seconds(entry["started_iso"], state, context))
    latency, timeout = entry["latency_seconds"], args.get("timeout_seconds", 30)
    timed_out = (latency > timeout or entry["error"] == "timeout") and elapsed + 1e-6 >= timeout
    ready = elapsed + 1e-6 >= latency and entry["error"] != "timeout"
    if not ready and not timed_out:
        return "", False, modified
    remove_pending(state, "web_pending", context.call_id)
    if timed_out:
        return failure("ETIMEDOUT: Simulated web request timed out", modified=True)
    if entry["error"]:
        return failure("Web transport error: " + entry["error"], modified=True)
    return entry["output"], True, True
