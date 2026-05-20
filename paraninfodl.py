#!/usr/bin/env python3
"""
paraninfodl - Download an ebook from ebooks.paraninfo.es as a PDF.

Usage:
    paraninfodl <url> [options]

Options:
    --quality N     Re-encode images at JPEG quality N (1-95, e.g. 85).
                    Without this, images are embedded without re-encoding.
    --text-layer    Add a selectable/searchable text layer to the PDF.
    --keep-pages    Keep the downloaded images after generating the PDF.
    -o PATH         Output path for the PDF (default: ./<book-slug>.pdf).

Example:
    paraninfodl https://ebooks.paraninfo.es/reader/my-book
    paraninfodl https://ebooks.paraninfo.es/reader/my-book --quality 85 --text-layer

The first run opens a browser window for Google login.
Subsequent runs use the saved session at ~/.paraninfo_session.json.
"""

import os
import sys
import io
import re
import json
import base64
import hashlib
import shutil
import subprocess
import time
import argparse
from pathlib import Path
from html.parser import HTMLParser
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from playwright.sync_api import sync_playwright
import img2pdf
import requests
from PIL import Image

# ── Load .env if present ─────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Configuration ─────────────────────────────────────────────────────────────
SESSION_FILE = Path(
    os.environ.get("SESSION_FILE", "~/.paraninfo_session.json")
).expanduser()
LOGIN_TIMEOUT = int(os.environ.get("LOGIN_TIMEOUT", "180")) * 1000  # ms
PAGE_DELAY = float(os.environ.get("PAGE_DELAY", "0"))

BASE_URL = "https://ebooks.paraninfo.es"
APP_URL = "https://app.publica.la"

# ── Decryption constants (hardcoded in the viewer JS) ────────────────────────
_KEY = bytes.fromhex("c536859c222f0c3f277b5d9b16bb35b1db035192b9f8586b0db5f3fec6a017e4")
_IV_SEED = "694338fcfa701ec754bbdbc6"

HEADERS_FELINI = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://volpe2.publica.la/",
    "Origin": "https://volpe2.publica.la",
}


# ── Cryptography ──────────────────────────────────────────────────────────────
def make_iv(tenant_id: str, issue_id: str) -> bytes:
    data = json.dumps(
        {"tenant_id": tenant_id, "issue_id": issue_id, "iv_seed": _IV_SEED},
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode()).digest()[:12]


def decrypt(data: bytes, iv: bytes) -> bytes:
    return AESGCM(_KEY).decrypt(iv, base64.b64decode(data), None)


# ── Session ───────────────────────────────────────────────────────────────────
def save_session(cookies: list):
    SESSION_FILE.write_text(json.dumps({"cookies": cookies}, indent=2))
    print(f"  Session saved to {SESSION_FILE}")


def load_session() -> list | None:
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text())["cookies"]
    except Exception:
        return None


def cookies_to_header(cookies: list) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


# ── Login + extraction with Playwright ────────────────────────────────────────
def get_session_data(book_url: str) -> tuple:
    saved = load_session()

    _launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    _ctx_opts = {
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "locale": "es-ES",
    }
    _stealth = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"

    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            print("  Installing Chromium (first run only)...")
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"], check=True
            )
        browser = p.chromium.launch(headless=saved is not None, args=_launch_args)
        ctx = browser.new_context(**_ctx_opts)

        if saved:
            ctx.add_cookies(saved)
            print("  Using saved session...")

        page = ctx.new_page()
        page.add_init_script(_stealth)
        page.goto(book_url, wait_until="domcontentloaded")

        # Reader loads for guests too — force login if no saved session
        needs_login = (saved is None) or ("auth/login" in page.url or "/login" in page.url)
        if not needs_login:
            try:
                page.wait_for_function(
                    "window.volpe && window.volpe.token", timeout=10_000
                )
            except Exception:
                needs_login = True

        if needs_login:
            browser.close()
            print("  Opening browser for login...")
            browser = p.chromium.launch(headless=False, args=_launch_args)
            ctx = browser.new_context(**_ctx_opts)
            page = ctx.new_page()
            page.add_init_script(_stealth)
            page.goto(f"{BASE_URL}/auth/login")
            print("  → Log in with Google in the browser window.")
            print("  → The script will continue automatically once done.\n")
            page.wait_for_url(f"{BASE_URL}/library**", timeout=LOGIN_TIMEOUT)
            page.goto(book_url, wait_until="domcontentloaded")
            page.wait_for_function("window.volpe && window.volpe.token", timeout=30_000)

        print("  Waiting for the viewer to load...")
        volpe_token = page.evaluate("window.volpe.token")
        csrf_token = page.evaluate(
            "document.querySelector('meta[name=\"csrf-token\"]')?.content || ''"
        )
        tenant_id = page.evaluate("String(window.app?.tenant?.id || '3651')")
        issue_id = page.evaluate("String(window.volpe?.issue?.id || '')")

        cookies = ctx.cookies()
        browser.close()

    save_session(cookies)
    return cookies, volpe_token, csrf_token, tenant_id, issue_id


# ── Book API ──────────────────────────────────────────────────────────────────
def get_files_urls(cookies: list, volpe_token: str, csrf_token: str) -> list:
    r = requests.post(
        f"{APP_URL}/api/v1/sessions",
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRF-TOKEN": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "X-Farfalla-Id-Format-String": "true",
            "X-Farfalla-Tenant-Id": "3651",
            "reader-token": volpe_token,
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/",
            "Cookie": cookies_to_header(cookies),
        },
        json={
            "token": volpe_token,
            "app_url": f"{BASE_URL}/library",
            "reader": "volpe",
            "volpe_host": "farfalla",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["files_urls"]


# ── Page download ─────────────────────────────────────────────────────────────
def fetch_page(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS_FELINI, timeout=30)
    r.raise_for_status()
    if len(r.content) < 64:
        raise ValueError(f"server returned {len(r.content)}B: {r.content!r}")
    return r.content


def fetch_text_layer(url: str | None, iv: bytes) -> str | None:
    if not url:
        return None
    try:
        r = requests.get(url, headers=HEADERS_FELINI, timeout=30)
        if not r.ok or len(r.content) < 32:
            return None
        return decrypt(r.content, iv).decode("utf-8", errors="replace")
    except Exception:
        return None


# ── Text layer parsing ────────────────────────────────────────────────────────
class _SpanParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.spans = []
        self._cur_style = None

    def handle_starttag(self, tag, attrs):
        if tag == "span":
            self._cur_style = dict(attrs).get("style", "")

    def handle_endtag(self, tag):
        if tag == "span":
            self._cur_style = None

    def handle_data(self, data):
        if self._cur_style is not None and data.strip():
            self.spans.append((data.strip(), self._cur_style))


def _css_scalex(style: str) -> float:
    m = re.search(r'scaleX\(([\d.]+)\)', style)
    return float(m.group(1)) if m else 1.0


def _css_font_family(style: str) -> str:
    m = re.search(r"font-family\s*:\s*([^;]+)", style)
    return m.group(1).strip() if m else ""


def _css_val(style: str, prop: str, dim: int | None = None) -> float | None:
    m = re.search(rf"(?:^|;)\s*{prop}\s*:\s*([\d.]+)%", style)
    if m:
        return float(m.group(1)) / 100
    if dim:
        m = re.search(rf"(?:^|;)\s*{prop}\s*:\s*([\d.]+)px", style)
        if m:
            return float(m.group(1)) / dim
    return None


def parse_text_layer(html: str, page_w: int | None = None, page_h: int | None = None) -> list:
    """Returns list of (text, left_frac, top_frac, size_frac, scalex, font_family)."""
    p = _SpanParser()
    p.feed(html)
    result = []
    for text, style in p.spans:
        left = _css_val(style, "left", page_w)
        top = _css_val(style, "top", page_h)
        size = _css_val(style, "font-size", page_h) or 0.01
        # None means negative px (off left/top edge) or missing; > 1.0 means off right/bottom edge
        if left is None or top is None or left > 1.0 or top > 1.0:
            continue
        result.append((text, left, top, size, _css_scalex(style), _css_font_family(style)))
    return result


# ── PDF generation ────────────────────────────────────────────────────────────
def build_pdf(page_paths: list, output_path: str, quality: int = 0, text_layers: list | None = None):
    total = len(page_paths)

    # Always build the image layer with img2pdf (compact, no moveable objects)
    if quality:
        imgs = _compress_pages(page_paths, quality, total)
    else:
        imgs = [str(p) for p in page_paths]
    print(f"  Assembling {total} pages...")
    img_pdf_bytes = img2pdf.convert(imgs)

    if not text_layers or not any(text_layers):
        with open(output_path, "wb") as f:
            f.write(img_pdf_bytes)
        return

    # Read the actual page sizes img2pdf chose (pixels→points via DPI conversion)
    # so the text overlay uses the exact same coordinate space
    print(f"  Adding text layer...")
    from pypdf import PdfReader
    pdf_sizes = [
        (float(p.mediabox.width), float(p.mediabox.height))
        for p in PdfReader(io.BytesIO(img_pdf_bytes)).pages
    ]
    txt_pdf_bytes = _make_text_pdf(page_paths, text_layers, total, pdf_sizes)
    _merge_pdfs(img_pdf_bytes, txt_pdf_bytes, output_path)


def _compress_pages(page_paths: list, quality: int, total: int) -> list:
    result = []
    for i, path in enumerate(page_paths):
        print(f"  [{i + 1:03d}/{total}] compressing...", flush=True)
        buf = io.BytesIO()
        Image.open(path).save(buf, format="JPEG", quality=quality, optimize=True)
        result.append(buf.getvalue())
    return result


_OPEN_SANS_PATH = "/usr/share/fonts/OpenSans/OpenSans-Regular.ttf"
_PDF_FONTS: set = set()


def _pdf_font(family: str) -> str:
    """Return a ReportLab font name matching the CSS font-family, registering TTFs as needed."""
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase import pdfmetrics

    low = family.lower()
    if "roboto mono" in low or ("monospace" in low and "open sans" not in low):
        return "Courier"
    # Open Sans or generic sans-serif → embed OpenSans-Regular for accurate metrics
    if "OpenSans" not in _PDF_FONTS and Path(_OPEN_SANS_PATH).exists():
        pdfmetrics.registerFont(TTFont("OpenSans", _OPEN_SANS_PATH))
        _PDF_FONTS.add("OpenSans")
    return "OpenSans" if "OpenSans" in _PDF_FONTS else "Helvetica"


def _make_text_pdf(page_paths: list, text_layers: list, total: int, pdf_sizes: list) -> bytes:
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    for i, path in enumerate(page_paths):
        # Use the exact page size img2pdf produced, not raw pixel dimensions
        pdf_w, pdf_h = pdf_sizes[i] if i < len(pdf_sizes) else Image.open(path).size
        c.setPageSize((pdf_w, pdf_h))
        spans = text_layers[i] if i < len(text_layers) else []
        for text, left_f, top_f, size_f, scalex, font_family in spans:
            font_size = max(4, pdf_h * size_f)
            x = pdf_w * left_f
            y = pdf_h * (1.0 - top_f) - font_size * 0.8
            t = c.beginText(x, y)
            t.setTextRenderMode(3)  # invisible but selectable/searchable
            t.setFont(_pdf_font(font_family), font_size)
            t.setHorizScale(scalex * 100)  # match CSS scaleX() compression
            t.textLine(text)
            c.drawText(t)
        c.showPage()
    c.save()
    return buf.getvalue()


def _merge_pdfs(img_pdf_bytes: bytes, txt_pdf_bytes: bytes, output_path: str):
    from pypdf import PdfReader, PdfWriter

    img_reader = PdfReader(io.BytesIO(img_pdf_bytes))
    txt_reader = PdfReader(io.BytesIO(txt_pdf_bytes))
    writer = PdfWriter()
    for img_page, txt_page in zip(img_reader.pages, txt_reader.pages):
        img_page.merge_page(txt_page)
        writer.add_page(img_page)
    with open(output_path, "wb") as f:
        writer.write(f)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Download an ebook from ebooks.paraninfo.es as a PDF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="URL of the book")
    parser.add_argument(
        "--quality", type=int, default=0, metavar="N",
        help="Re-encode images at JPEG quality N (1-95, e.g. 85).",
    )
    parser.add_argument(
        "--text-layer", action="store_true",
        help="Add a selectable/searchable text layer to the PDF.",
    )
    parser.add_argument(
        "--keep-pages", action="store_true",
        help="Keep the downloaded page images after building the PDF.",
    )
    parser.add_argument(
        "-o", "--output", metavar="PATH",
        help="Output path for the PDF (default: ./<book-slug>.pdf). Can be a directory.",
    )
    args = parser.parse_args()

    if args.quality and not (1 <= args.quality <= 95):
        parser.error("--quality must be between 1 and 95")

    book_url = args.url.split("?")[0].rstrip("/")
    book_slug = book_url.split("/")[-1]

    if args.output:
        out = Path(args.output)
        output = str(out / f"{book_slug}.pdf" if out.is_dir() else out)
    else:
        output = f"{book_slug}.pdf"

    print(f"\n{'─' * 56}")
    print(f"  paraninfo_dl")
    print(f"  Book   : {book_slug}")
    print(f"  Output : {output}")
    if args.quality:
        print(f"  Quality: {args.quality}%")
    if args.text_layer:
        print(f"  Mode   : searchable text")
    print(f"{'─' * 56}\n")

    # 1. Session
    print("── [1/4] Session ─────────────────────────────────")
    cookies, volpe_token, csrf_token, tenant_id, issue_id = get_session_data(book_url)
    print(f"  tenant_id : {tenant_id}")
    print(f"  issue_id  : {issue_id}")

    # 2. Page URLs
    print("\n── [2/4] Fetching page URLs ───────────────────────")
    files_urls = get_files_urls(cookies, volpe_token, csrf_token)
    total = len(files_urls)
    print(f"  Pages: {total}")

    iv = make_iv(tenant_id, issue_id)

    # 3. Descargar
    _cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    pages_dir = _cache_home / "paraninfodl" / book_slug
    pages_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n── [3/4] Downloading {total} pages → {pages_dir}/ ──")
    failed = 0
    for i, page_urls in enumerate(files_urls):
        n = i + 1
        img_dest = pages_dir / f"{n:03d}.jpg"
        txt_dest = pages_dir / f"{n:03d}.html"

        need_img = not img_dest.exists()
        need_txt = args.text_layer and not txt_dest.exists() and page_urls.get("text_layer")

        if not need_img and not need_txt:
            print(f"  [{n:03d}/{total}] already cached, skipping")
            continue

        print(f"  [{n:03d}/{total}] ", end="", flush=True)
        ok = True
        try:
            if need_img:
                img = fetch_page(page_urls["large"])
                img_dest.write_bytes(img)
                print(f"✓ {len(img) // 1024}KB", end="")
            if need_txt:
                html = fetch_text_layer(page_urls["text_layer"], iv)
                if html:
                    txt_dest.write_text(html, encoding="utf-8")
                    print(f" +txt", end="")
            print()
        except Exception as e:
            print(f"✗ {e}")
            ok = False
            failed += 1

        if PAGE_DELAY:
            time.sleep(PAGE_DELAY)

    saved_pages = sorted(pages_dir.glob("*.jpg"))
    print(f"\n  OK: {len(saved_pages)}  Failed: {failed}")

    # 4. PDF
    print(f"\n── [4/4] Building PDF ────────────────────────────")
    text_layers = None
    if args.text_layer:
        text_layers = []
        for path in saved_pages:
            html_path = path.with_suffix(".html")
            if html_path.exists():
                w, h = Image.open(path).size
                text_layers.append(parse_text_layer(html_path.read_text(encoding="utf-8"), w, h))
            else:
                text_layers.append([])

    build_pdf(saved_pages, output, quality=args.quality, text_layers=text_layers)

    if not args.keep_pages:
        shutil.rmtree(pages_dir)
        print(f"  Cache cleared.")

    size_mb = os.path.getsize(output) / 1_048_576
    print(f"\n✓ Done: {output} ({size_mb:.1f} MB, {len(saved_pages)} pages)\n")


if __name__ == "__main__":
    main()
