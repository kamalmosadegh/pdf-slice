#!/usr/bin/env python3
"""MCP server for pdf-slice (stdio transport).

Exposes the extractor to Claude Code, Cursor and Codex over MCP.
Local only: no API key, no paid service, no network beyond the URLs
the user explicitly asks to extract.

Tools:
  extract_pdf(source, ocr_lang="eng")
      Extract a PDF (local path or URL) -> markdown + metadata + warnings.
  verify_pdf(source, second_source)
      Extract the same document from two routes and report how much the
      outputs agree (catches silent mix-ups like Firecrawl #3968).

Run:
    python mcp_server.py        # stdio MCP server
    python mcp_server.py --help # usage
Entry point (after install):  pdf-slice-mcp
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import DEFAULT_CACHE_DIR, process_source  # noqa: E402

TOOL_VERSION = "1.1.0"


def _word_set(text: str) -> set:
    return set(re.findall(r"\w+", text.lower()))


def tool_extract_pdf(source: str, ocr_lang: str = "eng") -> str:
    try:
        meta, markdown, elements = process_source(
            source, DEFAULT_CACHE_DIR, False, 50, 200, ocr_lang)
    except RuntimeError as exc:
        return json.dumps({"ok": False, "error": str(exc)},
                          ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "source": meta["source"],
        "content_hash": meta["content_hash"],
        "cache_state": meta["cache_state"],
        "extraction_path": meta["extraction_path"],
        "pages": meta["pages"],
        "total_chars": meta["total_chars"],
        "chunks": meta["chunks"],
        "warnings": meta["warnings"],
        "markdown": markdown,
    }, ensure_ascii=False)


def tool_verify_pdf(source: str, second_source: str) -> str:
    """Extract from two routes and compare. High agreement is expected
    when both routes serve the same document; low agreement is a loud
    signal that one route returned different content."""
    try:
        meta1, md1, _ = process_source(
            source, DEFAULT_CACHE_DIR, True, 50, 200, "eng")
        meta2, md2, _ = process_source(
            second_source, DEFAULT_CACHE_DIR, True, 50, 200, "eng")
    except RuntimeError as exc:
        return json.dumps({"ok": False, "error": str(exc)},
                          ensure_ascii=False)
    w1, w2 = _word_set(md1), _word_set(md2)
    union = w1 | w2
    word_overlap = len(w1 & w2) / len(union) if union else 1.0
    seq_ratio = difflib.SequenceMatcher(
        None, md1, md2).ratio() if (md1 or md2) else 1.0
    match_rate = round((word_overlap + seq_ratio) / 2, 3)
    return json.dumps({
        "ok": True,
        "first": {"source": meta1["source"],
                  "content_hash": meta1["content_hash"],
                  "extraction_path": meta1["extraction_path"],
                  "total_chars": meta1["total_chars"]},
        "second": {"source": meta2["source"],
                   "content_hash": meta2["content_hash"],
                   "extraction_path": meta2["extraction_path"],
                   "total_chars": meta2["total_chars"]},
        "match_rate": match_rate,
        "word_overlap": round(word_overlap, 3),
        "sequence_ratio": round(seq_ratio, 3),
        "words_only_in_first": sorted(w1 - w2)[:50],
        "words_only_in_second": sorted(w2 - w1)[:50],
        "same_content_hash": meta1["content_hash"] == meta2["content_hash"],
        "verdict": ("same document" if match_rate >= 0.9 else
                    "DIFFERENT content — investigate before trusting either"),
        "warnings": meta1["warnings"] + meta2["warnings"],
    }, ensure_ascii=False)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        print("Tools: extract_pdf(source, ocr_lang='eng'), "
              "verify_pdf(source, second_source)")
        return 0
    import shutil
    if shutil.which("tesseract") is None:
        print("WARNING: tesseract binary not found — OCR fallback is "
              "disabled; scanned/thin pages will return low-confidence "
              "output.", file=sys.stderr)
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    mcp = FastMCP("pdf-slice")

    _READ_ONLY = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )

    @mcp.tool(annotations=_READ_ONLY)
    def extract_pdf(source: str, ocr_lang: str = "eng") -> str:
        """Extract a PDF file or URL to clean markdown.

        Returns JSON: ok, markdown, extraction_path (native/ocr/fallback),
        per-page detail, warnings, content_hash, cache_state. Warnings are
        always included — never trust markdown with warnings unexamined.
        """
        return tool_extract_pdf(source, ocr_lang)

    @mcp.tool(annotations=_READ_ONLY)
    def verify_pdf(source: str, second_source: str) -> str:
        """Extract the same document from two routes (file/URL) and compare.

        Returns JSON with match_rate 0..1. Use it to check that two routes
        serve the same document; match_rate < 0.9 means DIFFERENT content.
        """
        return tool_verify_pdf(source, second_source)

    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
