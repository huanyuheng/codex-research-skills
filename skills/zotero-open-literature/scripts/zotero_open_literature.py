#!/usr/bin/env python3
"""Build Zotero collections from formal open-access literature.

This script intentionally avoids shadow-library sources. It imports only records
whose PDF bytes are actually downloadable from public OA locations.
"""

from __future__ import annotations

import argparse
import csv
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
from collections import Counter
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


CONNECTOR = "http://127.0.0.1:23119/connector"
OPENALEX = "https://api.openalex.org/works"

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


def safe_slug(text: str, max_len: int = 96) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-").lower()
    return (slug or "paper")[:max_len]


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

    args = parser.parse_args(argv)
    try:
        args.func(args)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
