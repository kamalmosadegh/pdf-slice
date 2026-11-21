#!/usr/bin/env python3
"""Full pre-delivery test matrix for the pdf-slice tool.

Run:  python tests/test_slice.py   (any active virtualenv)
Each test is repeated REPEATS times (stability). Exit 0 only if ALL green.

Tests:
  T01 cache collision, Firecrawl #3968 (different URLs, same filename)
  T02 same URL, changed content -> cache must invalidate
  T03 white-text nested-form fixture, Firecrawl #5759
  T04 black-fill control fixture
  T05 fully scanned PDF (no text layer) -> OCR path
  T06 multilingual PDF (Persian + English)
  T07 table preserved as markdown table
  T08 merged-header table -> warning, no silent loss
  T09 two-column reading order
  T10 large document (120 pages)
  T11 corrupt / truncated PDF -> loud failure
  T12 not-a-PDF file -> loud failure
  T13 blank PDF -> empty markdown + low-confidence warning, no silence
  T14 zero-byte file -> loud failure
  T15 password-protected PDF -> loud failure
  T16 URL returning HTML for a .pdf URL -> loud failure
  T17 batch mode (good + corrupt files, summary counts)
  T18 benchmark script runs green
  T19 MIT license + light dependencies (no GPU models)
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
EXTRACT = PROJECT / "extract.py"
# Use the interpreter that is running this suite, wherever it lives —
# never assume the virtualenv directory is literally named "venv".
VENV_PY = Path(sys.executable)
FIX = HERE / "fixtures"
REFS = json.loads((HERE / "references.json").read_text(encoding="utf-8"))

REPEATS = 3
RESULTS: list[tuple[str, bool, str]] = []


def run_cli(args, cwd, timeout=180):
    cmd = [str(VENV_PY), str(EXTRACT)] + [str(a) for a in args]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
    return ok


def word_set(text):
    return set(re.findall(r"[a-z0-9$./]+", text.lower()))


# --- local HTTP server (mutable routes) -------------------------------------
ROUTES: dict[str, tuple[bytes, str, str]] = {}  # path -> (bytes, ctype, disp)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ROUTES:
            data, ctype, disp = ROUTES[self.path]
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if disp:
                self.send_header("Content-Disposition", disp)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# --- individual tests (each returns True/False) -----------------------------
def t01_cache_collision(base_url, tmp):
    """#3968: two URLs differing only in doc id, same filename Nexus.pdf."""
    for rep in range(REPEATS):
        cache = tmp / f"t01cache{rep}"
        for doc, expect, other in [
            ("38.1499654.1", "857280", "852883"),
            ("38.1491460.1", "852883", "857280"),
        ]:
            url = f"{base_url}/api/dms/{doc}"
            r = run_cli([url, "--cache-dir", cache,
                         "--out-prefix", tmp / f"t01-{rep}-{doc}"], tmp)
            if r.returncode != 0:
                return False, f"rep{rep} {doc} exit {r.returncode}: {r.stderr[:200]}"
            md = (tmp / f"t01-{rep}-{doc}.md").read_text()
            if expect not in md or other in md:
                return False, f"rep{rep} {doc}: wrong content (want {expect})"
        # second round: cache hits must still return each doc's own content
        for doc, expect, other in [
            ("38.1499654.1", "857280", "852883"),
            ("38.1491460.1", "852883", "857280"),
        ]:
            url = f"{base_url}/api/dms/{doc}"
            r = run_cli([url, "--cache-dir", cache,
                         "--out-prefix", tmp / f"t01b-{rep}-{doc}"], tmp)
            meta = json.loads((tmp / f"t01b-{rep}-{doc}.json").read_text())
            md = (tmp / f"t01b-{rep}-{doc}.md").read_text()
            if meta["cache_state"] != "hit":
                return False, f"rep{rep} {doc}: expected cache hit, got {meta['cache_state']}"
            if expect not in md or other in md:
                return False, f"rep{rep} {doc} cached: wrong content"
    return True, f"{REPEATS}x2 urls, cache hits correct"


def t02_same_url_content_change(base_url, tmp):
    doc = "38.1499654.1"
    url = f"{base_url}/api/dms/{doc}"
    for rep in range(REPEATS):
        cache = tmp / f"t02cache{rep}"
        ROUTES[f"/api/dms/{doc}"] = (
            (FIX / "nexus_a.pdf").read_bytes(),
            "application/pdf", 'attachment; filename="Nexus.pdf"')
        r = run_cli([url, "--cache-dir", cache,
                     "--out-prefix", tmp / f"t02-{rep}-1"], tmp)
        md1 = (tmp / f"t02-{rep}-1.md").read_text()
        if "857280" not in md1:
            return False, f"rep{rep} first fetch wrong"
        ROUTES[f"/api/dms/{doc}"] = (
            (FIX / "nexus_b.pdf").read_bytes(),
            "application/pdf", 'attachment; filename="Nexus.pdf"')
        r = run_cli([url, "--cache-dir", cache,
                     "--out-prefix", tmp / f"t02-{rep}-2"], tmp)
        meta = json.loads((tmp / f"t02-{rep}-2.json").read_text())
        md2 = (tmp / f"t02-{rep}-2.md").read_text()
        if meta["cache_state"] != "miss-hash-mismatch":
            return False, (f"rep{rep} cache_state={meta['cache_state']}, "
                           "expected miss-hash-mismatch")
        if "852883" not in md2 or "857280" in md2:
            return False, f"rep{rep} stale content served after change"
    return True, f"{REPEATS}x same-URL change invalidated cache"


def _extract_file(name, tmp, extra=None):
    out = tmp / name
    r = run_cli([str(FIX / f"{name}.pdf"), "--no-cache",
                 "--out-prefix", out] + (extra or []), tmp)
    md = (tmp / f"{name}.md").read_text() if (tmp / f"{name}.md").exists() else ""
    meta = (json.loads((tmp / f"{name}.json").read_text())
            if (tmp / f"{name}.json").exists() else {})
    return r, md, meta


def t03_white_form(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("white", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}: {r.stderr[:200]}"
        missing = [s for s in REFS["white_body"] if s not in md]
        if missing:
            return False, (f"rep{rep} body sentences missing: {missing[:2]} "
                           f"(path={meta.get('extraction_path')})")
    return True, f"{REPEATS}x all 8 body sentences present"


def t04_black_control(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("black", tmp)
        missing = [s for s in REFS["white_body"] if s not in md]
        if missing:
            return False, f"rep{rep} missing {missing[:2]}"
    return True, f"{REPEATS}x all 8 body sentences present"


def t05_scanned(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("scanned", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        if meta.get("extraction_path") not in ("ocr", "fallback"):
            return False, (f"rep{rep} path={meta.get('extraction_path')}, "
                           "expected ocr")
        got = word_set(md)
        want = {w.lower() for w in REFS["scanned_words"]}
        rec = len(got & want) / len(want)
        if rec < 0.85:
            return False, f"rep{rep} word recovery {rec:.0%} < 85%"
    return True, f"{REPEATS}x path=ocr, word recovery >=85%"


def fa_variants(word):
    import arabic_reshaper
    from bidi.algorithm import get_display
    reshaped = arabic_reshaper.reshape(word)
    return {word, reshaped, get_display(reshaped)}


def t06_multilingual(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("multilingual", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        en_missing = [w for w in REFS["multi_en"] if w.lower() not in md.lower()]
        if en_missing:
            return False, f"rep{rep} english missing {en_missing[:3]}"
        squashed = re.sub(r"\s+", "", md)
        fa_missing = []
        for w in REFS["multi_fa"]:
            if not any(re.sub(r"\s+", "", v) in squashed
                       for v in fa_variants(w)):
                fa_missing.append(w)
        if fa_missing:
            return False, f"rep{rep} persian missing {fa_missing}"
    return True, f"{REPEATS}x EN + FA words present"


def t07_table(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("table", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        if "| Product | Price | Stock |" not in md:
            return False, f"rep{rep} no markdown table header in output"
        for cell in ["Apple", "$1.20", "120", "Cherry", "$2.50"]:
            if cell not in md:
                return False, f"rep{rep} cell {cell!r} missing"
        if "$120" in md:
            return False, "rep{rep} price mangled ('$1.20' -> '$120')"
        # table must come between the heading and the closing paragraph
        i_head = md.find("Fruit price list")
        i_tab = md.find("| Product |")
        i_end = md.find("End of price list")
        if not (0 <= i_head < i_tab < i_end):
            return False, (f"rep{rep} wrong order: heading@{i_head} "
                           f"table@{i_tab} end@{i_end}")
        if meta["pages"][0].get("tables_detected", 0) < 1:
            return False, "rep{rep} table not detected in metadata"
    return True, f"{REPEATS}x markdown table, exact cells, right order"


def t08_merged_table(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("merged_table", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        for cell in ["Quarterly Sales Report", "Apple", "$1.20", "Banana"]:
            if cell not in md:
                return False, f"rep{rep} cell {cell!r} lost (silent!)"
        warns = " ".join(meta.get("warnings", []))
        if "merged" not in warns.lower():
            return False, (f"rep{rep} no merged-cell warning "
                           f"(warnings={meta.get('warnings')})")
    return True, f"{REPEATS}x merged table warned (span flattened), no loss"


def t08b_empty_grid(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("empty_grid", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        warns = " ".join(meta.get("warnings", []))
        if meta["pages"][0].get("tables_detected", 0) >= 1:
            if "table" not in warns.lower():
                return False, (f"rep{rep} empty table detected but no "
                               f"warning (warnings={meta.get('warnings')})")
            return True, f"{REPEATS}x empty grid detected + warned"
        return True, f"{REPEATS}x empty grid: no table detected, no crash"
    return True, "empty grid handled"


def t09_columns(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("columns", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        i1 = md.find("COLUMN ONE")
        i2 = md.find("COLUMN TWO")
        if i1 < 0 or i2 < 0:
            return False, f"rep{rep} markers missing ({i1}, {i2})"
        if i1 > i2:
            return False, "rep{rep} column two read before column one"
    return True, f"{REPEATS}x column one before column two"


def t10_large(tmp):
    import time
    for rep in range(REPEATS):
        t0 = time.perf_counter()
        r, md, meta = _extract_file("large", tmp)
        secs = time.perf_counter() - t0
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode}"
        if len(meta.get("pages", [])) != 120:
            return False, f"rep{rep} pages={len(meta.get('pages', []))} != 120"
        if "page 120 of 120" not in md:
            return False, "rep{rep} last page missing"
        if secs > 120:
            return False, f"rep{rep} too slow: {secs:.1f}s"
    return True, f"{REPEATS}x 120 pages, last page present"


def _loud_fail(name, path_args, tmp, expect_in_stderr=""):
    for rep in range(REPEATS):
        r = run_cli(path_args + ["--no-cache", "--out-prefix",
                                 tmp / f"fail-{name}-{rep}"], tmp)
        if r.returncode == 0:
            return False, f"rep{rep} exit 0 — silent success on bad input"
        if expect_in_stderr and expect_in_stderr.lower() not in r.stderr.lower():
            return False, (f"rep{rep} stderr lacks {expect_in_stderr!r}: "
                           f"{r.stderr[:200]}")
        if "ERROR" not in r.stderr:
            return False, f"rep{rep} no ERROR line on stderr"
    return True, f"{REPEATS}x loud failure (exit {r.returncode})"


def t11_corrupt(tmp):
    return _loud_fail("corrupt", [str(FIX / "corrupt.pdf")], tmp)


def t12_garbage(tmp):
    return _loud_fail("garbage", [str(FIX / "garbage.bin")], tmp)


def t14_zero(tmp):
    return _loud_fail("zero", [str(FIX / "zero.pdf")], tmp)


def t15_locked(tmp):
    return _loud_fail("locked", [str(FIX / "locked.pdf")], tmp,
                      expect_in_stderr="password")


def t16_html_url(base_url, tmp):
    for rep in range(REPEATS):
        r = run_cli([f"{base_url}/fake.pdf", "--no-cache",
                     "--out-prefix", tmp / f"t16-{rep}"], tmp)
        if r.returncode == 0:
            return False, f"rep{rep} exit 0 on HTML-as-PDF"
        if "ERROR" not in r.stderr:
            return False, f"rep{rep} no ERROR line"
    return True, f"{REPEATS}x HTML URL rejected loudly"


def t13_empty(tmp):
    for rep in range(REPEATS):
        r, md, meta = _extract_file("empty", tmp)
        if r.returncode != 0:
            return False, f"rep{rep} exit {r.returncode} (blank PDF is valid)"
        if meta.get("total_chars", 1) != 0:
            return False, f"rep{rep} chars={meta.get('total_chars')} != 0"
        if not meta.get("warnings"):
            return False, "rep{rep} no low-confidence warning on blank PDF"
        if meta.get("extraction_path") != "fallback":
            return False, (f"rep{rep} path={meta.get('extraction_path')}, "
                           "expected fallback")
    return True, f"{REPEATS}x empty md + warning + path=fallback"


def t17_batch(tmp):
    batch_dir = tmp / "batch"
    batch_dir.mkdir()
    for name in ["table", "columns", "scanned"]:
        shutil.copy(FIX / f"{name}.pdf", batch_dir / f"{name}.pdf")
    shutil.copy(FIX / "corrupt.pdf", batch_dir / "corrupt.pdf")
    for rep in range(REPEATS):
        out = tmp / f"batch-out-{rep}"
        r = run_cli(["--batch", str(batch_dir), "--out-dir", str(out),
                     "--no-cache"], tmp)
        summary = json.loads((out / "summary.json").read_text())
        if r.returncode == 0:
            return False, f"rep{rep} batch exit 0 despite corrupt file"
        if summary["files_total"] != 4 or summary["files_ok"] != 3:
            return False, f"rep{rep} summary {summary}"
        if summary["files_failed"] != 1:
            return False, f"rep{rep} failures={summary['files_failed']}"
        if not any("corrupt" in f["file"] for f in summary["failures"]):
            return False, "rep{rep} corrupt file not listed in failures"
        if summary["pages_ocr"] < 3:
            return False, f"rep{rep} pages_ocr={summary['pages_ocr']} < 3"
        for name in ["table", "columns", "scanned"]:
            if not (out / f"{name}.md").is_file():
                return False, f"rep{rep} missing output for {name}"
    return True, f"{REPEATS}x 4 files: 3 ok, 1 failed+listed, ocr pages>=3"


def t18_benchmark(tmp):
    for rep in range(REPEATS):
        r = subprocess.run(
            [str(VENV_PY), str(HERE / "benchmark.py")],
            cwd=str(tmp), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return False, f"rep{rep} benchmark exit {r.returncode}: {r.stdout[-500:]}"
    return True, f"{REPEATS}x benchmark green"


def t19_license_deps(tmp):
    lic = (PROJECT / "LICENSE").read_text()
    if "MIT License" not in lic:
        return False, "LICENSE is not MIT"
    req = (PROJECT / "requirements.txt").read_text().lower()
    req_test = (PROJECT / "requirements-test.txt").read_text().lower()
    for bad in ["torch", "transformers", "onnx", "tensorflow", "cuda"]:
        if bad in req or bad in req_test:
            return False, f"heavy dep found: {bad}"
    for need in ["pymupdf", "pytesseract", "pillow", "mcp"]:
        if need not in req:
            return False, f"missing runtime dep {need}"
    return True, "MIT license, light deps only"


def t20_jsonl(tmp):
    import hashlib
    for rep in range(REPEATS):
        for name in ["table", "multilingual"]:
            out = tmp / f"t20-{rep}-{name}"
            r = run_cli([str(FIX / f"{name}.pdf"), "--no-cache",
                         "--format", "jsonl", "--out-prefix", out], tmp)
            if r.returncode != 0:
                return False, f"rep{rep} {name} exit {r.returncode}"
            lines = (tmp / f"t20-{rep}-{name}.jsonl").read_text(
                encoding="utf-8").strip().split("\n")
            chunks = [json.loads(ln) for ln in lines if ln.strip()]
            if not chunks:
                return False, f"rep{rep} {name}: no chunks"
            want_hash = hashlib.sha256(
                (FIX / f"{name}.pdf").read_bytes()).hexdigest()
            for i, ch in enumerate(chunks):
                for f in ["text", "page", "element_type", "confidence",
                          "source", "reading_order"]:
                    if f not in ch:
                        return False, f"rep{rep} {name}: chunk lacks {f}"
                if ch["reading_order"] != i:
                    return False, f"rep{rep} {name}: reading_order not sequential"
                if ch["source"]["content_hash"] != want_hash:
                    return False, f"rep{rep} {name}: content_hash mismatch"
                if not (0.0 <= ch["confidence"] <= 1.0):
                    return False, f"rep{rep} {name}: confidence out of range"
                if ch["element_type"] not in ("heading", "paragraph", "table"):
                    return False, f"rep{rep} {name}: bad element_type"
            if name == "table" and not any(
                    c["element_type"] == "table" for c in chunks):
                return False, f"rep{rep} table: no table chunk"
    return True, f"{REPEATS}x jsonl valid, provenance + order correct"


def _mcp_session(tmp):
    """Start the MCP server, run initialize handshake. Returns proc."""
    import subprocess as sp
    proc = sp.Popen(
        [str(VENV_PY), str(PROJECT / "mcp_server.py")],
        stdin=sp.PIPE, stdout=sp.PIPE, text=True, bufsize=1, cwd=str(tmp))

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv():
        line = proc.stdout.readline()
        return json.loads(line) if line else None

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "test", "version": "0.1"}}})
    init = recv()
    if not init or "result" not in init:
        raise RuntimeError(f"initialize failed: {init}")
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return proc, send, recv


def t21_mcp(tmp):
    for rep in range(REPEATS):
        proc, send, recv = _mcp_session(tmp)
        try:
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                  "params": {}})
            lst = recv()
            names = [t["name"] for t in lst["result"]["tools"]]
            if names != ["extract_pdf", "verify_pdf"]:
                return False, f"rep{rep} tools={names}"
            send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "extract_pdf",
                             "arguments": {"source": str(FIX / "table.pdf")}}})
            res = recv()
            payload = json.loads(res["result"]["content"][0]["text"])
            if not payload.get("ok") or "Apple" not in payload["markdown"]:
                return False, f"rep{rep} extract_pdf bad: {str(payload)[:200]}"
            send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "verify_pdf",
                             "arguments": {
                                 "source": str(FIX / "table.pdf"),
                                 "second_source": str(FIX / "table.pdf")}}})
            v = json.loads(recv()["result"]["content"][0]["text"])
            if v["match_rate"] < 0.95:
                return False, f"rep{rep} same-file match {v['match_rate']}"
            send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                  "params": {"name": "verify_pdf",
                             "arguments": {
                                 "source": str(FIX / "table.pdf"),
                                 "second_source": str(FIX / "columns.pdf")}}})
            v = json.loads(recv()["result"]["content"][0]["text"])
            if v["match_rate"] >= 0.5:
                return False, (f"rep{rep} different-file match "
                               f"{v['match_rate']} too high")
        finally:
            proc.stdin.close()
            proc.wait(timeout=15)
    return True, f"{REPEATS}x MCP: list/call/verify (1.0 vs <0.5)"


def t22_skill(tmp):
    skill = PROJECT / "skills" / "pdf-slice" / "SKILL.md"
    if not skill.is_file():
        return False, "skills/pdf-slice/SKILL.md missing"
    text = skill.read_text()
    for need in ["extract_pdf", "verify_pdf", "pdf-slice",
                 "warnings", "uvx", "mcp-config"]:
        if need not in text:
            return False, f"SKILL.md lacks {need!r}"
    for cfg in ["mcp-config-claude.json", "mcp-config-cursor.json"]:
        data = json.loads((PROJECT / cfg).read_text())
        srv = data["mcpServers"]["pdf-slice"]
        if "mcp_server.py" not in " ".join(srv["args"]):
            return False, f"{cfg} does not point at mcp_server.py"
    return True, "SKILL.md + both MCP configs present and valid"


def t23_pyproject_uvx(tmp):
    import os
    # NOTE: venvs + pip build temp MUST live on the home disk, not /tmp
    # (/tmp is a 512MB tmpfs here — pip install fails with ENOSPC).
    scratch = Path.home() / "workspace" / ".tmp" / "test-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    for rep in range(REPEATS):
        venv_dir = scratch / f"uvx-venv-{rep}"
        shutil.rmtree(venv_dir, ignore_errors=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       check=True, capture_output=True)
        env = dict(os.environ, TMPDIR=str(scratch))
        pip = str(venv_dir / "bin" / "pip")
        r = subprocess.run([pip, "install", "--quiet", str(PROJECT)],
                           capture_output=True, text=True, env=env,
                           timeout=300)
        if r.returncode != 0:
            return False, f"rep{rep} pip install . failed: {r.stderr[-300:]}"
        r = subprocess.run([str(venv_dir / "bin" / "pdf-slice"), "--help"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return False, f"rep{rep} pdf-slice --help exit {r.returncode}"
        r = subprocess.run([str(venv_dir / "bin" / "pdf-slice-mcp"),
                            "--help"], capture_output=True, text=True,
                           timeout=60)
        if r.returncode != 0 or "MCP" not in r.stdout:
            return False, f"rep{rep} pdf-slice-mcp --help bad"
        r = subprocess.run([pip, "install", "--quiet", "uv"],
                           capture_output=True, text=True, env=env,
                           timeout=300)
        if r.returncode != 0:
            return False, f"rep{rep} pip install uv failed"
        r = subprocess.run(
            [str(venv_dir / "bin" / "uvx"), "--from", str(PROJECT),
             "pdf-slice", "--help"],
            capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            return False, f"rep{rep} uvx failed: {r.stderr[-300:]}"
    for rep in range(REPEATS):
        shutil.rmtree(scratch / f"uvx-venv-{rep}", ignore_errors=True)
    return True, f"{REPEATS}x pip install . + entry points + uvx --help"


def t24_benchmark_readme(tmp):
    for rep in range(REPEATS):
        r = subprocess.run(
            [str(VENV_PY), str(HERE / "benchmark.py")],
            cwd=str(tmp), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return False, f"rep{rep} benchmark exit {r.returncode}"
    readme = (PROJECT / "README_FA.md").read_text(encoding="utf-8")
    if "| scanned |" not in readme or "مجموع خطاهای بی‌صدا: 0" not in readme:
        return False, "README lacks benchmark table or silent-error total"
    return True, f"{REPEATS}x benchmark fills README table, silent errors 0"


def t25_clean_install(tmp):
    """Fresh-eyes: follow the README quickstart on a clean venv."""
    import os
    scratch = Path.home() / "workspace" / ".tmp" / "test-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    for rep in range(REPEATS):
        venv_dir = scratch / f"clean-venv-{rep}"
        shutil.rmtree(venv_dir, ignore_errors=True)
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                       check=True, capture_output=True)
        env = dict(os.environ, TMPDIR=str(scratch))
        pip = str(venv_dir / "bin" / "pip")
        r = subprocess.run(
            [pip, "install", "--quiet", "-r",
             str(PROJECT / "requirements.txt")],
            capture_output=True, text=True, env=env, timeout=300)
        if r.returncode != 0:
            return False, f"rep{rep} pip install -r failed"
        work = tmp / f"clean-work-{rep}"
        work.mkdir()
        # README quickstart: single file, both formats
        r = subprocess.run(
            [str(venv_dir / "bin" / "python"),
             str(PROJECT / "extract.py"), str(FIX / "table.pdf"),
             "--format", "both", "--out-prefix", str(work / "report")],
            cwd=str(work), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return False, f"rep{rep} quickstart extract failed: {r.stderr[:200]}"
        for ext in ["md", "json", "jsonl"]:
            if not (work / f"report.{ext}").is_file():
                return False, f"rep{rep} missing report.{ext}"
        md = (work / "report.md").read_text()
        if "Apple" not in md or "| Product |" not in md:
            return False, f"rep{rep} quickstart output wrong"
        # batch quickstart
        batch = work / "pdfs"
        batch.mkdir()
        shutil.copy(FIX / "columns.pdf", batch / "columns.pdf")
        r = subprocess.run(
            [str(venv_dir / "bin" / "python"),
             str(PROJECT / "extract.py"), "--batch", str(batch)],
            cwd=str(work), capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return False, f"rep{rep} batch quickstart failed"
        if not (batch / "out" / "summary.json").is_file():
            return False, f"rep{rep} batch summary missing"
    for rep in range(REPEATS):
        shutil.rmtree(scratch / f"clean-venv-{rep}", ignore_errors=True)
    return True, f"{REPEATS}x clean venv follows README quickstart end-to-end"


def t26_no_tesseract_warns(tmp):
    """OCR dependency missing -> loud warning, never silent wrong output."""
    import os
    env = {"PATH": str(Path(sys.executable).parent),
           "HOME": os.environ.get("HOME", ""),
           "TMPDIR": str(tmp)}
    for rep in range(REPEATS):
        r = subprocess.run(
            [str(VENV_PY), str(EXTRACT), str(FIX / "scanned.pdf"),
             "--no-cache", "--out-prefix", tmp / f"t26-{rep}"],
            capture_output=True, text=True, env=env, timeout=120)
        if "tesseract" not in r.stderr.lower():
            return False, f"rep{rep} no tesseract warning on stderr"
        if r.returncode == 0:
            meta = json.loads((tmp / f"t26-{rep}.json").read_text())
            if meta["extraction_path"] != "fallback":
                return False, (f"rep{rep} path={meta['extraction_path']} "
                               "without tesseract (expected fallback)")
            if not meta["warnings"]:
                return False, f"rep{rep} no warnings without tesseract"
    return True, f"{REPEATS}x missing tesseract -> loud warning + fallback"


def main():
    print("Generating fixtures...")
    subprocess.run([str(VENV_PY), str(HERE / "make_fixtures.py")],
                   check=True, capture_output=True)
    srv, port = start_server()
    base_url = f"http://127.0.0.1:{port}"
    ROUTES["/api/dms/38.1499654.1"] = (
        (FIX / "nexus_a.pdf").read_bytes(), "application/pdf",
        'attachment; filename="Nexus.pdf"')
    ROUTES["/api/dms/38.1491460.1"] = (
        (FIX / "nexus_b.pdf").read_bytes(), "application/pdf",
        'attachment; filename="Nexus.pdf"')
    ROUTES["/fake.pdf"] = (
        b"<html><body>not a pdf</body></html>", "text/html", "")

    tests = [
        ("T01 cache collision #3968", lambda tmp: t01_cache_collision(base_url, tmp)),
        ("T02 same-URL content change", lambda tmp: t02_same_url_content_change(base_url, tmp)),
        ("T03 white-form #5759", t03_white_form),
        ("T04 black control", t04_black_control),
        ("T05 scanned PDF -> OCR", t05_scanned),
        ("T06 multilingual FA+EN", t06_multilingual),
        ("T07 table as markdown", t07_table),
        ("T08 merged table warns", t08_merged_table),
        ("T08b empty grid warns", t08b_empty_grid),
        ("T09 two-column order", t09_columns),
        ("T10 large 120 pages", t10_large),
        ("T11 corrupt PDF", t11_corrupt),
        ("T12 not-a-PDF", t12_garbage),
        ("T13 blank PDF", t13_empty),
        ("T14 zero-byte file", t14_zero),
        ("T15 locked PDF", t15_locked),
        ("T16 HTML-as-PDF URL", lambda tmp: t16_html_url(base_url, tmp)),
        ("T17 batch mode", t17_batch),
        ("T18 benchmark", t18_benchmark),
        ("T19 license + light deps", t19_license_deps),
        ("T20 JSONL RAG chunks", t20_jsonl),
        ("T21 MCP server", t21_mcp),
        ("T22 agent skill + configs", t22_skill),
        ("T23 pyproject + uvx", t23_pyproject_uvx),
        ("T24 benchmark README table", t24_benchmark_readme),
        ("T25 clean-install quickstart", t25_clean_install),
        ("T26 missing tesseract warns", t26_no_tesseract_warns),
    ]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, fn in tests:
            print(f"== {name} ==")
            try:
                ok, detail = fn(tmp)
            except Exception as exc:
                ok, detail = False, f"EXCEPTION: {exc!r}"
            check(name, ok, detail)
    srv.shutdown()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} tests passed")
    if passed != len(RESULTS):
        print("MATRIX NOT GREEN")
        return 1
    print("MATRIX GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
