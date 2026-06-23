from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from scraper.bit.r2_storage import R2Config, R2Storage

DEFAULT_START_URL = "https://www.bit.courts.go.jp/app/areaselect/ps002/h04"
DETAIL_URL_PATTERN = re.compile(r"/app/propertyresult/")
PDF_DOWNLOAD_TEXT = re.compile(r"3\s*点\s*セット.*ダウンロード|３\s*点\s*セット.*ダウンロード|三\s*点\s*セット.*ダウンロード")


@dataclass(frozen=True)
class ScrapeTarget:
    detail_url: str
    stable_id: str


def parse_max_details(raw_value: str | None) -> int | None:
    if raw_value is None or raw_value == "":
        return None
    value = int(raw_value)
    if value < 1:
        raise ValueError("max details must be greater than or equal to 1")
    return value


def stable_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    path_slug = re.sub(r"[^0-9A-Za-z]+", "-", parsed.path).strip("-")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{path_slug}-{digest}"


def collect_detail_links(html: str, base_url: str, max_details: int | None) -> list[ScrapeTarget]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    targets: list[ScrapeTarget] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        absolute_url = urljoin(base_url, href)
        if not DETAIL_URL_PATTERN.search(urlparse(absolute_url).path):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        targets.append(ScrapeTarget(detail_url=absolute_url, stable_id=stable_id_from_url(absolute_url)))
        if max_details is not None and len(targets) >= max_details:
            break
    return targets


def dated_key(kind: str, stable_id: str, extension: str) -> str:
    now = datetime.now(timezone.utc)
    return f"okayama/{kind}/{now:%Y/%m/%d}/{stable_id}.{extension}"


async def save_detail_html(page: Page, storage: R2Storage, target: ScrapeTarget) -> str:
    html = await page.content()
    key = dated_key("html", target.stable_id, "html")
    return storage.put_bytes(key, html.encode("utf-8"), "text/html; charset=utf-8")


async def download_pdf_from_direct_link(page: Page, context: BrowserContext, storage: R2Storage, target: ScrapeTarget) -> str | None:
    candidates = await page.locator("a").evaluate_all(
        r"""
        els => els
          .map(a => ({ href: a.href, text: a.innerText || a.textContent || '' }))
          .filter(a => /3\s*点\s*セット|３\s*点\s*セット|三\s*点\s*セット/.test(a.text) || /pdf/i.test(a.href))
        """
    )
    for candidate in candidates:
        href = candidate.get("href")
        if not href:
            continue
        response = await context.request.get(href)
        if not response.ok:
            continue
        body = await response.body()
        content_type = response.headers.get("content-type", "application/pdf")
        if b"%PDF" not in body[:1024] and "pdf" not in content_type.lower():
            continue
        key = dated_key("pdf", target.stable_id, "pdf")
        return storage.put_bytes(key, body, "application/pdf")
    return None


async def download_pdf_by_click(page: Page, storage: R2Storage, target: ScrapeTarget) -> str | None:
    locator = page.get_by_text(PDF_DOWNLOAD_TEXT).first
    try:
        await locator.wait_for(timeout=5_000)
    except PlaywrightTimeoutError:
        return None
    try:
        async with page.expect_download(timeout=20_000) as download_info:
            await locator.click()
        download = await download_info.value
        body = await download.path()
        if body is None:
            stream = await download.create_read_stream()
            chunks: list[bytes] = []
            while True:
                chunk = await stream.read()
                if not chunk:
                    break
                chunks.append(chunk)
            pdf_bytes = b"".join(chunks)
        else:
            pdf_bytes = await asyncio.to_thread(lambda: open(body, "rb").read())
        key = dated_key("pdf", target.stable_id, "pdf")
        return storage.put_bytes(key, pdf_bytes, "application/pdf")
    except PlaywrightTimeoutError:
        return None


async def scrape(start_url: str, max_details: int | None, headless: bool) -> None:
    storage = R2Storage(R2Config.from_env())
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True, locale="ja-JP")
        page = await context.new_page()
        await page.goto(start_url, wait_until="networkidle", timeout=60_000)
        list_html = await page.content()
        targets = collect_detail_links(list_html, page.url, max_details)
        print(f"Found {len(targets)} detail links")
        for index, target in enumerate(targets, start=1):
            print(f"[{index}/{len(targets)}] {target.detail_url}")
            await page.goto(target.detail_url, wait_until="networkidle", timeout=60_000)
            html_key = await save_detail_html(page, storage, target)
            pdf_key = await download_pdf_from_direct_link(page, context, storage, target)
            if pdf_key is None:
                pdf_key = await download_pdf_by_click(page, storage, target)
            if pdf_key is None:
                print(f"  saved html={html_key}; pdf=NOT_FOUND")
            else:
                print(f"  saved html={html_key}; pdf={pdf_key}")
        await browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape BIT Okayama detail HTML and 3-piece-set PDFs to Cloudflare R2.")
    parser.add_argument("--start-url", default=os.getenv("BIT_START_URL", DEFAULT_START_URL), help="BIT list/search-result URL to collect detail links from.")
    parser.add_argument("--max-details", type=parse_max_details, default=parse_max_details(os.getenv("SCRAPE_MAX_DETAILS")), help="Maximum number of detail pages to process. Unset means all links found on the start page.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium with a visible browser for local debugging.")
    return parser


def main(argv: Iterable[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(scrape(start_url=args.start_url, max_details=args.max_details, headless=not args.headed))


if __name__ == "__main__":
    main()
