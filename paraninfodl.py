#!/usr/bin/env python3
"""
paraninfo_dl.py
---------------
Descarga un libro de ebooks.paraninfo.es como PDF.

Uso:
    python3 paraninfo_dl.py <url_del_libro>

Ejemplo:
    python3 paraninfo_dl.py https://ebooks.paraninfo.es/reader/lenguajes-de-marcas-y-sistemas-de-gestion-de-informacion-2a-edicion-2025

La primera vez abre una ventana del navegador para login con Google.
Las siguientes veces usa la sesión guardada automáticamente.
"""

import sys
import os
import json
import base64
import hashlib
import time
import requests
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from playwright.sync_api import sync_playwright
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image
import io

# ── Cargar .env si existe ─────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Configuración ─────────────────────────────────────────────────────────────
SESSION_FILE = Path(
    os.environ.get("SESSION_FILE", "~/.paraninfo_session.json")
).expanduser()
LOGIN_TIMEOUT = int(os.environ.get("LOGIN_TIMEOUT", "180")) * 1000  # ms
PAGE_DELAY = float(os.environ.get("PAGE_DELAY", "0"))

BASE_URL = "https://ebooks.paraninfo.es"
APP_URL = "https://app.publica.la"

# ── Constantes de descifrado (hardcodeadas en el JS del visor) ────────────────
_KEY = bytes.fromhex("c536859c222f0c3f277b5d9b16bb35b1db035192b9f8586b0db5f3fec6a017e4")
_IV_SEED = "694338fcfa701ec754bbdbc6"

HEADERS_FELINI = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://volpe2.publica.la/",
    "Origin": "https://volpe2.publica.la",
}


# ── Criptografía ──────────────────────────────────────────────────────────────
def make_iv(tenant_id: str, issue_id: str) -> bytes:
    """Replica _O() del JS: SHA-256({tenant_id, issue_id, iv_seed})[:12]"""
    data = json.dumps(
        {"tenant_id": tenant_id, "issue_id": issue_id, "iv_seed": _IV_SEED},
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode()).digest()[:12]


def decrypt(data: bytes, iv: bytes) -> bytes:
    """Replica CK() + decryptArrayBuffer(): base64 → AES-GCM decrypt"""
    b64 = data.strip()
    padding = 4 - len(b64) % 4
    if padding != 4:
        b64 += b"=" * padding
    return AESGCM(_KEY).decrypt(iv, base64.b64decode(b64), None)


# ── Sesión ────────────────────────────────────────────────────────────────────
def save_session(cookies: list):
    SESSION_FILE.write_text(json.dumps({"cookies": cookies}, indent=2))
    print(f"  Sesión guardada en {SESSION_FILE}")


def load_session() -> list | None:
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text())["cookies"]
    except Exception:
        return None


def cookies_to_header(cookies: list) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


# ── Login + extracción con Playwright ─────────────────────────────────────────
def get_session_data(book_url: str) -> tuple:
    """
    Abre el libro (con login si hace falta).
    Devuelve: (cookies, volpe_token, csrf_token, tenant_id, issue_id)
    """
    saved = load_session()
    headless = saved is not None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()

        if saved:
            ctx.add_cookies(saved)
            print("  Usando sesión guardada...")

        page = ctx.new_page()
        page.goto(book_url, wait_until="domcontentloaded")

        # Si redirige al login (sesión expirada o primera vez)
        if "auth/login" in page.url or "/login" in page.url:
            if headless:
                browser.close()
                print("  Sesión expirada, abriendo navegador para re-login...")
                browser = p.chromium.launch(headless=False)
                ctx = browser.new_context()
                page = ctx.new_page()
                page.goto(f"{BASE_URL}/auth/login")

            print("  → Haz login con Google en la ventana que se ha abierto.")
            print("  → El script continuará automáticamente al terminar.\n")
            page.wait_for_url(f"{BASE_URL}/library**", timeout=LOGIN_TIMEOUT)
            page.goto(book_url, wait_until="domcontentloaded")

        # Esperar window.volpe
        print("  Esperando que cargue el visor...")
        try:
            page.wait_for_function("window.volpe && window.volpe.token", timeout=30_000)
        except Exception:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("window.volpe && window.volpe.token", timeout=30_000)

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


# ── API del libro ─────────────────────────────────────────────────────────────
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


# ── Descarga de páginas ───────────────────────────────────────────────────────
def fetch_decrypt(url: str, iv: bytes) -> bytes:
    r = requests.get(url, headers=HEADERS_FELINI, timeout=30)
    r.raise_for_status()
    return decrypt(r.content, iv)


# ── Generación de PDF ─────────────────────────────────────────────────────────
def build_pdf(pages: list, output_path: str):
    c = canvas.Canvas(output_path)
    total = len(pages)
    for i, p in enumerate(pages):
        print(f"  [{i + 1:03d}/{total}] añadiendo al PDF...", flush=True)
        img = Image.open(io.BytesIO(p["image"]))
        w, h = img.size
        c.setPageSize((w, h))
        c.drawImage(ImageReader(img), 0, 0, width=w, height=h)
        c.showPage()
    c.save()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    book_url = sys.argv[1].split("?")[0].rstrip("/")
    book_slug = book_url.split("/")[-1]
    output = f"{book_slug}.pdf"

    print(f"\n{'─' * 56}")
    print(f"  paraninfo_dl")
    print(f"  Libro : {book_slug}")
    print(f"  Salida: {output}")
    print(f"{'─' * 56}\n")

    # 1. Sesión
    print("── [1/4] Sesión ──────────────────────────────────")
    cookies, volpe_token, csrf_token, tenant_id, issue_id = get_session_data(book_url)
    print(f"  tenant_id : {tenant_id}")
    print(f"  issue_id  : {issue_id}")

    # 2. URLs de páginas
    print("\n── [2/4] Obteniendo URLs de páginas ──────────────")
    files_urls = get_files_urls(cookies, volpe_token, csrf_token)
    total = len(files_urls)
    print(f"  Páginas: {total}")

    iv = make_iv(tenant_id, issue_id)
    print(f"  IV : {iv.hex()}")

    # 3. Descargar
    print(f"\n── [3/4] Descargando {total} páginas ─────────────────")
    pages = []
    failed = 0
    for i, urls in enumerate(files_urls):
        n = i + 1
        print(f"  [{n:03d}/{total}] ", end="", flush=True)
        try:
            img = fetch_decrypt(urls["large"], iv)
            pages.append({"image": img, "page": n})
            print(f"✓ {len(img) // 1024}KB")
        except Exception as e:
            print(f"✗ {e}")
            failed += 1
        if PAGE_DELAY:
            time.sleep(PAGE_DELAY)

    print(f"\n  OK: {len(pages)}  Fallidas: {failed}")

    # 4. PDF
    print(f"\n── [4/4] Generando PDF ───────────────────────────")
    build_pdf(pages, output)
    size_mb = os.path.getsize(output) / 1_048_576
    print(f"\n✓ Listo: {output} ({size_mb:.1f} MB, {len(pages)} páginas)\n")


if __name__ == "__main__":
    main()
