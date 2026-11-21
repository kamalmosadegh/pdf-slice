# pdf-slice — استخراج PDF به مارک‌داون تمیز

[English](README.md)

ابزاری کوچک و محلی که PDF را به مارک‌داون تمیز تبدیل می‌کند و **هیچ‌وقت
بی‌صدا محتوای اشتباه برنمی‌گرداند**. ساخته‌شده از روی دو خرابی واقعی
فایرکرال:

- **#3968 — کش محتوای سند اشتباه را می‌داد:** چند سند متفاوت با اسم فایل
  یکسان (`Nexus.pdf`) یک هش کش می‌گرفتند و کاربر گزارش ماه اشتباه را
  می‌گرفت. این ابزار کلید کش را **URL کامل + هش SHA-256 بایت‌های
  دانلودشده** می‌گذارد (هرگز اسم فایل) و پیش از تحویل کش، هش را چک
  می‌کند؛ عدم تطابق یعنی استخراج تازه.
- **#5759 — متن سفید در فرم‌های تو در تو:** متن بدنه سفیدرنگ زیر یک تصویر
  مات گم می‌شد و طبقه‌بند می‌گفت «متن‌محور، نیازی به OCR نیست»؛ فقط هدر
  برمی‌گشت. این ابزار وقتی استخراج بومی نسبت به صفحه مشکوک کم باشد،
  **خودکار به OCR می‌رود** و مسیر استفاده‌شده را اعلام می‌کند.

## نصب

```bash
# روش ۱ — از روی سورس
git clone https://github.com/kamalmosadegh/pdf-slice && cd pdf-slice
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python extract.py --help

# روش ۲ — نصب با pip (entry point)
pip install .
pdf-slice --help

# روش ۳ — یک دستور با uvx (بعد از انتشار روی PyPI)
uvx pdf-slice --help
```

نیازمندی‌ها سبک و CPU-only هستند: PyMuPDF ،pytesseract ،Pillow و
tesseract (روی سیستم نصب باشد؛ `eng` و `fas` پشتیبانی می‌شوند). هیچ مدل
سنگین GPU، هیچ کلید API، هیچ سرویس پولی.

```bash
# پیش‌نیاز سیستمی برای OCR (اگر نباشد، ابزار با هشدار بلند ادامه می‌دهد
# و صفحه‌های اسکن‌شده را کم‌اطمینان برمی‌گرداند — هرگز بی‌صدا نیست)
sudo apt-get install -y tesseract-ocr tesseract-ocr-fas
```

## استفاده

```bash
# یک فایل -> مارک‌داون + متادیتای JSON
pdf-slice report.pdf

# یک URL (کش با URL کامل + هش محتوا)
pdf-slice https://example.com/api/docs/123

# چانک‌های RAG با منشأ (JSONL: هر خط یک چانک)
pdf-slice report.pdf --format jsonl
# هر دو خروجی با هم
pdf-slice report.pdf --format both

# یک پوشه کامل + گزارش جمعی
pdf-slice --batch ./pdfs --out-dir ./out

# OCR فارسی
pdf-slice scanned-fa.pdf --ocr-lang eng+fas

# آستانه‌ها (پیش‌فرض: ۵۰ کاراکتر در صفحه؛ ۲۰۰ برای صفحه‌های عکس‌دار)
pdf-slice scan.pdf --min-chars-per-page 80 --image-page-chars 300
```

## خروجی

- **`*.md`** — مارک‌داون تمیز با ترتیب خواندن طبیعی (چندستونه) و جدول‌ها
  به شکل جدول مارک‌داون.
- **`*.json`** — متادیتا: `extraction_path` ‏(`native` / `ocr` /
  `fallback`)، جزئیات هر صفحه (منشأ، تعداد کاراکتر، جدول‌ها، زمان)،
  `content_hash`، وضعیت کش (`hit` / `miss` / `miss-hash-mismatch`)،
  `warnings`، زمان‌بندی.
- **`*.jsonl`** — چانک RAG؛ هر خط یک آبجکت با فیلدهای: `text` ،`page` ،
  `element_type` ‏(`heading` / `paragraph` / `table`)، `confidence` ،
  `source` ‏(uri + `content_hash`)، `reading_order`.

**قانون طلایی:** اگر `warnings` خالی نیست، پیش از اعتماد به مارک‌داون
آن را بخوانید. مسیر `fallback` یعنی اطمینان پایین، حتی بعد از OCR.

## کش

- کلید URL: `SHA-256(URL کامل)`؛ محتوای ذخیره‌شده فقط وقتی تحویل داده
  می‌شود که `content_hash` ذخیره‌شده با هش بایت‌های تازه‌دانلودشده برابر
  باشد.
- کلید فایل محلی: `SHA-256` محتوای فایل.
- محل پیش‌فرض کش: `~/.cache/pdf-slice` (با `--cache-dir` یا متغیر محیطی
  `PDF_SLICE_CACHE_DIR` قابل تغییر است).
- `--no-cache` کش را کاملاً خاموش می‌کند.

## سرور MCP و اسکیل ایجنت

```bash
# اجرای سرور MCP (stdio) برای Claude Code / Cursor / Codex
pdf-slice-mcp
```

- ابزار `extract_pdf(source, ocr_lang="eng")` و
  `verify_pdf(source, second_source)` (مقایسه دو مسیر و گزارش
  `match_rate`).
- نمونه کانفیگ: `mcp-config-claude.json` و `mcp-config-cursor.json`
  (مسیرهای مطلق داخل فایل را برای ماشین خودتان تنظیم کنید).
- اسکیل ایجنت: `skills/pdf-slice/SKILL.md`.

## بنچمارک (روی فیکسچرهای همراه ابزار)

<!-- BENCHMARK:START -->
| فایل | مسیر | بازیابی کلمات | ثانیه/صفحه | خطای بی‌صدا |
|---|---|---|---|---|
| white | native | 100% | 1.747 | 0 |
| black | native | None | 1.421 | 0 |
| scanned | fallback | 0% | 1.273 | 0 |
| multilingual | native | 100% | 1.797 | 0 |
| table | native | 100% | 1.855 | 0 |
| columns | native | None | 1.836 | 0 |
| large | native | None | 0.52 | 0 |

**مجموع خطاهای بی‌صدا: 0** — خطای بی‌صدا یعنی محتوای اشتباه یا ناقص بدون هیچ هشدار. هدف این ابزار صفر است.

تولیدشده با `tests/benchmark.py`؛ فیکسچرها در `tests/fixtures/`.
<!-- BENCHMARK:END -->

خودسنجی: `venv/bin/python tests/benchmark.py` و ماتریس کامل تست:
`venv/bin/python tests/test_slice.py`

## بیرون از اسکوپ (آگاهانه)

کلون فایرکرال، کراول، ضدبات، زیرساخت مرورگر، قیمت‌گذاری، SDK، و مدل‌های
سنگین چیدمان/فرمول (مثل UniMERNet یا TableFormer). برتری این ابزار تضمین
«بی‌صدا اشتباه نده» و سرعت مسیر بومی است.

## لایسنس

MIT — فایل `LICENSE`.
