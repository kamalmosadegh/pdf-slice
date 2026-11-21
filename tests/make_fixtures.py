#!/usr/bin/env python3
"""Generate all test fixtures for the pdf-slice test matrix.

Run:  python tests/make_fixtures.py
Output: tests/fixtures/*.pdf  +  tests/references.json (expected words)
"""
import glob
import json
import os
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

REFERENCES: dict = {}

DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
NASKH = glob.glob("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf")
NASKH = NASKH[0] if NASKH else None


def save_references():
    (HERE / "references.json").write_text(
        json.dumps(REFERENCES, ensure_ascii=False, indent=2),
        encoding="utf-8")


# --- 1. Nexus pair: two different reports, same served filename -----------
def nexus_pair():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    for tag, report, month in [("a", "857280", "04/01/2026 -> 04/30/2026"),
                               ("b", "852883", "03/01/2026 -> 03/31/2026")]:
        p = FIX / f"nexus_{tag}.pdf"
        c = canvas.Canvas(str(p), pagesize=letter)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 720, f"DMR Report - {report}")
        c.setFont("Helvetica", 12)
        c.drawString(72, 690, month)
        c.drawString(72, 660, f"Facility discharge monitoring report {report}.")
        c.drawString(72, 630, "All values below the permitted limit.")
        c.save()
    REFERENCES["nexus_a"] = ["857280", "04/01/2026", "04/30/2026"]
    REFERENCES["nexus_b"] = ["852883", "03/01/2026", "03/31/2026"]


# --- 2. White/black nested-form fixture (Firecrawl #5759 recipe) -----------
def white_form_pair():
    from PIL import Image as PILImage
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    image = Image.new("RGB", (460, 620), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(DEJAVU, 16)
    for row in range(8):
        draw.text((24, 36 + 23 * row),
                  f"Synthetic body sentence number {row + 1}",
                  fill="black", font=font)
    for fill in ("white", "black"):
        pdf = Canvas(str(FIX / f"{fill}.pdf"), pagesize=(612, 792), invariant=1)
        pdf.beginForm("Inner", 0, 0, 460, 620)
        pdf.setFillColor(fill)
        for row in range(8):
            pdf.drawString(24, 574 - 23 * row,
                           f"Synthetic body sentence number {row + 1}")
        pdf.drawImage(ImageReader(image), 0, 0, width=460, height=620)
        pdf.endForm()
        pdf.beginForm("Outer", 0, 0, 460, 620)
        pdf.doForm("Inner")
        pdf.endForm()
        for row in range(5):
            pdf.drawString(72, 750 - 15 * row,
                           f"Direct page header line {row + 1}")
        pdf.saveState()
        pdf.translate(72, 38)
        pdf.doForm("Outer")
        pdf.restoreState()
        pdf.showPage()
        pdf.save()
    REFERENCES["white_body"] = [f"Synthetic body sentence number {i}"
                                for i in range(1, 9)]
    REFERENCES["white_header"] = [f"Direct page header line {i}"
                                  for i in range(1, 6)]


# --- 3. Fully scanned PDF (no text layer) ----------------------------------
def scanned():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    sentences = [
        "The quick brown fox jumps over the lazy dog",
        "Pack my box with five dozen liquor jugs",
        "How vexingly quick daft zebras jump",
        "Bright vixens jump dozy fowl quack",
        "Sphinx of black quartz judge my vow",
        "Two driven jocks help fax my big quiz",
    ]
    font = ImageFont.truetype(DEJAVU, 26)
    c = canvas.Canvas(str(FIX / "scanned.pdf"), pagesize=letter)
    for page in range(3):
        img = Image.new("RGB", (1200, 1500), "white")
        d = ImageDraw.Draw(img)
        for row, s in enumerate(sentences):
            d.text((60, 90 + 90 * row), s, fill="black", font=font)
        c.drawImage(ImageReader(img), 0, 0, width=612, height=792)
        c.showPage()
    c.save()
    words = sorted({w for s in sentences for w in s.split()})
    REFERENCES["scanned_words"] = words
    REFERENCES["scanned_sentences"] = sentences


# --- 4. Multilingual: Persian + English ------------------------------------
def multilingual():
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    fa_lines = [
        "سلام دنیا",
        "این یک سند آزمایشی است",
        "استخراج متن فارسی باید درست کار کند",
    ]
    en_lines = [
        "Hello world",
        "This is a test document",
        "English text extraction must work too",
    ]
    p = FIX / "multilingual.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    y = 720
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, y, "Multilingual test document")
    y -= 40
    c.setFont("Helvetica", 12)
    for line in en_lines:
        c.drawString(72, y, line)
        y -= 30
    if NASKH:
        pdfmetrics.registerFont(TTFont("Naskh", NASKH))
        c.setFont("Naskh", 14)
        for line in fa_lines:
            shaped = get_display(arabic_reshaper.reshape(line))
            c.drawRightString(540, y, shaped)
            y -= 34
    c.save()
    REFERENCES["multi_en"] = [w for s in en_lines for w in s.split()]
    REFERENCES["multi_fa"] = [w for s in fa_lines for w in s.split()]


# --- 5. Table + merged-header table ----------------------------------------
def tables():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    styles = getSampleStyleSheet()
    data = [["Product", "Price", "Stock"],
            ["Apple", "$1.20", "120"],
            ["Banana", "$0.80", "200"],
            ["Cherry", "$2.50", "75"]]
    t = Table(data)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    doc = SimpleDocTemplate(str(FIX / "table.pdf"), pagesize=letter)
    doc.build([Paragraph("Fruit price list", styles["Heading1"]),
               Spacer(1, 12), t,
               Spacer(1, 12),
               Paragraph("End of price list.", styles["Normal"])])
    REFERENCES["table_cells"] = [c for row in data for c in row]

    merged = [["Quarterly Sales Report"],
              ["Product", "Price", "Stock"],
              ["Apple", "$1.20", "120"],
              ["Banana", "$0.80", "200"]]
    t2 = Table(merged, colWidths=[120, 120, 120])
    t2.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("SPAN", (0, 0), (2, 0)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("BACKGROUND", (0, 1), (-1, 1), colors.whitesmoke),
    ]))
    doc2 = SimpleDocTemplate(str(FIX / "merged_table.pdf"), pagesize=letter)
    doc2.build([Paragraph("Merged header table", styles["Heading1"]),
                Spacer(1, 12), t2])
    REFERENCES["merged_cells"] = [c for row in merged for c in row]


# --- 5b. Empty grid: ruled table with no text at all ----------------------
def empty_grid():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(FIX / "empty_grid.pdf"), pagesize=letter)
    c.drawString(72, 720, "An empty ruled grid follows.")
    x0, y0, cw, ch = 72, 500, 120, 40
    for r in range(4):
        c.line(x0, y0 + r * ch, x0 + 3 * cw, y0 + r * ch)
    for col in range(4):
        c.line(x0 + col * cw, y0, x0 + col * cw, y0 + 3 * ch)
    c.save()
def columns():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (BaseDocTemplate, Frame, FrameBreak,
                                    PageTemplate, Paragraph)

    styles = getSampleStyleSheet()
    col1 = [Paragraph("COLUMN ONE marker alpha. " + " ".join(
        f"Left column sentence number {i}." for i in range(1, 7)),
        styles["Normal"])]
    col2 = [Paragraph("COLUMN TWO marker beta. " + " ".join(
        f"Right column sentence number {i}." for i in range(1, 7)),
        styles["Normal"])]
    doc = BaseDocTemplate(str(FIX / "columns.pdf"), pagesize=letter)
    frame1 = Frame(72, 72, 200, 648, id="left")
    frame2 = Frame(320, 72, 200, 648, id="right")
    doc.addPageTemplates([PageTemplate(id="two", frames=[frame1, frame2])])
    doc.build(col1 + [FrameBreak()] + col2)
    REFERENCES["columns_markers"] = ["COLUMN ONE marker alpha",
                                     "COLUMN TWO marker beta"]


# --- 7. Large: 120 pages ----------------------------------------------------
def large():
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(FIX / "large.pdf"), pagesize=letter)
    for n in range(1, 121):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 720, f"Large document page {n} of 120")
        c.setFont("Helvetica", 12)
        c.drawString(72, 690, f"Filler sentence for page {n} with some text.")
        c.drawString(72, 660, "The quick brown fox jumps over the lazy dog.")
        c.showPage()
    c.save()
    REFERENCES["large_pages"] = 120


# --- 8. Corrupt / empty / zero-byte / locked -------------------------------
def edge_cases():
    # truncated PDF (45% of a valid file)
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    good = FIX / "_good.pdf"
    c = canvas.Canvas(str(good), pagesize=letter)
    c.drawString(72, 720, "This is a valid document that will be truncated.")
    c.save()
    data = good.read_bytes()
    (FIX / "corrupt.pdf").write_bytes(data[: int(len(data) * 0.45)])
    good.unlink()
    # garbage, not a PDF at all
    (FIX / "garbage.bin").write_bytes(b"\x00\x01\x02not a pdf at all" * 10)
    # blank page (valid PDF, no text)
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(FIX / "empty.pdf"))
    doc.close()
    # zero bytes
    (FIX / "zero.pdf").write_bytes(b"")
    # password-protected
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 720), "Secret content behind password.",
                     fontsize=12)
    doc.save(str(FIX / "locked.pdf"),
             encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="secret", owner_pw="owner")
    doc.close()
    REFERENCES["edge"] = True


if __name__ == "__main__":
    nexus_pair()
    white_form_pair()
    scanned()
    multilingual()
    tables()
    empty_grid()
    columns()
    large()
    edge_cases()
    save_references()
    for f in sorted(FIX.glob("*")):
        print(f"{f.name:20s} {f.stat().st_size:>8d} bytes")
    print("references.json written")
