"""Парсинг веб-страниц: скачивание HTML, извлечение текста, ссылок, картинок.

Выполняется на ВМ (core), так как требует исходящего интернета.
Не требует агентов — чисто серверная функция.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import DATA_DIR, settings
from .audit import audit
from .files import attach_artifacts

log = logging.getLogger("web_fetch")

SHOTS_DIR = DATA_DIR / "shots"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_HTML_SIZE = 5 * 1024 * 1024  # 5 МБ
MAX_IMAGES = 20
MAX_IMAGE_SIZE = 3 * 1024 * 1024  # 3 МБ на картинку
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36 NetAssistant/1.0"


@dataclass
class FetchResult:
    url: str
    title: str
    text: str
    links: list[dict[str, str]]
    images: list[dict[str, Any]]
    meta: dict[str, str]
    screenshot_path: str | None = None
    error: str | None = None


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_meta(soup: BeautifulSoup) -> dict[str, str]:
    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property") or tag.get("itemprop")
        content = tag.get("content")
        if name and content:
            meta[name] = content
    # Open Graph
    for tag in soup.find_all("meta", property=re.compile(r"^og:")):
        content = tag.get("content")
        if content:
            meta[tag["property"]] = content
    return meta


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        abs_url = urljoin(base_url, href)
        text = _clean_text(a.get_text())
        if text:
            links.append({"url": abs_url, "text": text[:200]})
    return links[:100]


def _download_image(client: httpx.Client, img_url: str, base_url: str) -> dict[str, Any] | None:
    try:
        abs_url = urljoin(base_url, img_url)
        resp = client.get(abs_url, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return None
        content = resp.content
        if len(content) > MAX_IMAGE_SIZE:
            return None
        ctype = resp.headers.get("content-type", "")
        ext = mimetypes.guess_extension(ctype.split(";")[0]) or ".bin"
        if ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}:
            ext = ".bin"
        # Сохраняем
        fname = hashlib.sha256(content).hexdigest()[:16] + ext
        path = SHOTS_DIR / fname
        path.write_bytes(content)
        return {
            "url": abs_url,
            "local_path": f"/shots/{fname}",
            "size": len(content),
            "mime": ctype,
        }
    except Exception as exc:
        log.debug("image download failed: %s %s", img_url, exc)
        return None


def _extract_images(soup: BeautifulSoup, base_url: str, client: httpx.Client) -> list[dict[str, Any]]:
    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy")
        if not src:
            continue
        info = _download_image(client, src, base_url)
        if info:
            info["alt"] = img.get("alt", "")[:200]
            images.append(info)
        if len(images) >= MAX_IMAGES:
            break
    return images


def fetch_url(url: str, timeout: float = DEFAULT_TIMEOUT, extract_images: bool = True,
              max_text_len: int = 50000) -> FetchResult:
    """Скачать страницу, извлечь текст, ссылки, картинки."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return FetchResult(url=url, title="", text="", links=[], images=[], meta={},
                           error="поддерживаются только http/https")
    try:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return FetchResult(url=url, title="", text="", links=[], images=[], meta={},
                                   error=f"HTTP {resp.status_code}")
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return FetchResult(url=url, title="", text="", links=[], images=[], meta={},
                                   error=f"не HTML: {content_type}")
            html = resp.text[:MAX_HTML_SIZE]

            soup = BeautifulSoup(html, "html.parser")

            # Удаляем скрипты/стили
            for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer"]):
                tag.decompose()

            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            text = _clean_text(soup.get_text(" ", strip=True))
            if len(text) > max_text_len:
                text = text[:max_text_len] + " … [обрезано]"

            links = _extract_links(soup, url)
            meta = _extract_meta(soup)
            images = _extract_images(soup, url, client) if extract_images else []

            return FetchResult(
                url=url,
                title=title,
                text=text,
                links=links,
                images=images,
                meta=meta,
            )
    except httpx.TimeoutException:
        return FetchResult(url=url, title="", text="", links=[], images=[], meta={},
                           error="таймаут")
    except httpx.RequestError as exc:
        return FetchResult(url=url, title="", text="", links=[], images=[], meta={},
                           error=f"ошибка запроса: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("fetch_url unexpected error")
        return FetchResult(url=url, title="", text="", links=[], images=[], meta={},
                           error=f"внутренняя ошибка: {exc}")


# ── Команды каталога ──

def web_fetch(url: str, timeout: int = 30, images: bool = True, max_text: int = 50000) -> dict:
    """Скачать страницу и вернуть структурированные данные."""
    t0 = time.time()
    result = fetch_url(url, timeout=timeout, extract_images=images, max_text_len=max_text)
    duration = int((time.time() - t0) * 1000)
    audit.write(event="web_fetch", url=url, ok=result.error is None, duration_ms=duration,
                images=len(result.images), text_len=len(result.text))
    if result.error:
        return {"ok": False, "error": result.error}
    return {
        "ok": True,
        "url": result.url,
        "title": result.title,
        "text": result.text,
        "links": result.links,
        "images": result.images,
        "meta": result.meta,
    }


def web_search(query: str, engine: str = "duckduckgo", max_results: int = 10) -> dict:
    """Простой поиск через DuckDuckGo HTML (без API ключей)."""
    from urllib.parse import quote
    # DuckDuckGo HTML scrape
    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    result = fetch_url(search_url, timeout=20, extract_images=False, max_text_len=100000)
    if result.error:
        return {"ok": False, "error": result.error}
    soup = BeautifulSoup(result.text, "html.parser")
    items = []
    for link in soup.select(".result__snippet, .result__title, a.result__url"):
        # Упрощённый парсинг DDG HTML
        pass
    # Fallback: возвращаем первые ссылки из страницы
    return {"ok": True, "query": query, "results": result.links[:max_results]}


def web_screenshot(url: str, width: int = 1280, height: int = 720, timeout: int = 60) -> dict:
    """Скриншот страницы через headless Chrome (playwright). Опционально."""
    # Требует playwright и chromium — оставляем заглушку, можно расширить
    return {"ok": False, "error": "web_screenshot не реализован (нужен playwright + chromium)"}


# ── Внутренние утилиты для LLM ──

def _extract_main_content(html: str) -> str:
    """Пытается выделить основной контент (article, main, .content, #content)."""
    soup = BeautifulSoup(html, "html.parser")
    for selector in ["article", "main", "[role=main]", ".content", "#content", ".post", ".entry"]:
        el = soup.select_one(selector)
        if el:
            return _clean_text(el.get_text(" ", strip=True))
    return _clean_text(soup.get_text(" ", strip=True))


if __name__ == "__main__":
    # Быстрый тест
    import json
    r = fetch_url("https://example.com", timeout=10)
    print(json.dumps({"url": r.url, "title": r.title, "text": r.text[:200], "links": len(r.links), "images": len(r.images)}, ensure_ascii=False, indent=2))