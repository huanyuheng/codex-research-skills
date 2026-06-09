---
name: zotero-open-literature
description: Use when Codex needs to build or extend a Zotero literature collection with legally downloadable open-access PDFs, especially for formal DOI-bearing journal or conference papers; trigger on Zotero literature import, OpenAlex paper search, DOI/PDF batch download, "100 papers", "download papers into Zotero", filtering out arXiv/preprints, or summarizing Zotero titles by research theme.
---

# Zotero Open Literature

Build a Zotero collection of formal, citable papers with real PDF attachments. The default workflow uses Zotero Desktop's local Connector API, OpenAlex metadata, and only URLs that return valid PDF bytes. It is designed for research-project preparation where preprints should not be counted as core literature.

## Guardrails

- Use only legal open-access routes: publisher OA PDFs, institutional repositories, PMC, arXiv only when the user explicitly accepts preprints, and other public repositories.
- Do not use Sci-Hub, shadow libraries, paywall bypasses, or credentials that the user did not explicitly provide.
- Count "formal core literature" as non-arXiv items with DOI and a PDF attachment.
- Keep preprints separate from the formal count. They may be useful for frontier awareness, but do not use them to satisfy "formal/citable paper count" unless the user approves.
- Never delete existing Zotero items unless the user explicitly asks. Prefer tags or exported reports for cleanup.

## Quick Start

1. Start Zotero Desktop.
2. Select the target Zotero collection in the Zotero UI.
3. Run a dry check:

```bash
python scripts/zotero_open_literature.py doctor
```

4. Search, download, and import formal OA papers:

```bash
python scripts/zotero_open_literature.py import-openalex \
  --query "femtosecond laser fused silica selective etching" \
  --target-formal 100 \
  --max-new 20 \
  --work-dir ./zotero-open-literature-work
```

5. Export a title/theme report:

```bash
python scripts/zotero_open_literature.py summarize \
  --work-dir ./zotero-open-literature-work
```

## Workflow

### 1. Confirm Zotero Target

Run `doctor`. It should confirm:

- Zotero is reachable at `http://127.0.0.1:23119/connector/ping`.
- A Zotero collection is selected.
- The local API can read top-level items for the selected collection.

If selection discovery fails, ask the user to select the collection manually in Zotero. Use explicit arguments only when necessary:

```bash
python scripts/zotero_open_literature.py doctor \
  --library-id 17442387 \
  --collection-key ACPDVYSC \
  --target-tree-id C77
```

You may also set `ZOTERO_USER_ID` when Zotero reports an internal library id but the local API asks for the logged-in user id:

```bash
ZOTERO_USER_ID=17442387 python scripts/zotero_open_literature.py doctor
```

### 2. Build Candidate Set

Prefer OpenAlex for broad discovery because it returns DOI, source metadata, citation counts, abstracts, and OA PDF locations in one JSON response. Use focused queries and several pages rather than one huge generic search.

Good query patterns:

- `femtosecond laser fused silica selective etching`
- `ultrafast laser fused silica nanogratings`
- `Bessel beam glass cutting femtosecond laser`
- `femtosecond laser glass microfluidics`

### 3. Import Only Real PDFs

The bundled script validates every download:

- starts with `%PDF`
- is larger than a minimal size threshold
- for timeout cases, accepts the file only if the tail contains `%%EOF`

If a publisher returns an HTML landing page, the script rejects it and tries the next candidate URL.

### 4. Separate Formal Papers and Preprints

By default `import-openalex` excludes `arxiv.org` and `content.openalex.org` PDF URLs. It also skips records whose source looks like arXiv/preprint and skips records without DOI. If the user explicitly wants preprints or no-DOI records, run a separate pass into another collection or tag them separately.

### 5. Summarize

Run `summarize` after import. It writes:

- `formal_titles.csv`
- `formal_titles.json`
- `summary.json`

Use these outputs to brainstorm project titles, literature clusters, and application-writing directions.

## Resource Files

- `scripts/zotero_open_literature.py`: main CLI for doctor, OpenAlex import, and summary export.
- `references/workflow-notes.md`: implementation notes, known network failure modes, and extension points.

## Common Failure Modes

- **Zotero connection refused**: Zotero Desktop is not running.
- **Selected collection is wrong**: user must click the desired collection in Zotero or pass explicit collection arguments.
- **HTML instead of PDF**: publisher blocks command-line access or redirects to a landing page; this is rejected.
- **OpenAlex content URL asks for an API key**: skip `content.openalex.org`; prefer original OA locations.
- **Unpaywall requires a real email**: do not invent emails. Ask the user for a contact email if Unpaywall is added later.
