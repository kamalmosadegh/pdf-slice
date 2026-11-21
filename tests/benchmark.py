#!/usr/bin/env python3
"""Self-benchmark for the pdf-slice tool.

Run:  python tests/benchmark.py   (any active virtualenv)
Runs the CLI (--no-cache) over the fixtures and reports, per file:
wall time, pages, seconds/page, and word-recovery % against the known
reference text of each fixture. Writes tests/benchmark_results.json.
Exit 0 only if every recovery threshold is met.
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
EXTRACT = PROJECT / "extract.py"
# Use the interpreter that is running this script, wherever it lives —
# never assume the virtualenv directory is literally named "venv".
VENV_PY = Path(sys.executable)
FIX = HERE / "fixtures"
REFS = json.loads((HERE / "references.json").read_text(encoding="utf-8"))

# recovery thresholds (documented; do not lower to hide a regression)
THRESHOLDS = {
    "scanned": 0.85,
    "white": 0.90,
    "multilingual": 0.90,
    "table": 0.95,
}

TARGETS = {
    "scanned": "scanned_words",
    "white": "white_body",
    "multilingual": "multi_en",
    "table": "table_cells",
}


def words_of(ref):
    out = set()
    for item in ref:
        out.update(re.findall(r"[a-z0-9$./]+", str(item).lower()))
    return out


def main():
    results = {}
    ok_all = True
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name in ["white", "black", "scanned", "multilingual",
                     "table", "columns", "large"]:
            t0 = time.perf_counter()
            r = subprocess.run(
                [str(VENV_PY), str(EXTRACT), str(FIX / f"{name}.pdf"),
                 "--no-cache", "--out-prefix", tmp / name],
                capture_output=True, text=True, timeout=600)
            wall = time.perf_counter() - t0
            if r.returncode != 0:
                print(f"{name:14s} CLI FAILED: {r.stderr[:200]}")
                ok_all = False
                continue
            meta = json.loads((tmp / f"{name}.json").read_text())
            md = (tmp / f"{name}.md").read_text()
            n_pages = len(meta["pages"])
            extract_secs = meta["timing"]["total_seconds"]
            entry = {
                "wall_seconds": round(wall, 2),
                "extraction_seconds": extract_secs,
                "pages": n_pages,
                # Extraction-only time per page (excludes interpreter
                # startup + imports, which dominate wall time on 1-pagers).
                "seconds_per_page": round(extract_secs / n_pages, 3) if n_pages else 0,
                "extraction_path": meta["extraction_path"],
                "total_chars": meta["total_chars"],
                "recovery": None,
            }
            if name in TARGETS:
                want = words_of(REFS[TARGETS[name]])
                got = set(re.findall(r"[a-z0-9$./]+", md.lower()))
                rec = len(got & want) / len(want) if want else 1.0
                entry["recovery"] = round(rec, 3)
                entry["threshold"] = THRESHOLDS[name]
                if rec < THRESHOLDS[name]:
                    ok_all = False
            entry["warnings"] = len(meta["warnings"])
            # Silent error = expected content missing AND no warning raised.
            entry["silent_error"] = 0
            if (name in TARGETS and entry["recovery"] is not None
                    and entry["recovery"] < THRESHOLDS[name]
                    and entry["warnings"] == 0):
                entry["silent_error"] = 1
            results[name] = entry
            rec = entry["recovery"]
            print(f"{name:14s} {entry['wall_seconds']:6.2f}s  "
                  f"{n_pages:3d} pages  {entry['seconds_per_page']:5.3f}s/pg  "
                  f"path={entry['extraction_path']:8s} "
                  f"recovery={rec if rec is None else f'{rec:.0%}'}")
    (HERE / "benchmark_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    total_silent = sum(e.get("silent_error", 0) for e in results.values())
    if total_silent > 0:
        ok_all = False
    # Write the public benchmark table into README_FA.md.
    lines = [
        "| فایل | مسیر | بازیابی کلمات | ثانیه/صفحه | خطای بی‌صدا |",
        "|---|---|---|---|---|",
    ]
    for name, e in results.items():
        rec = e["recovery"]
        lines.append(f"| {name} | {e['extraction_path']} | "
                     f"{rec if rec is None else f'{rec:.0%}'} | "
                     f"{e['seconds_per_page']} | {e.get('silent_error', 0)} |")
    lines.append("")
    lines.append(f"**مجموع خطاهای بی‌صدا: {total_silent}** — خطای بی‌صدا یعنی "
                 "محتوای اشتباه یا ناقص بدون هیچ هشدار. هدف این ابزار صفر است.")
    lines.append("")
    lines.append("تولیدشده با `tests/benchmark.py`؛ فیکسچرها در `tests/fixtures/`.")
    table = "\n".join(lines)
    readme = PROJECT / "README_FA.md"
    text = readme.read_text(encoding="utf-8")
    start = "<!-- BENCHMARK:START -->"
    end = "<!-- BENCHMARK:END -->"
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        readme.write_text(
            before + start + "\n" + table + "\n" + end + after,
            encoding="utf-8")
        print("README benchmark table updated")
    print("BENCHMARK GREEN" if ok_all else "BENCHMARK FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
