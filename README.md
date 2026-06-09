# Codex Research Skills

Reusable Codex skills for research workflows.

This repository starts with one skill:

- `zotero-open-literature`: search OpenAlex, download legally available open-access PDFs, import formal DOI-bearing literature into Zotero, read Zotero-indexed PDFs, export proposal-ready literature tables, and mine DOI-only reference candidates through Crossref.

The repository is intentionally structured so more skills can be added later under `skills/<skill-name>/`.

## Install

Clone this repository, then copy or symlink the skill folder into your Codex skills directory.

```bash
git clone https://github.com/huanyuheng/codex-research-skills.git
```

Windows PowerShell example:

```powershell
$repo = "E:\path\to\codex-research-skills"
$dest = "$env:USERPROFILE\.codex\skills\zotero-open-literature"
Copy-Item -Recurse -Force "$repo\skills\zotero-open-literature" $dest
```

Restart Codex after installing the skill.

## Use

In Codex, ask for:

```text
Use $zotero-open-literature to build a formal Zotero literature set with legal open-access PDFs and summarize the titles by theme.
Use $zotero-open-literature to read my Zotero collection, generate a literature review table, and draft domestic/foreign research status.
```

You can also run the bundled script directly:

```bash
cd skills/zotero-open-literature
python scripts/zotero_open_literature.py doctor
python scripts/zotero_open_literature.py import-openalex --target-formal 100 --max-new 20
python scripts/zotero_open_literature.py summarize
python scripts/zotero_open_literature.py review-collection --project-title "Your project title"
python scripts/zotero_open_literature.py reference-doi-candidates --limit 40
```

## Notes

- The workflow requires Zotero Desktop to be running.
- It uses Zotero's local Connector API at `127.0.0.1:23119`.
- It does not use Sci-Hub, shadow libraries, or paywall bypasses.
- arXiv/preprints are excluded from the formal count unless explicitly enabled.
- No-DOI records are excluded from formal import/review by default.

## Add More Skills

Add each new skill as:

```text
skills/
  new-skill-name/
    SKILL.md
    scripts/
    references/
    agents/openai.yaml
```

Keep workflows narrow, executable, and easy to validate.
