# pdf-slice — PDF to markdown that never silently returns wrong content

Use this skill whenever you need to extract text from a PDF: reading a
report, feeding a document to RAG, comparing two copies of a document, or
checking what a PDF actually contains.

## When to use it
- A PDF (local file or URL) must become markdown or RAG chunks.
- The document is scanned, has tables, multiple columns, or mixed languages.
- Two routes supposedly serve the same document and you must verify that.
- NEVER silently trust a PDF extraction: always check `warnings` and
  `extraction_path` in the output before using the markdown.

## How to use it

### CLI (local, no API key, no network except the URL you give it)
```bash
# single file -> clean markdown + metadata JSON
pdf-slice report.pdf
# URL (cache key = full URL + SHA-256 of the bytes, never the filename)
pdf-slice https://example.com/api/docs/123
# RAG chunks with provenance (one JSON object per chunk)
pdf-slice report.pdf --format jsonl
# both markdown and chunks
pdf-slice report.pdf --format both
# a whole folder + summary report
pdf-slice --batch ./pdfs --out-dir ./out
# Persian OCR
pdf-slice scanned-fa.pdf --ocr-lang eng+fas
```

### MCP tools (Claude Code / Cursor / Codex)
- `extract_pdf(source, ocr_lang="eng")` — returns JSON with `markdown`,
  `extraction_path`, per-page detail, `warnings`, `content_hash`.
- `verify_pdf(source, second_source)` — extracts from two routes and
  returns `match_rate` 0..1 plus the words that differ. Treat
  `match_rate < 0.9` as DIFFERENT content and investigate.

### Reading the output
- `extraction_path`: `native` (text layer, fast) / `ocr` (scanned or thin
  text, tesseract) / `fallback` (still thin after OCR — low confidence).
- `warnings`: non-empty means look before you trust the markdown. A table
  that was detected but parsed unreliably, merged cells, or a page that
  stayed thin after OCR are all reported here.
- `content_hash` (SHA-256 of the PDF bytes): the identity of the document.
  Two files with the same name but different hashes are different documents.
- JSONL chunks have `text`, `page`, `element_type`
  (heading/paragraph/table), `confidence`, `source` (uri + content hash),
  `reading_order`.

## Rules
- If `warnings` is non-empty, say so when you hand the markdown onward.
- If `extraction_path` is `fallback`, say the result is low-confidence.
- If `verify_pdf` reports `match_rate < 0.9`, do NOT pick one version
  silently — report the disagreement.
- Do not use this tool for crawling, anti-bot work, or whole-site scraping:
  it extracts single PDFs only, by design.

## Install
```bash
git clone <repo> && cd firecrawl-pdf-slice
python -m venv venv && venv/bin/pip install -r requirements.txt
# one-command install (after publishing):
uvx pdf-slice --help
# MCP: add mcp-config-claude.json / mcp-config-cursor.json to your client
# (adjust the absolute paths inside to your machine).
```
