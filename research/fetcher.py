"""
research/fetcher.py — "Read pages" / "Extract text" / "Extract images" /
"Read PDFs" stages of the pipeline.

Kept deliberately dependency-light: requests + beautifulsoup4 (already in
requirements.txt) for HTML, pypdf (new, optional) for PDF text. Every
function degrades gracefully — a missing optional dependency or a network
failure never raises past this module; it's logged and the caller gets
back whatever could be extracted (possibly nothing).
"""
from __future__ import annotations

from .models import ExtractedImage, Source

_MAX_TEXT_CHARS = 20000     # cap per-page text so one huge page can't blow the report budget
_FETCH_TIMEOUT = 15
_UA = "Mozilla/5.0 (compatible; Mark-XLIX-ResearchBot/1.0)"


def _is_pdf_url(url: str) -> bool:
    return url.lower().split("?")[0].endswith(".pdf")


def fetch_and_extract(source: Source) -> tuple[str, list[ExtractedImage]]:
    """Fetch source.url and return (extracted_text, extracted_images).
    Mutates nothing on `source` — caller decides how to store the result."""
    if _is_pdf_url(source.url):
        text = _fetch_pdf_text(source.url)
        return text, []
    return _fetch_html(source.url)


def _fetch_html(url: str) -> tuple[str, list[ExtractedImage]]:
    try:
        import requests
    except Exception as e:
        print(f"[research/fetcher] ⚠️ requests unavailable: {e}")
        return "", []

    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception as e:
        print(f"[research/fetcher] ⚠️ fetch failed for {url}: {e}")
        return "", []

    try:
        from bs4 import BeautifulSoup
    except Exception as e:
        print(f"[research/fetcher] ⚠️ beautifulsoup4 unavailable: {e}")
        return resp.text[:_MAX_TEXT_CHARS], []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"[research/fetcher] ⚠️ HTML parse failed for {url}: {e}")
        return "", []

    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    text = text[:_MAX_TEXT_CHARS]

    images: list[ExtractedImage] = []
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not src or not src.startswith(("http://", "https://")):
            continue
        caption = img.get("alt") or img.get("title") or ""
        images.append(ExtractedImage(
            id=ExtractedImage.new_id(),
            source_url=url,
            image_url=src,
            caption=caption.strip(),
            kind=_guess_image_kind(src, caption),
        ))
        if len(images) >= 15:
            break

    return text, images


def _guess_image_kind(src: str, caption: str) -> str:
    blob = f"{src} {caption}".lower()
    if any(k in blob for k in ("chart", "graph", "plot")):
        return "chart"
    if any(k in blob for k in ("diagram", "architecture", "flow")):
        return "diagram"
    if any(k in blob for k in ("infographic",)):
        return "infographic"
    if any(k in blob for k in ("photo", "jpg", "jpeg")):
        return "photo"
    return "figure"


def _fetch_pdf_text(url: str) -> str:
    try:
        import requests
    except Exception as e:
        print(f"[research/fetcher] ⚠️ requests unavailable: {e}")
        return ""

    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT, headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception as e:
        print(f"[research/fetcher] ⚠️ PDF fetch failed for {url}: {e}")
        return ""

    try:
        import io
        from pypdf import PdfReader
    except Exception as e:
        print(f"[research/fetcher] ⚠️ pypdf unavailable, skipping PDF text extraction: {e}")
        return ""

    try:
        reader = PdfReader(io.BytesIO(resp.content))
        pages_text = []
        for page in reader.pages[:40]:      # cap pages read for very long PDFs
            try:
                pages_text.append(page.extract_text() or "")
            except Exception:
                continue
        text = " ".join(" ".join(pages_text).split())
        return text[:_MAX_TEXT_CHARS]
    except Exception as e:
        print(f"[research/fetcher] ⚠️ PDF parse failed for {url}: {e}")
        return ""
