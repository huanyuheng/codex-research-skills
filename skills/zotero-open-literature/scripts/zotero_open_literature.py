#!/usr/bin/env python3
"""Build Zotero collections from formal open-access literature.

This script intentionally avoids shadow-library sources. It imports only records
whose PDF bytes are actually downloadable from public OA locations.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


CONNECTOR = "http://127.0.0.1:23119/connector"
OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"

DEFAULT_QUERIES = [
    "femtosecond laser fused silica selective etching",
    "ultrafast laser fused silica nanogratings",
    "femtosecond laser glass microfluidics",
    "Bessel beam glass cutting femtosecond laser",
    "femtosecond laser fused silica waveguide",
]

BAD_PDF_HOSTS = {
    "arxiv.org",
    "content.openalex.org",
    "doi.org",
    "www.sciencedirect.com",
    "ieeexplore.ieee.org",
}

OFF_TOPIC = [
    "graphene",
    "protein",
    "cell",
    "cancer",
    "blood",
    "bone",
    "skin",
    "retina",
    "ophthalm",
]

MATERIAL_TERMS = [
    "fused silica",
    "silica",
    "quartz",
    "glass",
    "transparent",
    "dielectric",
]

LASER_TERMS = [
    "femtosecond",
    "ultrafast",
    "picosecond",
    "laser",
    "bessel",
    "filament",
]

PROJECT_TITLE = "熔融石英齿形结构超快激光辅助化学刻蚀切割释放工艺研究"

REVIEW_THEME_RULES = [
    (
        "SLE/FLICE与化学刻蚀释放",
        ["selective", "etch", "chemical", "flice", "hf", "koh", "naoh", "microchannel", "microfluidic", "release"],
        "支撑“超快激光改性-辅助化学刻蚀-结构释放”的技术路线，可写入国外研究现状和技术路线依据。",
    ),
    (
        "切割、隐形切割与Bessel光束加工",
        ["cutting", "dicing", "stealth", "bessel", "filament", "drilling", "kerf", "microgroove", "micro-hole"],
        "提供透明硬脆材料切割释放、损伤控制和高长径比加工参考，可对照本项目齿形结构的崩边与断面质量。",
    ),
    (
        "熔融石英改性机理与选择性来源",
        ["nanograting", "densification", "stress", "thermal", "plasma", "damage", "birefringence", "relaxation", "micro-explosion"],
        "用于解释熔融石英内部改性、应力/热效应、纳米光栅和刻蚀选择性形成机理，可写入机理分析基础。",
    ),
    (
        "表面质量、粗糙度与缺陷修复",
        ["surface", "roughness", "polishing", "repair", "defect", "crack", "damage", "quality", "sidewall"],
        "可支撑本项目用崩边、断面粗糙度、侧壁垂直度等指标评价切割释放质量。",
    ),
    (
        "三维玻璃微结构与器件制造",
        ["3d", "three-dimensional", "microstructure", "microfabrication", "monolithic", "membrane", "actuator", "mems"],
        "说明超快激光结合后处理已用于玻璃三维微结构/器件制造，可作为项目应用背景。",
    ),
    (
        "光波导、光子结构与功能器件",
        ["waveguide", "photonic", "bragg", "microcavity", "optofluidic", "optical"],
        "主要作为超快激光玻璃加工能力和应用拓展背景，优先级低于切割释放和化学刻蚀文献。",
    ),
]

REVIEW_KEYWORDS = [
    "fused silica",
    "quartz",
    "silica glass",
    "glass",
    "femtosecond",
    "ultrafast",
    "picosecond",
    "selective laser etching",
    "chemical etching",
    "HF",
    "KOH",
    "NaOH",
    "Bessel",
    "cutting",
    "dicing",
    "release",
    "roughness",
    "sidewall",
    "nanograting",
    "stress",
    "thermal",
    "microfluidic",
    "microstructure",
]


def log(msg: object) -> None:
    print(str(msg), flush=True)


def curl_bin() -> str:
    return "curl.exe" if platform.system().lower().startswith("win") else "curl"


def http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "zotero-open-literature"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")), dict(resp.headers)


def connector_post_json(path: str, payload: dict, timeout: int = 60):
    req = urllib.request.Request(
        CONNECTOR + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def connector_get_text(path: str, timeout: int = 30):
    req = urllib.request.Request(CONNECTOR + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def connector_post_pdf(path: str, body: bytes, metadata: dict, timeout: int = 180):
    req = urllib.request.Request(
        CONNECTOR + path,
        data=body,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(body)),
            "X-Metadata": json.dumps(metadata, ensure_ascii=False),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def selected_collection() -> dict:
    status, text = connector_post_json("/getSelectedCollection", {}, timeout=20)
    if status != 200:
        raise RuntimeError(f"getSelectedCollection status {status}")
    return json.loads(text)


def zotero_api_base(library_id: str) -> str:
    return f"http://127.0.0.1:23119/api/users/{library_id}"


def zotero_get(library_id: str, path: str, params: dict | None = None):
    url = zotero_api_base(library_id) + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Zotero-API-Version": "3"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")), dict(resp.headers)


def normalize_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").strip()).lower()


def normalize_doi(doi: str | None) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    return doi.lower()


def clean_text(text: str | None) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u00ad", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def short_text(text: str | None, limit: int = 180) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def safe_slug(text: str, max_len: int = 96) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-").lower()
    return (slug or "paper")[:max_len]


def parse_year(date_text: str | None) -> int:
    match = re.search(r"(19|20)\d{2}", date_text or "")
    return int(match.group(0)) if match else 0


def creators_text(data: dict, limit: int = 3) -> str:
    names = []
    for creator in data.get("creators") or []:
        name = creator.get("name")
        if not name:
            first = creator.get("firstName") or ""
            last = creator.get("lastName") or ""
            name = (first + " " + last).strip()
        if name:
            names.append(name)
    if len(names) > limit:
        return ", ".join(names[:limit]) + " et al."
    return ", ".join(names)


def zotero_children(library_id: str, item_key: str) -> list[dict]:
    try:
        children, _ = zotero_get(library_id, f"/items/{item_key}/children", {"format": "json"})
        return children
    except Exception:
        return []


def zotero_fulltext(library_id: str, attachment_key: str) -> str:
    try:
        payload, _ = zotero_get(library_id, f"/items/{attachment_key}/fulltext")
    except Exception:
        return ""
    if isinstance(payload, dict):
        return clean_text(payload.get("content") or "")
    return ""


def item_fulltext(library_id: str, item: dict, max_chars: int = 12000) -> tuple[str, int, int]:
    data = item.get("data", {})
    children = zotero_children(library_id, data.get("key", ""))
    attachment_count = 0
    best = ""
    for child in children:
        cdata = child.get("data", {})
        if cdata.get("itemType") != "attachment":
            continue
        if cdata.get("contentType") and "pdf" not in cdata.get("contentType", "").lower():
            continue
        attachment_count += 1
        text = zotero_fulltext(library_id, cdata.get("key", ""))
        if len(text) > len(best):
            best = text
    return best[:max_chars], len(best), attachment_count


def first_section(text: str, names: list[str], stop_names: list[str], limit: int = 1200) -> str:
    if not text:
        return ""
    name_re = "|".join(re.escape(x) for x in names)
    stop_re = "|".join(re.escape(x) for x in stop_names)
    pattern = rf"(?is)\b(?:{name_re})\b\s*[:.\-]?\s*(.*?)(?=\b(?:{stop_re})\b|$)"
    match = re.search(pattern, text)
    if not match:
        return ""
    return short_text(match.group(1), limit)


def abstract_or_fulltext(data: dict, fulltext: str) -> str:
    abstract = clean_text(data.get("abstractNote"))
    if abstract:
        return abstract
    return first_section(
        fulltext,
        ["abstract"],
        ["keywords", "introduction", "1 introduction", "1. introduction"],
        limit=1000,
    )


def conclusion_from_fulltext(fulltext: str) -> str:
    return first_section(
        fulltext,
        ["conclusion", "conclusions", "summary"],
        ["acknowledg", "references", "bibliography"],
        limit=1000,
    )


def parameter_terms(text: str, limit: int = 8) -> str:
    patterns = [
        r"\b\d+(?:\.\d+)?\s?(?:fs|ps|ns|um|µm|μm|mm|nm|W|mW|kHz|MHz|GHz)\b",
        r"\b(?:HF|KOH|NaOH|HNO3|HCl)\b",
        r"\bRa\b",
    ]
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            value = match.group(0)
            if value not in found:
                found.append(value)
            if len(found) >= limit:
                return "; ".join(found)
    return "; ".join(found)


def review_theme(text: str) -> tuple[str, str]:
    low = text.lower()
    best = ("其他/背景文献", "可作为背景补充；若申请书篇幅有限，优先引用更贴近化学刻蚀释放和质量评价的论文。", 0)
    for theme, keys, note in REVIEW_THEME_RULES:
        hits = sum(1 for key in keys if key.lower() in low)
        if hits > best[2]:
            best = (theme, note, hits)
    return best[0], best[1]


def project_score(data: dict, fulltext: str) -> int:
    text = f"{data.get('title','')} {data.get('abstractNote','')} {fulltext[:4000]}".lower()
    score = 0
    weights = [
        ("fused silica", 14),
        ("quartz", 12),
        ("silica", 8),
        ("glass", 5),
        ("femtosecond", 10),
        ("ultrafast", 8),
        ("selective", 7),
        ("etch", 10),
        ("chemical", 6),
        ("cutting", 8),
        ("dicing", 8),
        ("release", 8),
        ("bessel", 7),
        ("roughness", 7),
        ("sidewall", 6),
        ("damage", 5),
        ("nanograting", 5),
        ("microstructure", 5),
    ]
    for token, weight in weights:
        if token in text:
            score += weight
    year = parse_year(data.get("date"))
    if year >= 2022:
        score += 6
    elif year >= 2018:
        score += 3
    return min(score, 100)


def found_keywords(text: str) -> str:
    low = text.lower()
    hits = []
    for key in REVIEW_KEYWORDS:
        if key.lower() in low:
            hits.append(key)
    return "; ".join(hits[:10])


def application_position(theme: str) -> str:
    if "化学刻蚀" in theme:
        return "国内外研究现状/技术路线"
    if "切割" in theme:
        return "国内外研究现状/研究内容"
    if "机理" in theme:
        return "理论依据/关键科学问题"
    if "表面质量" in theme:
        return "研究指标/预期成果"
    if "三维" in theme:
        return "应用背景/创新点"
    return "背景引用"


def markdown_table(headers: list[str], rows: list[dict], keys: list[str]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        values = []
        for key in keys:
            value = str(row.get(key, "")).replace("|", "/")
            values.append(value)
        out.append("| " + " | ".join(values) + " |")
    return "\n".join(out) + "\n"


def crossref_work(doi: str) -> dict | None:
    doi = normalize_doi(doi)
    if not doi:
        return None
    url = CROSSREF + "/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "zotero-open-literature/0.2 (https://github.com/huanyuheng/codex-research-skills)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return payload.get("message") if isinstance(payload, dict) else None


def issued_year(message: dict) -> int:
    for field in ("published-print", "published-online", "issued"):
        parts = (((message.get(field) or {}).get("date-parts") or [[]])[0])
        if parts:
            try:
                return int(parts[0])
            except Exception:
                continue
    return 0


def crossref_title(message: dict) -> str:
    title = message.get("title") or []
    return clean_text(title[0] if title else "")


def discover_collection_key(library_id: str, selected: dict) -> str | None:
    for field in ("collectionKey", "key"):
        if selected.get(field):
            return selected[field]
    selected_name = selected.get("name") or selected.get("collectionName")
    if not selected_name:
        return None
    matches = []
    start = 0
    while True:
        batch, headers = zotero_get(
            library_id,
            "/collections",
            {"limit": 100, "start": start, "format": "json"},
        )
        for collection in batch:
            data = collection.get("data", {})
            if data.get("name") == selected_name:
                matches.append(collection.get("key") or data.get("key"))
        total = int(headers.get("Total-Results", len(batch)))
        start += len(batch)
        if start >= total or not batch:
            break
    return matches[0] if len(matches) == 1 else None


def resolve_target(args) -> dict:
    selected = selected_collection()
    library_id = str(
        args.library_id
        or os.environ.get("ZOTERO_USER_ID")
        or selected.get("libraryID")
        or selected.get("libraryId")
        or ""
    )
    if not args.library_id and not os.environ.get("ZOTERO_USER_ID") and library_id == "1":
        # Zotero 9 may report the local internal library id here. The local API
        # accepts /users/0 for the logged-in user, avoiding a misleading 400.
        library_id = "0"
    if not library_id:
        raise RuntimeError("Could not determine Zotero library id; pass --library-id or set ZOTERO_USER_ID.")
    collection_key = args.collection_key or discover_collection_key(library_id, selected)
    if not collection_key:
        raise RuntimeError("Could not determine collection key; pass --collection-key.")
    target_tree_id = args.target_tree_id or selected.get("treeViewID") or selected.get("id")
    if target_tree_id is not None:
        target_tree_id = str(target_tree_id)
        if target_tree_id.isdigit():
            target_tree_id = "C" + target_tree_id
    return {
        "library_id": library_id,
        "collection_key": collection_key,
        "target_tree_id": target_tree_id,
        "selected": selected,
    }


def existing_items(library_id: str, collection_key: str):
    items = []
    start = 0
    while True:
        batch, headers = zotero_get(
            library_id,
            f"/collections/{collection_key}/items/top",
            {"limit": 100, "start": start, "format": "json"},
        )
        items.extend(batch)
        total = int(headers.get("Total-Results", len(items)))
        start += len(batch)
        if start >= total or not batch:
            break
    dois, titles = set(), set()
    for item in items:
        data = item.get("data", {})
        doi = normalize_doi(data.get("DOI"))
        title = normalize_title(data.get("title"))
        if doi:
            dois.add(doi)
        if title:
            titles.add(title)
    return items, dois, titles


def abstract_text(work: dict) -> str:
    inv = work.get("abstract_inverted_index")
    if not inv:
        return ""
    pairs = []
    for word, positions in inv.items():
        for pos in positions:
            pairs.append((pos, word))
    return " ".join(word for _, word in sorted(pairs))


def source_name(work: dict) -> str:
    for loc in [work.get("primary_location"), work.get("best_oa_location")]:
        src = (loc or {}).get("source") or {}
        if src.get("display_name"):
            return src["display_name"]
    return ""


def is_arxivish(data_or_work: dict) -> bool:
    pub = (data_or_work.get("publicationTitle") or source_name(data_or_work) or "").lower()
    url = (data_or_work.get("url") or "").lower()
    return "arxiv" in pub or "arxiv.org" in url


def formal_count(items: list[dict]) -> int:
    return sum(1 for item in items if normalize_doi(item.get("data", {}).get("DOI")) and not is_arxivish(item.get("data", {})))


def relevance_score(work: dict) -> int:
    text = f"{work.get('title') or ''} {abstract_text(work)} {source_name(work)}".lower()
    if any(term in text for term in OFF_TOPIC):
        return -100
    if not any(term in text for term in MATERIAL_TERMS):
        return -100
    if not any(term in text for term in LASER_TERMS):
        return -100
    score = 0
    weights = [
        ("femtosecond", 6),
        ("ultrafast", 5),
        ("fused silica", 8),
        ("silica", 5),
        ("quartz", 6),
        ("glass", 3),
        ("selective", 4),
        ("etch", 6),
        ("nanograting", 7),
        ("microchannel", 5),
        ("bessel", 4),
        ("filament", 3),
        ("waveguide", 3),
    ]
    for token, weight in weights:
        if token in text:
            score += weight
    score += min(int(work.get("cited_by_count") or 0) // 50, 8)
    year = work.get("publication_year") or 0
    if year >= 2022:
        score += 3
    elif year >= 2018:
        score += 1
    return score


def pdf_urls(work: dict, allow_arxiv: bool = False) -> list[str]:
    urls = []
    for loc in [work.get("primary_location"), work.get("best_oa_location")]:
        if loc and loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    for loc in work.get("locations") or []:
        if loc and loc.get("pdf_url"):
            urls.append(loc["pdf_url"])
    content = work.get("content_urls") or {}
    if content.get("pdf"):
        urls.append(content["pdf"])
    out, seen = [], set()
    for url in urls:
        host = urllib.parse.urlparse(url).netloc.lower()
        if not allow_arxiv and host in BAD_PDF_HOSTS:
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def curl_text(url: str, timeout: int = 60) -> str:
    cmd = [
        curl_bin(),
        "-L",
        "--retry",
        "2",
        "--retry-all-errors",
        "--connect-timeout",
        "12",
        "--max-time",
        str(timeout),
        "-A",
        "zotero-open-literature",
        "-s",
        url,
    ]
    if platform.system().lower().startswith("win"):
        cmd.insert(2, "--ssl-no-revoke")
        cmd.insert(3, "--http1.1")
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-500:])
    return result.stdout.decode("utf-8", errors="replace")


def curl_file(url: str, out: Path, timeout: int = 120) -> tuple[bool, str]:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        curl_bin(),
        "-L",
        "--location-trusted",
        "--retry",
        "1",
        "--retry-all-errors",
        "--retry-delay",
        "1",
        "--connect-timeout",
        "10",
        "--max-time",
        str(timeout),
        "-A",
        "Mozilla/5.0",
        "-H",
        "Accept: application/pdf,application/octet-stream,*/*",
        "-o",
        str(out),
        url,
    ]
    if platform.system().lower().startswith("win"):
        cmd.insert(2, "--ssl-no-revoke")
        cmd.insert(3, "--http1.1")
        cmd.insert(4, "--compressed")
    result = subprocess.run(cmd, capture_output=True)
    if not out.exists():
        if result.returncode != 0:
            return False, result.stderr.decode("utf-8", errors="replace")[-500:]
        return False, "no output"
    body = out.read_bytes()
    if not body.startswith(b"%PDF"):
        return False, f"not pdf: {body[:8]!r}, size={out.stat().st_size}"
    if out.stat().st_size < 20_000:
        return False, f"pdf too small: {out.stat().st_size}"
    if result.returncode != 0 and b"%%EOF" not in body[-8192:]:
        return False, result.stderr.decode("utf-8", errors="replace")[-500:]
    return True, ""


def fetch_candidates(queries: list[str], pages: int, per_page: int, allow_arxiv: bool, out: Path) -> list[dict]:
    by_key = {}
    if out.exists():
        try:
            for work in json.loads(out.read_text(encoding="utf-8")):
                key = normalize_doi(work.get("doi")) or normalize_title(work.get("title"))
                if key:
                    by_key[key] = work
        except Exception:
            pass
    for query in queries:
        for page in range(1, pages + 1):
            params = {
                "search": query,
                "filter": "is_oa:true,type:article",
                "per-page": per_page,
                "page": page,
                "sort": "cited_by_count:desc",
            }
            url = OPENALEX + "?" + urllib.parse.urlencode(params)
            log(f"OpenAlex: {query} page {page}")
            try:
                data = json.loads(curl_text(url, timeout=70))
            except Exception as exc:
                log(f"  OpenAlex failed: {exc}")
                continue
            for work in data.get("results", []):
                key = normalize_doi(work.get("doi")) or normalize_title(work.get("title"))
                if not key:
                    continue
                score = relevance_score(work)
                if score < 8:
                    continue
                urls = pdf_urls(work, allow_arxiv=allow_arxiv)
                if not urls:
                    continue
                work["_score"] = score
                work["_pdf_urls"] = urls
                if key not in by_key or score > by_key[key].get("_score", 0):
                    by_key[key] = work
            candidates = sorted(
                by_key.values(),
                key=lambda w: (w.get("_score") or 0, w.get("publication_year") or 0, w.get("cited_by_count") or 0),
                reverse=True,
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"  saved candidates: {len(candidates)}")
    return sorted(
        by_key.values(),
        key=lambda w: (w.get("_score") or 0, w.get("publication_year") or 0, w.get("cited_by_count") or 0),
        reverse=True,
    )


def make_item(work: dict, connector_item_id: str) -> dict:
    creators = []
    for authorship in (work.get("authorships") or [])[:12]:
        name = ((authorship.get("author") or {}).get("display_name") or "").strip()
        if not name:
            continue
        parts = name.split()
        if len(parts) == 1:
            creators.append({"creatorType": "author", "name": name})
        else:
            creators.append({"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]})
    biblio = work.get("biblio") or {}
    return {
        "id": connector_item_id,
        "itemType": "journalArticle",
        "title": work.get("title") or "",
        "creators": creators,
        "publicationTitle": source_name(work),
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": "-".join([p for p in [biblio.get("first_page") or "", biblio.get("last_page") or ""] if p]),
        "date": str(work.get("publication_year") or ""),
        "DOI": normalize_doi(work.get("doi")),
        "url": work.get("doi") or work.get("id") or "",
        "abstractNote": abstract_text(work),
        "tags": ["codex-added-downloadable", "open-literature"],
    }


def import_one(work: dict, pdf_path: Path, pdf_url: str, target_tree_id: str | None) -> dict:
    item_id = "codex-" + uuid.uuid4().hex[:20]
    session_id = "codex-" + uuid.uuid4().hex[:16]
    item = make_item(work, item_id)
    status, _ = connector_post_json(
        "/saveItems",
        {"sessionID": session_id, "uri": item.get("url") or work.get("id"), "items": [item]},
        timeout=90,
    )
    if status != 201:
        raise RuntimeError(f"saveItems status {status}")
    if target_tree_id:
        connector_post_json(
            "/updateSession",
            {"sessionID": session_id, "target": target_tree_id, "tags": ["codex-added-downloadable", "open-literature"]},
            timeout=60,
        )
    status, _ = connector_post_pdf(
        "/saveAttachment",
        pdf_path.read_bytes(),
        {"sessionID": session_id, "parentItemID": item_id, "title": "Full Text PDF", "url": pdf_url},
        timeout=240,
    )
    if status != 201:
        raise RuntimeError(f"saveAttachment status {status}")
    return item


def cmd_doctor(args) -> None:
    status, text = connector_get_text("/ping", timeout=10)
    log(f"connector ping: {status} {text[:80]}")
    target = resolve_target(args)
    log(json.dumps(target, ensure_ascii=False, indent=2))
    items, _, _ = existing_items(target["library_id"], target["collection_key"])
    log(f"top-level items: {len(items)}")
    log(f"formal non-arXiv items: {formal_count(items)}")


def cmd_import_openalex(args) -> None:
    target = resolve_target(args)
    items, existing_dois, existing_titles = existing_items(target["library_id"], target["collection_key"])
    formal_before = formal_count(items)
    quota = min(args.max_new, max(0, args.target_formal - formal_before))
    log(f"formal before: {formal_before}; quota: {quota}")
    if quota <= 0:
        return
    work_dir = Path(args.work_dir)
    candidate_file = work_dir / "candidates.json"
    queries = args.query or DEFAULT_QUERIES
    candidates = fetch_candidates(queries, args.pages, args.per_page, args.allow_arxiv, candidate_file)
    pdf_dir = work_dir / "pdfs"
    report = work_dir / "import_report.jsonl"
    imported = 0
    for work in candidates:
        if imported >= quota:
            break
        if not args.allow_arxiv and is_arxivish(work):
            continue
        doi = normalize_doi(work.get("doi"))
        if not doi and not args.allow_no_doi:
            continue
        title = work.get("title") or ""
        title_norm = normalize_title(title)
        if doi and doi in existing_dois:
            continue
        if title_norm and title_norm in existing_titles:
            continue
        year = work.get("publication_year") or "undated"
        pdf_path = pdf_dir / f"{year}_{safe_slug(title)}.pdf"
        pdf_url_used = None
        for url in work.get("_pdf_urls") or pdf_urls(work, allow_arxiv=args.allow_arxiv):
            log(f"PDF try: {title[:100]} -> {url[:140]}")
            ok, err = curl_file(url, pdf_path, timeout=args.download_timeout)
            if ok:
                pdf_url_used = url
                break
            log(f"  failed: {err[:220]}")
            try:
                pdf_path.unlink(missing_ok=True)
            except Exception:
                pass
        if not pdf_url_used:
            continue
        try:
            item = import_one(work, pdf_path, pdf_url_used, target["target_tree_id"])
        except Exception as exc:
            log(f"  Zotero import failed: {exc}")
            continue
        imported += 1
        if doi:
            existing_dois.add(doi)
        if title_norm:
            existing_titles.add(title_norm)
        rec = {
            "title": item["title"],
            "year": item["date"],
            "doi": item["DOI"],
            "journal": item["publicationTitle"],
            "pdf": str(pdf_path),
            "pdf_url": pdf_url_used,
            "score": work.get("_score"),
            "cited_by_count": work.get("cited_by_count"),
        }
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"imported": rec}, ensure_ascii=False) + "\n")
        log(f"IMPORTED {imported}/{quota}: {item['title']}")
        time.sleep(args.delay)
    final_items, _, _ = existing_items(target["library_id"], target["collection_key"])
    log(f"final top-level items: {len(final_items)}")
    log(f"final formal non-arXiv items: {formal_count(final_items)}")


def has_pdf_attachment(item: dict) -> bool:
    if (item.get("links") or {}).get("attachment"):
        return True
    return ((item.get("meta") or {}).get("numChildren") or 0) > 0


def theme_for(title: str) -> str:
    t = title.lower()
    if any(x in t for x in ["etch", "chemical", "selective", "microchannel", "via hole", "tgv"]):
        return "selective-etching-microchannels"
    if any(x in t for x in ["bessel", "filament", "cutting", "dicing", "drilling", "micro-hole", "microgroove"]):
        return "bessel-cutting-drilling"
    if any(x in t for x in ["nanograting", "birefringence", "waveplate", "polarization"]):
        return "nanogratings-birefringence"
    if any(x in t for x in ["stress", "densification", "damage", "threshold", "plasma", "thermal"]):
        return "damage-thermal-stress"
    if any(x in t for x in ["waveguide", "photonic", "bragg", "microcav", "optofluidic"]):
        return "waveguides-photonics"
    if any(x in t for x in ["printing", "microfluidic", "3d", "monolithic", "flexure", "micro-actuator"]):
        return "3d-microstructures"
    if any(x in t for x in ["surface", "lipss", "ablation", "roughness", "nanohole"]):
        return "surface-ablation"
    return "other"


def cmd_summarize(args) -> None:
    target = resolve_target(args)
    items, _, _ = existing_items(target["library_id"], target["collection_key"])
    rows = []
    for item in items:
        data = item.get("data", {})
        if not args.include_arxiv and is_arxivish(data):
            continue
        if not args.include_no_doi and not normalize_doi(data.get("DOI")):
            continue
        title = data.get("title", "")
        rows.append(
            {
                "key": data.get("key", ""),
                "title": title,
                "year": data.get("date", ""),
                "journal": data.get("publicationTitle", ""),
                "doi": data.get("DOI", ""),
                "url": data.get("url", ""),
                "has_pdf_attachment": has_pdf_attachment(item),
                "theme": theme_for(title),
                "date_added": data.get("dateAdded", ""),
            }
        )
    rows.sort(key=lambda r: (r["theme"], str(r["year"]), r["title"]))
    out_dir = Path(args.work_dir) / "summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    if rows:
        with (out_dir / "formal_titles.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "formal_titles.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "count": len(rows),
        "with_pdf_attachment": sum(1 for r in rows if r["has_pdf_attachment"]),
        "without_pdf_attachment": sum(1 for r in rows if not r["has_pdf_attachment"]),
        "theme_counts": dict(Counter(r["theme"] for r in rows).most_common()),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False, indent=2))


def review_row(library_id: str, item: dict, args) -> dict:
    data = item.get("data", {})
    fulltext = ""
    indexed_chars = 0
    attachment_count = 0
    if not args.no_fulltext:
        fulltext, indexed_chars, attachment_count = item_fulltext(
            library_id,
            item,
            max_chars=max(args.fulltext_chars, 2000),
        )
    title = clean_text(data.get("title"))
    abstract = abstract_or_fulltext(data, fulltext)
    conclusion = conclusion_from_fulltext(fulltext)
    evidence_text = f"{title} {abstract} {conclusion} {fulltext[:3000]}"
    theme, project_note = review_theme(evidence_text)
    read_status = "全文已索引" if indexed_chars >= 2000 else ("有PDF但未索引全文" if attachment_count else "无PDF/未找到附件")
    if not indexed_chars and abstract:
        read_status = "仅读摘要"
    score = project_score(data, evidence_text)
    return {
        "key": data.get("key", ""),
        "year": parse_year(data.get("date")),
        "title": title,
        "authors": creators_text(data),
        "journal": clean_text(data.get("publicationTitle")),
        "doi": normalize_doi(data.get("DOI")),
        "theme": theme,
        "score": score,
        "read_status": read_status,
        "keywords": found_keywords(evidence_text),
        "parameters": parameter_terms(evidence_text),
        "project_note": project_note,
        "application_position": application_position(theme),
        "abstract_digest": short_text(abstract, 260),
        "conclusion_digest": short_text(conclusion, 260),
        "indexed_chars": indexed_chars,
        "attachment_count": attachment_count,
        "url": data.get("url", ""),
    }


def cmd_review_collection(args) -> None:
    target = resolve_target(args)
    items, _, _ = existing_items(target["library_id"], target["collection_key"])
    rows = []
    for idx, item in enumerate(items, 1):
        data = item.get("data", {})
        if not args.include_arxiv and is_arxivish(data):
            continue
        if not args.include_no_doi and not normalize_doi(data.get("DOI")):
            continue
        row = review_row(target["library_id"], item, args)
        if row["score"] < args.min_score:
            continue
        rows.append(row)
        if args.limit and len(rows) >= args.limit:
            break
        if idx % 20 == 0:
            log(f"reviewed {idx} Zotero items; kept {len(rows)}")
    rows.sort(key=lambda r: (r["score"], r["year"], r["title"]), reverse=True)

    out_dir = Path(args.work_dir) / "review"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / args.csv_name
    md_path = out_dir / args.md_name
    draft_path = out_dir / args.draft_name

    fieldnames = [
        "year",
        "title",
        "authors",
        "journal",
        "doi",
        "theme",
        "score",
        "read_status",
        "keywords",
        "parameters",
        "project_note",
        "application_position",
        "abstract_digest",
        "conclusion_digest",
        "indexed_chars",
        "attachment_count",
        "key",
        "url",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    table_rows = []
    for idx, row in enumerate(rows[: args.markdown_rows], 1):
        table_rows.append(
            {
                "idx": idx,
                "year": row["year"] or "",
                "title": short_text(row["title"], 88),
                "doi": row["doi"],
                "theme": row["theme"],
                "score": row["score"],
                "read_status": row["read_status"],
                "position": row["application_position"],
            }
        )
    md = [
        f"# {args.project_title}：Zotero 文献阅读表",
        "",
        f"- 集合：{target['selected'].get('name') or target['collection_key']}",
        f"- DOI 正式文献数量：{len(rows)}",
        f"- 全文已索引：{sum(1 for r in rows if r['read_status'] == '全文已索引')}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 主题分布",
        "",
    ]
    theme_counts = Counter(r["theme"] for r in rows)
    for theme, count in theme_counts.most_common():
        md.append(f"- {theme}: {count}")
    md += [
        "",
        "## 高相关文献表",
        "",
        markdown_table(
            ["#", "年份", "题名", "DOI", "方向", "相关度", "读取状态", "申请书位置"],
            table_rows,
            ["idx", "year", "title", "doi", "theme", "score", "read_status", "position"],
        ),
        "",
        "## 使用提示",
        "",
        "- 申请书正文优先引用 `score` 高、`read_status=全文已索引`、方向贴近“化学刻蚀释放/切割质量/改性机理”的论文。",
        "- `abstract_digest` 和 `conclusion_digest` 是为快速筛读准备的短摘录，正式写作时应回到 PDF 核对原文。",
        "- 无 DOI 或 arXiv/preprint 默认不计入正式文献；如需前沿补充，单独导出并标注。",
        "",
    ]
    md_path.write_text("\n".join(md), encoding="utf-8")

    draft_path.write_text(make_research_status_draft(rows, args.project_title), encoding="utf-8")
    log(json.dumps({"rows": len(rows), "csv": str(csv_path), "markdown": str(md_path), "draft": str(draft_path)}, ensure_ascii=False, indent=2))


def make_research_status_draft(rows: list[dict], project_title: str) -> str:
    by_theme: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_theme[row["theme"]].append(row)
    for theme_rows in by_theme.values():
        theme_rows.sort(key=lambda r: (r["score"], r["year"]), reverse=True)

    def cite_line(row: dict) -> str:
        year = row["year"] or "年份待核"
        doi = row["doi"] or "DOI待核"
        title = short_text(row["title"], 80)
        return f"{title}（{year}，DOI: {doi}）"

    lines = [
        f"# {project_title}：国内外研究现状初稿",
        "",
        "说明：本稿由 Zotero 集合中的 DOI 文献和已索引 PDF 自动整理，适合作为青苗计划申请书的底稿；提交前需再按学校格式压缩、润色并核对引用格式。",
        "",
        "## 国外研究现状",
        "",
    ]
    ordered = [
        "SLE/FLICE与化学刻蚀释放",
        "切割、隐形切割与Bessel光束加工",
        "熔融石英改性机理与选择性来源",
        "表面质量、粗糙度与缺陷修复",
        "三维玻璃微结构与器件制造",
    ]
    theme_intro = {
        "SLE/FLICE与化学刻蚀释放": "在透明玻璃材料微加工领域，超快激光诱导选择性刻蚀已经形成较成熟的技术路线：先利用聚焦超快脉冲在熔融石英或玻璃内部产生局部结构改性，再通过 HF、KOH、NaOH 等刻蚀体系实现改性区的优先去除，从而获得微通道、三维结构或释放结构。",
        "切割、隐形切割与Bessel光束加工": "围绕透明硬脆材料的切割与释放，国外研究大量关注 Bessel 光束、激光成丝、隐形切割和少脉冲加工等路线，核心目标是扩大加工深度、降低热影响和控制断面缺陷。",
        "熔融石英改性机理与选择性来源": "在机理方面，熔融石英内部会出现致密化、应力场、纳米光栅、微爆破和等离子体相关效应，这些改性决定后续化学刻蚀速率、侧壁形貌和结构完整性。",
        "表面质量、粗糙度与缺陷修复": "在质量评价方面，已有工作关注表面粗糙度、微裂纹、侧壁形貌、缺陷修复和激光抛光等问题，为建立崩边尺寸、Ra 粗糙度、侧壁垂直度等指标提供了参考。",
        "三维玻璃微结构与器件制造": "应用层面，超快激光结合刻蚀或后处理已用于微流控芯片、光流控器件、微腔、MEMS 和复杂三维玻璃结构制造，说明该路线具备器件化潜力。",
    }
    for theme in ordered:
        theme_rows = by_theme.get(theme, [])
        if not theme_rows:
            continue
        lines += [f"### {theme}", "", theme_intro[theme]]
        examples = [cite_line(r) for r in theme_rows[:5]]
        if examples:
            lines.append("当前集合中可优先引用的代表性文献包括：" + "；".join(examples) + "。")
        lines.append("")

    lines += [
        "## 国内研究现状与本项目切入点",
        "",
        "结合当前实验基础，国内相关工作可从“超快激光加工透明硬脆材料”“熔融石英器件加工”“激光辅助化学刻蚀后处理”三个层面展开梳理。现有研究多证明超快激光能够实现玻璃/熔融石英微结构加工，但面向具体齿形结构的切割释放质量控制、崩边-粗糙度-垂直度协同评价，以及加工参数与化学刻蚀效果之间的定量关系仍有进一步研究空间。",
        "",
        "本项目已有 200 μm 熔融石英齿形结构实验基础，可围绕激光功率、重复频率、脉宽、扫描点间距和刻蚀释放条件，建立工艺参数与崩边尺寸、断面粗糙度、侧壁垂直度和释放完整性的关系。与已有文献相比，项目的切入点不是单纯证明超快激光能加工玻璃，而是把“超快激光辅助化学刻蚀”落实到齿形结构切割释放质量优化上。",
        "",
        "## 申请书可写的不足与创新点",
        "",
        "1. 已有选择性激光刻蚀研究多以微通道、微流控或三维结构为目标，对薄型齿形结构释放过程中的边缘崩边和断面粗糙度关注不足。",
        "2. 透明硬脆材料切割研究常强调切缝、速度或深宽比，但与后续化学刻蚀释放耦合的质量评价体系仍不充分。",
        "3. 熔融石英内部改性机理研究较多，但面向工程化齿形结构时，仍需要把脉冲参数、改性连续性、刻蚀选择性和最终几何质量联系起来。",
        "4. 本项目可形成一套面向熔融石英齿形结构的超快激光辅助化学刻蚀切割释放工艺窗口和评价方法，为后续石英精密器件加工提供实验依据。",
        "",
    ]
    return "\n".join(lines)


def cmd_reference_doi_candidates(args) -> None:
    target = resolve_target(args)
    items, existing_dois, _ = existing_items(target["library_id"], target["collection_key"])
    sources = []
    for item in items:
        data = item.get("data", {})
        if not args.include_arxiv and is_arxivish(data):
            continue
        doi = normalize_doi(data.get("DOI"))
        if doi:
            sources.append({"doi": doi, "title": clean_text(data.get("title")), "year": parse_year(data.get("date"))})
    sources = sources[: args.limit]

    candidates: dict[str, dict] = {}
    for idx, source in enumerate(sources, 1):
        message = crossref_work(source["doi"])
        if not message:
            continue
        for ref in message.get("reference") or []:
            ref_doi = normalize_doi(ref.get("DOI") or ref.get("doi"))
            if not ref_doi or ref_doi in existing_dois:
                continue
            rec = candidates.setdefault(
                ref_doi,
                {
                    "doi": ref_doi,
                    "title": clean_text(ref.get("article-title") or ref.get("unstructured") or ""),
                    "journal": clean_text(ref.get("journal-title") or ""),
                    "year": ref.get("year") or "",
                    "source_count": 0,
                    "source_titles": [],
                },
            )
            rec["source_count"] += 1
            rec["source_titles"].append(source["title"])
        if idx % 10 == 0:
            log(f"Crossref checked {idx}/{len(sources)} source papers; candidates {len(candidates)}")
        time.sleep(args.delay)

    enriched = []
    for rec in candidates.values():
        message = crossref_work(rec["doi"]) if args.enrich else None
        if message:
            rec["title"] = crossref_title(message) or rec["title"]
            rec["journal"] = clean_text((message.get("container-title") or [""])[0]) or rec["journal"]
            rec["year"] = issued_year(message) or rec["year"]
        text = f"{rec['title']} {rec['journal']} {' '.join(rec['source_titles'][:3])}".lower()
        rec["score"] = rec["source_count"] * 10
        for token in ["femtosecond", "ultrafast", "fused silica", "quartz", "glass", "etch", "cut", "bessel", "roughness"]:
            if token in text:
                rec["score"] += 5
        rec["source_titles"] = "；".join(short_text(t, 70) for t in rec["source_titles"][:5])
        enriched.append(rec)
    enriched.sort(key=lambda r: (r["score"], r["source_count"], str(r["year"])), reverse=True)
    if args.max_candidates:
        enriched = enriched[: args.max_candidates]

    out_dir = Path(args.work_dir) / "reference-candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / args.csv_name
    md_path = out_dir / args.md_name
    fieldnames = ["doi", "title", "journal", "year", "source_count", "score", "source_titles"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched)
    table = markdown_table(
        ["DOI", "题名", "期刊", "年份", "被集合内几篇引用", "分数"],
        [{**r, "title": short_text(r["title"], 90)} for r in enriched[: args.markdown_rows]],
        ["doi", "title", "journal", "year", "source_count", "score"],
    )
    md_path.write_text(
        "\n".join(
            [
                "# 可继续下载的参考文献 DOI 候选",
                "",
                "这些 DOI 来自集合内论文在 Crossref 中公开的参考文献列表；脚本不会自动导入，先人工筛选再用 `import-openalex` 或 Zotero Connector 下载。",
                "",
                table,
                "",
            ]
        ),
        encoding="utf-8",
    )
    log(json.dumps({"sources_checked": len(sources), "candidates": len(enriched), "csv": str(csv_path), "markdown": str(md_path)}, ensure_ascii=False, indent=2))


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--library-id", help="Zotero local library id. Defaults to selected collection library.")
    parser.add_argument("--collection-key", help="Zotero collection key. Defaults to selected collection name lookup.")
    parser.add_argument("--target-tree-id", help="Zotero treeView id such as C77. Defaults to selected collection id.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    add_target_args(doctor)
    doctor.set_defaults(func=cmd_doctor)

    imp = sub.add_parser("import-openalex")
    add_target_args(imp)
    imp.add_argument("--query", action="append", help="OpenAlex search query. Repeatable.")
    imp.add_argument("--target-formal", type=int, default=100)
    imp.add_argument("--max-new", type=int, default=20)
    imp.add_argument("--pages", type=int, default=4)
    imp.add_argument("--per-page", type=int, default=50)
    imp.add_argument("--allow-arxiv", action="store_true", help="Allow arXiv PDF URLs and arXiv-like records.")
    imp.add_argument("--allow-no-doi", action="store_true", help="Allow records without DOI. Disabled by default.")
    imp.add_argument("--download-timeout", type=int, default=180)
    imp.add_argument("--delay", type=float, default=0.4)
    imp.add_argument("--work-dir", default="zotero-open-literature-work")
    imp.set_defaults(func=cmd_import_openalex)

    summ = sub.add_parser("summarize")
    add_target_args(summ)
    summ.add_argument("--include-arxiv", action="store_true")
    summ.add_argument("--include-no-doi", action="store_true")
    summ.add_argument("--work-dir", default="zotero-open-literature-work")
    summ.set_defaults(func=cmd_summarize)

    review = sub.add_parser("review-collection")
    add_target_args(review)
    review.add_argument("--project-title", default=PROJECT_TITLE)
    review.add_argument("--include-arxiv", action="store_true")
    review.add_argument("--include-no-doi", action="store_true")
    review.add_argument("--no-fulltext", action="store_true", help="Do not query Zotero attachment fulltext indexes.")
    review.add_argument("--fulltext-chars", type=int, default=12000)
    review.add_argument("--min-score", type=int, default=0)
    review.add_argument("--limit", type=int, default=0, help="Maximum kept DOI papers; 0 means no limit.")
    review.add_argument("--markdown-rows", type=int, default=80)
    review.add_argument("--work-dir", default="zotero-open-literature-work")
    review.add_argument("--csv-name", default="literature_review_table.csv")
    review.add_argument("--md-name", default="literature_review_table.md")
    review.add_argument("--draft-name", default="research_status_draft.md")
    review.set_defaults(func=cmd_review_collection)

    refs = sub.add_parser("reference-doi-candidates")
    add_target_args(refs)
    refs.add_argument("--include-arxiv", action="store_true")
    refs.add_argument("--limit", type=int, default=40, help="How many existing DOI papers to inspect via Crossref.")
    refs.add_argument("--max-candidates", type=int, default=80)
    refs.add_argument("--markdown-rows", type=int, default=80)
    refs.add_argument("--enrich", action="store_true", help="Fetch Crossref metadata for each candidate DOI.")
    refs.add_argument("--delay", type=float, default=0.1)
    refs.add_argument("--work-dir", default="zotero-open-literature-work")
    refs.add_argument("--csv-name", default="reference_doi_candidates.csv")
    refs.add_argument("--md-name", default="reference_doi_candidates.md")
    refs.set_defaults(func=cmd_reference_doi_candidates)

    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
