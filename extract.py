#!/usr/bin/env python3
"""PDF -> clean markdown extractor (Firecrawl PDF slice).

Frozen spec: ~/workspace/firecrawl-pdf-slice/SCOPE_FROZEN.md
Guarantee: never silently return wrong content.
  1. Cache key = FULL URL + SHA-256 of the downloaded bytes (never the
     filename). The stored hash is verified before cached markdown is served;
     on mismatch the document is re-extracted.
  2. Native extraction first, in natural reading order (column-aware).
     Tables are detected and preserved as markdown tables; a table that is
     detected but parses unreliably produces a WARNING, never silence.
     Per page, if native text is suspiciously thin (or the page is
     image-heavy with little text) the page is routed to OCR (tesseract)
     automatically. The output always states which path was used and warns
     instead of staying silent when confidence is low.
  3. No heavy layout/formula models (deliberate): the edge is the
     "never silently wrong" guarantee + the fast native path, not GPU models.

Usage:
    extract.py <pdf-file-or-url> [--out-prefix PATH] [--print]
               [--no-cache] [--cache-dir DIR]
               [--min-chars-per-page N] [--image-page-chars N]
               [--ocr-lang LANG] [--format {md,jsonl,both}]
    extract.py --batch <dir> [--out-dir DIR] [--recursive] [...]
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

import pymupdf  # PyMuPDF

TOOL_VERSION = "1.1.0"

# --- defaults (documented in README_FA.md) ---------------------------------
# A page whose native extraction yields fewer characters than this is "thin"
# and is routed to OCR.
DEFAULT_MIN_CHARS_PER_PAGE = 50
# A page that contains raster images but whose native text is below this is
# OCR-verified as well: this is the Firecrawl #5759 shape (headers-only text
# layer over a raster image / white text inside nested Form XObjects).
DEFAULT_IMAGE_PAGE_CHARS = 200
OCR_DPI = 200

PROJECT_DIR = Path(__file__).resolve().parent
# Default cache is user-local (overridable with --cache-dir or env var),
# so an installed copy never writes into site-packages.
DEFAULT_CACHE_DIR = Path(
    os.environ.get("PDF_SLICE_CACHE_DIR",
                   Path.home() / ".cache" / "pdf-slice"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_pdf_bytes(data: bytes) -> bool:
    return data[:4] == b"%PDF"


def fetch_source(source: str, timeout: int = 30) -> tuple[bytes, str]:
    """Return (pdf_bytes, source_kind). Raises loudly on any problem."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(
            source, headers={"User-Agent": "pdf-slice/1.1 (+local tool)"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except Exception as exc:
            raise RuntimeError(f"download failed for {source}: {exc}") from exc
        if not is_pdf_bytes(data):
            raise RuntimeError(
                f"URL did not return a PDF (first bytes: {data[:16]!r}); "
                "refusing to guess — no silent fallback to HTML."
            )
        return data, "url"
    path = Path(source)
    if not path.is_file():
        raise RuntimeError(f"file not found: {source}")
    data = path.read_bytes()
    if not is_pdf_bytes(data):
        raise RuntimeError(f"not a PDF file: {source}")
    return data, "file"


def clean_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip() == "":
            blank += 1
            if blank <= 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


# --- reading order ----------------------------------------------------------
def order_blocks(blocks):
    """Order text blocks in natural reading order (column-aware).

    Blocks are clustered into columns by horizontal overlap; columns run
    left to right, blocks inside a column top to bottom. A full-width block
    (e.g. a heading) joins the column it overlaps most. Single-column pages
    reduce to plain top-to-bottom order.
    """
    cols: list[list] = []  # each: [x0, x1, [blocks]]
    for b in sorted(blocks, key=lambda b: (b[0], b[1])):
        best = None
        best_overlap = 0.0
        for c in cols:
            overlap = min(b[2], c[1]) - max(b[0], c[0])
            if overlap > 0 and overlap > best_overlap:
                best_overlap = overlap
                best = c
        if best is not None:
            best[2].append(b)
            best[0] = min(best[0], b[0])
            best[1] = max(best[1], b[2])
        else:
            cols.append([b[0], b[2], [b]])
    cols.sort(key=lambda c: c[0])
    ordered: list = []
    for c in cols:
        ordered.extend(sorted(c[2], key=lambda b: (b[1], b[0])))
    return ordered


# --- tables -----------------------------------------------------------------
def _clean_cell(cell) -> str:
    if cell is None:
        return ""
    return str(cell).strip().replace("\n", " ").replace("|", "\\|")


def table_to_markdown(rows: list[list]) -> tuple[str, list[str]]:
    """Render extracted rows as a markdown table; return (md, warnings)."""
    warnings: list[str] = []
    if not rows:
        return "", ["table detected but no rows extracted"]
    cleaned = [[_clean_cell(c) for c in row] for row in rows]
    counts = [len(r) for r in cleaned]
    if len(set(counts)) > 1:
        warnings.append(
            f"table rows have inconsistent column counts {sorted(set(counts))}"
        )
    ncols = max(counts)
    for r in cleaned:
        r.extend([""] * (ncols - len(r)))
    if not any(c for r in cleaned for c in r):
        warnings.append("table detected but all cells are empty")
    header = cleaned[0]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    for r in cleaned[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines), warnings


def detect_tables(page) -> list[dict]:
    """Detect tables; return [{bbox, md, warnings}]. Never raises.

    Cell text is read by clipping to each cell rect instead of trusting
    Table.extract(): extract() mangles values like "$1.20" into "$120\\n."
    (the period is split off as its own word). A cell rect of None means
    the cell is part of a merged span — its text lives in the span's first
    cell, and the flattening is reported as a warning, not hidden.
    """
    found: list[dict] = []
    try:
        tables = list(page.find_tables())
    except Exception:
        return found
    for t in tables:
        try:
            rows: list[list[str]] = []
            merged = False
            for row in t.rows:
                cells: list[str] = []
                for rect in row.cells:
                    if rect is None:
                        merged = True
                        cells.append("")
                    else:
                        txt = page.get_text(
                            "text", clip=pymupdf.Rect(rect))
                        cells.append(txt.strip().replace("\n", " "))
                rows.append(cells)
        except Exception as exc:
            found.append({"bbox": t.bbox, "md": "",
                          "warnings": [f"table extraction failed: {exc}"]})
            continue
        md, warns = table_to_markdown(rows or [])
        if merged:
            warns.append(
                "table contains merged cells; span flattened in markdown")
        found.append({"bbox": t.bbox, "md": md, "warnings": warns})
    return found


def _inside(bbox, x: float, y: float) -> bool:
    return bbox[0] - 1 <= x <= bbox[2] + 1 and bbox[1] - 1 <= y <= bbox[3] + 1


def block_font_size(block_dict) -> float:
    """Dominant (longest-span) font size of a get_text('dict') block."""
    best_size, best_len = 0.0, -1
    for line in (block_dict or {}).get("lines", []):
        for span in line.get("spans", []):
            n = len(span.get("text", ""))
            if n > best_len:
                best_len = n
                best_size = span.get("size", 0.0)
    return best_size


def doc_body_size(doc) -> float:
    """Median dominant font size across text blocks (the body size)."""
    sizes = []
    for page in doc:
        try:
            d = page.get_text("dict")
        except Exception:
            continue
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            s = block_font_size(b)
            if s > 0:
                sizes.append(s)
    if not sizes:
        return 12.0
    sizes.sort()
    return sizes[len(sizes) // 2]


def build_page_elements(blocks, dict_blocks, tables, body_size) -> list[dict]:
    """Assemble one page's elements in reading order.

    Returns [{element_type, text, confidence}]. Headings: short blocks whose
    dominant font is clearly larger than the body size. Tables: rendered
    markdown tables. Everything else: paragraph. Text blocks that fall
    inside a table bbox are skipped (their content is the table's cells).
    """
    if len(blocks) != len(dict_blocks):
        dict_blocks = [None] * len(blocks)
    items = [(b[0], b[1], b[2], b[3], clean_text(b[4]), d)
             for b, d in zip(blocks, dict_blocks)]
    ordered = order_blocks(items)
    # Text blocks first (column order); table cell blocks are skipped here —
    # their content is the table itself. Tables are then inserted by vertical
    # position: before the first text element starting below the table's
    # bottom edge, else at the end. (A centered table must not be treated
    # as a side column — that mis-ordered it after the following paragraph.)
    text_elements: list[tuple[float, dict]] = []
    for it in ordered:
        x0, y0, x1, y1, txt, dblk = it
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        if any(_inside(t["bbox"], cx, cy) for t in tables):
            continue
        if not txt:
            continue
        size = block_font_size(dblk)
        n_lines = txt.count("\n") + 1
        if size >= body_size * 1.15 and len(txt) < 120 and n_lines <= 2:
            el = {"element_type": "heading", "text": txt, "confidence": 0.95}
        else:
            el = {"element_type": "paragraph", "text": txt, "confidence": 0.95}
        text_elements.append((y0, el))
    elements = [el for _, el in text_elements]
    for t in sorted(tables, key=lambda t: t["bbox"][1]):
        if not t["md"]:
            continue
        conf = 0.9 if not t["warnings"] else 0.6
        tel = {"element_type": "table", "text": t["md"], "confidence": conf}
        y1 = t["bbox"][3]
        idx = next((k for k, (yy, _) in enumerate(text_elements) if yy > y1),
                   len(elements))
        elements.insert(idx, tel)
        text_elements.insert(idx, (y1, tel))
    return elements


def elements_to_markdown(elements: list[dict]) -> str:
    parts = []
    for e in elements:
        if e["element_type"] == "heading":
            parts.append("## " + e["text"])
        else:
            parts.append(e["text"])
    return "\n\n".join(p for p in parts if p.strip())


def page_has_images(page) -> bool:
    try:
        return len(page.get_images(full=True)) > 0
    except Exception:
        return False


def ocr_page_text(page, lang: str = "eng") -> tuple[str, float]:
    """OCR one page; return (text, mean word confidence 0..1)."""
    import pytesseract
    from PIL import Image

    tesseract_bin = shutil.which("tesseract")
    if tesseract_bin:
        pytesseract.pytesseract.tesseract_cmd = tesseract_bin
    pix = page.get_pixmap(dpi=OCR_DPI)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    text = pytesseract.image_to_string(img, lang=lang) or ""
    conf = 0.75
    try:
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT)
        confs = [float(c) for c in data.get("conf", []) if float(c) >= 0]
        if confs:
            conf = sum(confs) / len(confs) / 100.0
    except Exception:
        pass
    return text, conf


def extract(pdf_bytes: bytes, min_chars: int, image_page_chars: int,
            ocr_lang: str = "eng") -> dict:
    """Extract markdown. Returns dict with markdown + per-page detail."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise RuntimeError(f"cannot open PDF (corrupt or truncated?): {exc}")
    if doc.needs_pass:
        # Encrypted PDFs must fail LOUDLY: without this, get_text() would
        # return empty strings and we would silently output empty markdown.
        raise RuntimeError(
            "PDF is password-protected (encrypted); cannot extract without "
            "a password — refusing to return empty markdown silently."
        )
    n_pages = len(doc)
    if n_pages == 0:
        raise RuntimeError("PDF has zero pages — nothing to extract.")
    body_size = doc_body_size(doc)
    pages: list[dict] = []
    md_parts: list[str] = []
    all_elements: list[dict] = []
    used_ocr = False
    warnings: list[str] = []
    total_seconds = 0.0

    for i, page in enumerate(doc, start=1):
        t0 = time.perf_counter()
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]
        try:
            dict_blocks = [b for b in page.get_text("dict")["blocks"]
                           if b.get("type") == 0]
        except Exception:
            dict_blocks = []
        native_chars = sum(len(clean_text(b[4])) for b in blocks)
        has_img = page_has_images(page)
        tables = detect_tables(page)
        for t in tables:
            for w in t["warnings"]:
                warnings.append(f"page {i}: table warning — {w}")
        needs_ocr = native_chars < min_chars or (
            has_img and native_chars < image_page_chars
        )
        used = "native"
        ocr_chars = 0
        ocr_conf = 0.0
        if needs_ocr:
            try:
                ocr_raw, ocr_conf = ocr_page_text(page, lang=ocr_lang)
                ocr = clean_text(ocr_raw)
                ocr_chars = len(ocr)
            except Exception as exc:
                warnings.append(
                    f"page {i}: OCR failed ({exc}); kept native text "
                    f"({native_chars} chars)"
                )
                ocr = ""
            # Prefer OCR only when it actually found more text.
            if ocr_chars > native_chars:
                if tables:
                    warnings.append(
                        f"page {i}: {len(tables)} table(s) detected but page "
                        "was OCR-routed — table structure not preserved"
                    )
                elements = [
                    {"element_type": "paragraph", "text": para,
                     "confidence": round(ocr_conf, 2)}
                    for para in ocr.split("\n\n") if para.strip()
                ]
                used = "ocr"
                used_ocr = True
            else:
                elements = build_page_elements(blocks, dict_blocks, tables,
                                               body_size)
                used = "native"
                if native_chars < min_chars:
                    warnings.append(
                        f"page {i}: thin text ({native_chars} native chars, "
                        f"{ocr_chars} OCR chars) — low confidence, "
                        "kept best available text"
                    )
        else:
            elements = build_page_elements(blocks, dict_blocks, tables,
                                           body_size)
        text = elements_to_markdown(elements)
        for e in elements:
            e["page"] = i
            all_elements.append(e)
        secs = time.perf_counter() - t0
        total_seconds += secs
        md_parts.append(text)
        pages.append(
            {
                "page": i,
                "native_chars": native_chars,
                "ocr_chars": ocr_chars,
                "has_images": has_img,
                "tables_detected": len(tables),
                "used": used,
                "chars": len(text),
                "seconds": round(secs, 3),
            }
        )

    markdown = "\n\n".join(p for p in md_parts if p.strip())
    total_chars = sum(p["chars"] for p in pages)
    if n_pages and total_chars / n_pages < min_chars:
        path = "fallback"
        warnings.append(
            f"document average {total_chars / n_pages:.1f} chars/page is below "
            f"the {min_chars} threshold even after OCR — low confidence"
        )
    elif used_ocr:
        path = "ocr"
    else:
        path = "native"
    if path == "fallback":
        for e in all_elements:
            e["confidence"] = min(e["confidence"], 0.5)
    for order, e in enumerate(all_elements):
        e["reading_order"] = order
    return {
        "extraction_path": path,
        "markdown": markdown,
        "elements": all_elements,
        "pages": pages,
        "total_chars": total_chars,
        "warnings": warnings,
        "total_seconds": round(total_seconds, 3),
    }


# --- cache ------------------------------------------------------------------
def cache_key_for(source: str, kind: str, content_hash: str) -> str:
    if kind == "url":
        # FULL URL, never the filename. Two different documents that share a
        # filename (Firecrawl #3968) therefore get different keys.
        return sha256_hex(source.encode("utf-8"))
    # Local files: content hash.
    return content_hash


def cache_paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    return cache_dir / f"{key}.md", cache_dir / f"{key}.json"


def cache_lookup(cache_dir: Path, key: str, source: str,
                 content_hash: str) -> dict | None:
    """Return cached entry only if url + content hash both match."""
    md_path, json_path = cache_paths(cache_dir, key)
    if not (md_path.is_file() and json_path.is_file()):
        return None
    try:
        entry = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if entry.get("source") != source:
        return None
    if entry.get("content_hash") != content_hash:
        return None
    entry["markdown"] = md_path.read_text(encoding="utf-8")
    return entry


def cache_store(cache_dir: Path, key: str, source: str, content_hash: str,
                result: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    md_path, json_path = cache_paths(cache_dir, key)
    entry = {
        "source": source,
        "content_hash": content_hash,
        "extraction_path": result["extraction_path"],
        "elements": result.get("elements", []),
        "pages": result["pages"],
        "total_chars": result["total_chars"],
        "warnings": result["warnings"],
        "total_seconds": result.get("total_seconds"),
        "tool_version": TOOL_VERSION,
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    md_path.write_text(result["markdown"], encoding="utf-8")
    json_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2),
                         encoding="utf-8")


# --- shared pipeline --------------------------------------------------------
def process_source(source: str, cache_dir: Path, no_cache: bool,
                   min_chars: int, image_page_chars: int,
                   ocr_lang: str) -> tuple[dict, str]:
    """Run the whole pipeline for one source.
    Returns (meta, markdown, elements). Raises RuntimeError on failure."""
    pdf_bytes, kind = fetch_source(source)
    content_hash = sha256_hex(pdf_bytes)
    key = cache_key_for(source, kind, content_hash)

    cache_state = "disabled"
    entry = None
    if not no_cache:
        entry = cache_lookup(cache_dir, key, source, content_hash)
        if entry is not None:
            cache_state = "hit"
        else:
            md_path, json_path = cache_paths(cache_dir, key)
            cache_state = ("miss-hash-mismatch" if json_path.is_file()
                           else "miss")

    if entry is not None:
        result = {
            "extraction_path": entry["extraction_path"],
            "markdown": entry["markdown"],
            "elements": entry.get("elements", []),
            "pages": entry["pages"],
            "total_chars": entry["total_chars"],
            "warnings": entry["warnings"],
            "total_seconds": entry.get("total_seconds", 0),
        }
    else:
        try:
            result = extract(pdf_bytes, min_chars, image_page_chars, ocr_lang)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"extraction failed for {source}: {exc}")
        if not no_cache:
            cache_store(cache_dir, key, source, content_hash, result)

    for e in result.get("elements", []):
        e["source"] = {"uri": source, "content_hash": content_hash}

    n_pages = len(result["pages"])
    meta = {
        "tool": "pdf-slice",
        "tool_version": TOOL_VERSION,
        "source": source,
        "source_kind": kind,
        "content_hash": content_hash,
        "cache_state": cache_state,
        "cache_key": key,
        "extraction_path": result["extraction_path"],
        "pages": result["pages"],
        "total_chars": result["total_chars"],
        "chunks": len(result.get("elements", [])),
        "warnings": result["warnings"],
        "timing": {
            "total_seconds": result.get("total_seconds", 0),
            "seconds_per_page_avg": (
                round(result.get("total_seconds", 0) / n_pages, 3)
                if n_pages else 0
            ),
        },
        "thresholds": {
            "min_chars_per_page": min_chars,
            "image_page_chars": image_page_chars,
            "ocr_lang": ocr_lang,
        },
    }
    return meta, result["markdown"], result.get("elements", [])


# --- CLI --------------------------------------------------------------------
def write_outputs(prefix: Path, meta: dict, markdown: str,
                  elements: list, fmt: str) -> list[str]:
    """Write outputs per --format; always writes the .json metadata.
    Returns the list of written paths."""
    wrote: list[str] = []
    if fmt in ("md", "both"):
        Path(f"{prefix}.md").write_text(markdown, encoding="utf-8")
        wrote.append(f"{prefix}.md")
    Path(f"{prefix}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    wrote.append(f"{prefix}.json")
    if fmt in ("jsonl", "both"):
        lines = [json.dumps(e, ensure_ascii=False) for e in elements]
        Path(f"{prefix}.jsonl").write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        wrote.append(f"{prefix}.jsonl")
    return wrote


def cmd_single(args) -> int:
    cache_dir = Path(args.cache_dir)
    try:
        meta, markdown, elements = process_source(
            args.source, cache_dir, args.no_cache, args.min_chars_per_page,
            args.image_page_chars, args.ocr_lang)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.out_prefix:
        prefix = Path(args.out_prefix)
    else:
        if meta["source_kind"] == "url":
            stem = f"url-{meta['cache_key'][:12]}"
        else:
            stem = Path(args.source).stem
        prefix = Path.cwd() / stem
    wrote = write_outputs(prefix, meta, markdown, elements, args.format)

    if args.print:
        print(markdown)
    print(f"path={meta['extraction_path']} cache={meta['cache_state']} "
          f"chars={meta['total_chars']} pages={len(meta['pages'])} "
          f"chunks={meta['chunks']} "
          f"hash={meta['content_hash'][:12]} "
          f"secs={meta['timing']['total_seconds']}", file=sys.stderr)
    for w in meta["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"wrote {', '.join(wrote)}", file=sys.stderr)
    return 0


def cmd_batch(args) -> int:
    input_dir = Path(args.batch)
    if not input_dir.is_dir():
        print(f"ERROR: not a directory: {args.batch}", file=sys.stderr)
        return 2
    patterns = ["*.pdf", "*.PDF"]
    pdfs: list[Path] = []
    for pat in patterns:
        pdfs.extend(input_dir.rglob(pat) if args.recursive
                    else input_dir.glob(pat))
    pdfs = sorted(set(pdfs))
    out_dir = Path(args.out_dir) if args.out_dir else input_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)

    files_ok = 0
    failures: list[dict] = []
    pages_total = 0
    pages_ocr = 0
    chunks_total = 0
    warnings_total = 0
    total_seconds = 0.0
    t_start = time.perf_counter()
    for pdf in pdfs:
        try:
            meta, markdown, elements = process_source(
                str(pdf), cache_dir, args.no_cache, args.min_chars_per_page,
                args.image_page_chars, args.ocr_lang)
        except RuntimeError as exc:
            failures.append({"file": str(pdf), "error": str(exc)})
            print(f"FAILED: {pdf.name}: {exc}", file=sys.stderr)
            continue
        write_outputs(out_dir / pdf.stem, meta, markdown, elements,
                      args.format)
        files_ok += 1
        pages_total += len(meta["pages"])
        pages_ocr += sum(1 for p in meta["pages"] if p["used"] == "ocr")
        chunks_total += meta["chunks"]
        warnings_total += len(meta["warnings"])
        total_seconds += meta["timing"]["total_seconds"]
    wall = round(time.perf_counter() - t_start, 2)
    summary = {
        "files_total": len(pdfs),
        "files_ok": files_ok,
        "files_failed": len(failures),
        "failures": failures,
        "pages_total": pages_total,
        "pages_ocr": pages_ocr,
        "chunks_total": chunks_total,
        "warnings_total": warnings_total,
        "extraction_seconds_total": round(total_seconds, 2),
        "wall_seconds": wall,
        "out_dir": str(out_dir),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        print(f"BATCH DONE with {len(failures)} failure(s) — see summary.json",
              file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", nargs="?",
                    help="local PDF path or http(s) URL (single mode)")
    ap.add_argument("--batch", default=None,
                    help="process every PDF in this directory")
    ap.add_argument("--out-dir", default=None,
                    help="batch output directory (default: <dir>/out)")
    ap.add_argument("--recursive", action="store_true",
                    help="batch: recurse into subdirectories")
    ap.add_argument("--out-prefix", default=None,
                    help="prefix for .md/.json outputs (single mode)")
    ap.add_argument("--print", action="store_true",
                    help="also print markdown to stdout (single mode)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    ap.add_argument("--min-chars-per-page", type=int,
                    default=DEFAULT_MIN_CHARS_PER_PAGE)
    ap.add_argument("--image-page-chars", type=int,
                    default=DEFAULT_IMAGE_PAGE_CHARS)
    ap.add_argument("--ocr-lang", default="eng",
                    help="tesseract language(s), e.g. eng or eng+fas")
    ap.add_argument("--format", default="md", choices=["md", "jsonl", "both"],
                    help="output format: markdown (+metadata JSON), JSONL "
                    "RAG chunks with provenance, or both")
    args = ap.parse_args(argv)

    if shutil.which("tesseract") is None:
        # Loud startup warning: without the tesseract binary the OCR
        # fallback is disabled and scanned/thin pages will come back
        # low-confidence. Never let this fail silently.
        print("WARNING: tesseract binary not found — OCR fallback is "
              "disabled. Install it (sudo apt-get install tesseract-ocr "
              "tesseract-ocr-fas); scanned or thin pages will otherwise "
              "return low-confidence output.", file=sys.stderr)

    if args.batch:
        return cmd_batch(args)
    if not args.source:
        ap.error("a PDF file/URL or --batch is required")
    return cmd_single(args)


if __name__ == "__main__":
    raise SystemExit(main())
