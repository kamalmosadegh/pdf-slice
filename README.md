# pdf-slice

**PDF → clean markdown that never silently returns wrong content.**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP server](https://img.shields.io/badge/MCP-server-purple.svg)](#mcp-server--agent-skill)
[![M8ven Score](https://m8ven.ai/badge/mcp/kamalmosadegh-pdf-slice-sygq4y?v=33cc6431a91e5c70b91ddef3b9edc613)](https://m8ven.ai/mcp/kamalmosadegh-pdf-slice-sygq4y?s=readme)
[![M8ven: Verified Publisher](https://img.shields.io/badge/M8ven-Verified_Publisher-2ea44f)](https://m8ven.ai/mcp/kamalmosadegh/pdf-slice) [![M8ven: Live Monitored](https://img.shields.io/badge/M8ven-Live_Monitored-blue)](https://m8ven.ai/mcp/kamalmosadegh/pdf-slice) [![Glama: listed](https://img.shields.io/badge/Glama-listed-purple)](https://glama.ai/mcp/servers/kamalmosadegh/pdf-slice)

A small, local, CPU-only tool that converts a PDF (file or URL) to clean
markdown — and is engineered around one rule: **if the output might be
wrong or incomplete, it says so, loudly.** No silent failures, no wrong
documents, no empty results dressed up as success.

[فارسی](README_FA.md) · [Agent skill](skills/pdf-slice/SKILL.md)

## Why this exists

pdf-slice is built directly from two real, silent failure modes reported
against Firecrawl's PDF pipeline:

- **Cache served the wrong document** — several different files that
  shared the name `Nexus.pdf` collided in the cache, so a user asking for
  the April report silently received the March report, with a `200`
  status and correct-looking metadata. pdf-slice keys its cache by
  **full URL + SHA-256 of the downloaded bytes** — never the filename —
  and re-verifies the content hash before serving anything from cache. A
  mismatch means a fresh extraction, never the wrong document.
- **White text hidden in nested forms** — a 46-page PDF whose body text
  was white, nested inside form XObjects under an opaque image, came back
  as ~3,000 header-only characters while other extractors found 86,000+.
  The classifier had declared "text-based, confidence 1.0, no OCR
  needed". pdf-slice treats a suspiciously thin native extraction as a
  signal, not an answer: it **falls back to OCR automatically** and
  always reports which path produced the output.

## What you get

- **Clean markdown** with natural reading order (multi-column aware) and
  tables preserved as markdown tables. A table that is detected but
  cannot be parsed reliably produces a warning — never silent loss.
- **Provenance metadata (JSON)** for every run: `extraction_path`
  (`native` / `ocr` / `fallback`), per-page origin, character counts,
  table counts and timings, `content_hash`, cache state
  (`hit` / `miss` / `miss-hash-mismatch`), and `warnings`.
- **RAG chunks (JSONL)** with provenance: each chunk carries `text`,
  `page`, `element_type` (`heading` / `paragraph` / `table`),
  `confidence`, `source` (URI + content hash) and `reading_order`.
- **Batch mode**: process a whole folder of PDFs with one command and
  get a summary report (files, OCR pages, warnings).
- **Loud failures**: encrypted, corrupt, empty, zero-byte and non-PDF
  inputs fail with a clear error — never with empty "successful" output.
- **MCP server** for Claude Code, Cursor and Codex, plus a ready-made
  **agent skill** (`skills/pdf-slice/SKILL.md`).

## Install

```bash
# from source
git clone https://github.com/kamalmosadegh/pdf-slice && cd pdf-slice
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python extract.py --help

# or as a package (console entry points)
pip install .
pdf-slice --help

# one command, after PyPI release
uvx pdf-slice --help
```

Dependencies are light and CPU-only: PyMuPDF, pytesseract, Pillow and the
MCP SDK. No GPU models, no API key, no paid service.

OCR uses the system `tesseract` binary (`eng` and `fas` supported). If it
is missing, the tool continues with a loud warning and returns
low-confidence output for scanned pages — never silence:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-fas
```

## Usage

```bash
# one file -> markdown + JSON metadata
pdf-slice report.pdf

# a URL (cache key = full URL + content hash)
pdf-slice https://example.com/api/docs/123

# RAG chunks with provenance (JSONL, one object per line)
pdf-slice report.pdf --format jsonl
# markdown and chunks together
pdf-slice report.pdf --format both

# a whole folder + summary report
pdf-slice --batch ./pdfs --out-dir ./out

# Persian OCR
pdf-slice scanned-fa.pdf --ocr-lang eng+fas

# thresholds (defaults: 50 chars/page; 200 for pages with images)
pdf-slice scan.pdf --min-chars-per-page 80 --image-page-chars 300
```

**Golden rule:** if `warnings` is non-empty, read them before trusting
the markdown. The `fallback` path means low confidence even after OCR.

## Cache

- URL key: `SHA-256(full URL)`; cached content is served only when the
  stored `content_hash` equals the hash of the freshly downloaded bytes.
- Local file key: `SHA-256` of the file's bytes.
- Default location: `~/.cache/pdf-slice` (override with `--cache-dir` or
  the `PDF_SLICE_CACHE_DIR` environment variable). `--no-cache` disables
  it entirely.

## MCP server & agent skill

```bash
pdf-slice-mcp   # stdio transport, for Claude Code / Cursor / Codex
```

- `extract_pdf(source, ocr_lang="eng")` — markdown + metadata + warnings.
- `verify_pdf(source, second_source)` — extracts the same document from
  two routes and reports a `match_rate`; a low rate is a loud signal that
  one route served different content.
- Ready-made client configs: `mcp-config-claude.json`,
  `mcp-config-cursor.json` (adjust the absolute paths to your machine).

## Benchmark

Measured on the fixtures bundled in `tests/fixtures/`, generated by
`tests/benchmark.py`. "Silent error" = wrong or incomplete content
returned with no warning. The target is zero.

| File | Path | Word recovery | Seconds/page | Silent errors |
|---|---|---|---|---|
| white | native | 100% | 1.575 | 0 |
| black | native | — | 1.480 | 0 |
| scanned | ocr | 100% | 2.802 | 0 |
| multilingual | native | 100% | 1.401 | 0 |
| table | native | 100% | 1.632 | 0 |
| columns | native | — | 1.452 | 0 |
| large (120 pages) | native | — | 0.462 | 0 |

**Total silent errors: 0.**

Run the full pre-delivery test matrix (26 test groups × 3 repeats —
cache collisions, white text, scanned, multilingual, tables, columns,
large, corrupt, encrypted, batch, MCP, clean-install and more):

```bash
python tests/test_slice.py
```

## Deliberately out of scope

A Firecrawl clone, crawling, anti-bot infrastructure, browser rendering,
pricing, SDKs — and heavy layout/formula models (UniMERNet, TableFormer
and friends). pdf-slice's edge is the *never silently wrong* guarantee
and a fast native path, not heavyweight modelling.

## License

MIT — see [LICENSE](LICENSE).


---
## How it compares

Head-to-head against Microsoft MarkItDown 0.1.9 on this repo's own test fixtures (23 Nov 2026, full method and numbers: https://dev.to/kamalmosadegh/markitdown-returned-an-empty-file-for-my-scanned-pdf-so-i-benchmarked-it-against-a-tool-that-5b91):

- **Scanned (image-only) PDF:** MarkItDown exited 0 with 0 characters and no warning. pdf-slice routed the pages to OCR and recovered the full text (699 chars).
- **Simple table:** MarkItDown's output contained zero `|` characters — the grid did not survive. pdf-slice emitted a proper Markdown table.
- **English + Persian:** MarkItDown returned the Persian lines in reversed (visual) order; pdf-slice kept the correct logical reading order.

Honestly: MarkItDown was faster on every fixture, the two-column test was a tie, and its merged-cell table output was arguably cleaner. pdf-slice's edge is not speed — it is that failure never passes silently.
