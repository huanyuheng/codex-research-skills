# Workflow Notes

## Purpose

This skill captures a repeatable workflow for building a Zotero collection of formal papers with downloadable PDFs. It was designed after a real session that required 100 formal papers for a graduate research proposal.

## Selection Rules

Core literature should satisfy all of these:

- Has a DOI or clear formal publication source.
- Source is a journal, conference, or proceedings venue.
- PDF attachment is actually saved in Zotero.
- PDF comes from a legal open-access source, institutional repository, publisher OA page, or public repository.

Exclude from the formal count unless explicitly requested:

- arXiv-only records.
- bioRxiv/medRxiv preprints.
- `content.openalex.org` cache URLs that require credentials or return API errors.
- Metadata-only items without a PDF attachment.

## Recommended Import Strategy

1. Start with narrow OpenAlex queries.
2. Deduplicate by DOI and normalized title against the Zotero collection.
3. Prefer repository and publisher domains that return stable PDF bytes in the current network environment.
4. Download one batch at a time, then re-count formal items.
5. Export a report and review title clusters before proposing research-project titles.

## Network Notes

Some domains return HTML, Cloudflare pages, or reset TLS connections for command-line clients. The script treats this as a failure and moves on. Do not weaken validation just to increase counts.

Examples of acceptable outcomes:

- PDF bytes begin with `%PDF`.
- The file is complete despite a timeout and contains `%%EOF` near the end.

Examples of rejected outcomes:

- `<html`, `<!doctype`, JSON errors, or 403 pages.
- Very small files.
- Publisher landing pages.

## Extension Points

Future versions can add:

- Unpaywall support if the user provides a real contact email.
- GROBID support for robust PDF structure parsing, bibliography extraction, and TEI output when Zotero's local full-text index is insufficient.
- `grobid-client-python` support for batch processing local PDF folders through a self-hosted GROBID service.
- Semantic Scholar enrichment when API access is available.
- Better domain ranking by recording local success/failure statistics.
- A cleanup command that tags preprints separately, after explicit user approval.

## Paper Reading Workflow

Use the lightest reliable reading source first:

1. Zotero local metadata and DOI fields for filtering.
2. Zotero attachment children and `/fulltext` local API for indexed PDF text.
3. Crossref `/works/{doi}` for DOI metadata and reference DOI candidates.
4. GROBID only when the task requires structured section extraction, reference parsing from PDF, or citation-context analysis beyond Zotero's full-text index.

For Chinese proposal writing, the generated table should map each paper to:

- research theme;
- project relevance;
- read status;
- possible application section in the proposal;
- DOI and journal/source.

Keep all no-DOI, arXiv-only, and metadata-only records out of the formal count unless the user explicitly asks for frontier/preprint awareness.

## GitHub Tooling Notes

Useful open-source projects checked while updating this skill:

- `grobidOrg/grobid`: production-grade PDF-to-structured-TEI extraction, header parsing, full text structuring, and reference parsing.
- `grobidOrg/grobid-client-python`: Python CLI/library for concurrent batch calls to a GROBID service.

Current script does not require either dependency. They are best treated as optional future integrations because Zotero already exposes local indexed full text for many saved PDFs and Crossref can return DOI-bearing reference lists without running a separate service.
