from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from scraper.bit.r2_storage import R2Config, R2Storage

DEFAULT_START_URL = "https://www.bit.courts.go.jp/"
BIT_HOST = "www.bit.courts.go.jp"
BIT_ENTRY_PATHS = {"", "/", "/app/areaselect/ps002/h04", "/app/top/pt001/h01"}
OKAYAMA_ALL_PROPERTIES_BUTTON_TEXT = "選択した都道府県の全物件を検索する"
DEFAULT_ERROR_ARTIFACT_DIR = "artifacts/bit-error"
DETAIL_URL_PATTERN = re.compile(r"/app/propertyresult/")
PDF_DOWNLOAD_TEXT = re.compile(
    r"3\s*点\s*セット.*ダウンロード|３\s*点\s*セット.*ダウンロード|三\s*点\s*セット.*ダウンロード"
)


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


def collect_detail_links(
    html: str, base_url: str, max_details: int | None
) -> list[ScrapeTarget]:
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
        targets.append(
            ScrapeTarget(
                detail_url=absolute_url, stable_id=stable_id_from_url(absolute_url)
            )
        )
        if max_details is not None and len(targets) >= max_details:
            break
    return targets


def should_navigate_to_okayama(start_url: str) -> bool:
    parsed = urlparse(start_url)
    if parsed.netloc != BIT_HOST:
        return False
    return parsed.path.rstrip("/") in {path.rstrip("/") for path in BIT_ENTRY_PATHS}


async def wait_after_navigation_click(
    page: Page, expected_text: str | re.Pattern[str] | None = None
) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=30_000)
        return
    except PlaywrightTimeoutError:
        pass
    if expected_text is None:
        await page.wait_for_load_state("domcontentloaded", timeout=30_000)
        return
    await page.get_by_text(expected_text).first.wait_for(timeout=30_000)


async def click_region_or_prefecture(
    page: Page, label: str, expected_text: str | re.Pattern[str] | None = None
) -> None:
    candidate_locators = [
        page.get_by_role("link", name=re.compile(label)).first,
        page.get_by_text(re.compile(label)).first,
        page.locator(
            f'a[alt*="{label}"], a[title*="{label}"], area[alt*="{label}"], area[title*="{label}"], img[alt*="{label}"], img[title*="{label}"]'
        ).first,
    ]
    for locator in candidate_locators:
        try:
            await locator.wait_for(state="visible", timeout=5_000)
            await locator.click()
            await wait_after_navigation_click(page, expected_text)
            return
        except PlaywrightTimeoutError:
            continue

    clicked = await page.evaluate(
        r"""
        label => {
          const elements = Array.from(document.querySelectorAll('a[href], area[href]'));
          const target = elements.find(el => {
            const text = (el.innerText || el.textContent || '').trim();
            const alt = el.getAttribute('alt') || '';
            const title = el.getAttribute('title') || '';
            const href = el.getAttribute('href') || '';
            return text.includes(label) || alt.includes(label) || title.includes(label) || href.includes(label);
          });
          if (!target) return false;
          target.click();
          return true;
        }
        """,
        label,
    )
    if not clicked:
        raise PlaywrightTimeoutError(f'Could not find link for "{label}"')
    await wait_after_navigation_click(page, expected_text)


async def click_all_selected_prefecture_properties(page: Page) -> None:
    button_text = OKAYAMA_ALL_PROPERTIES_BUTTON_TEXT
    candidate_locators = [
        page.get_by_role("button", name=button_text).first,
        page.get_by_role("link", name=button_text).first,
        page.get_by_text(button_text).first,
        page.locator(
            f'input[value*="{button_text}"], button:has-text("{button_text}")'
        ).first,
    ]
    for locator in candidate_locators:
        try:
            await locator.wait_for(state="visible", timeout=5_000)
            await locator.click()
            await wait_after_navigation_click(
                page, re.compile("物件|検索結果|一覧|売却")
            )
            return
        except PlaywrightTimeoutError:
            continue

    clicked = await page.evaluate(
        r"""
        buttonText => {
          const elements = Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], a[href]'));
          const target = elements.find(el => {
            const text = (el.innerText || el.textContent || '').trim();
            const value = el.getAttribute('value') || '';
            const ariaLabel = el.getAttribute('aria-label') || '';
            const title = el.getAttribute('title') || '';
            return text.includes(buttonText) || value.includes(buttonText) || ariaLabel.includes(buttonText) || title.includes(buttonText);
          });
          if (!target) return false;
          target.click();
          return true;
        }
        """,
        button_text,
    )
    if not clicked:
        raise PlaywrightTimeoutError(f'Could not find button for "{button_text}"')
    await wait_after_navigation_click(page, re.compile("物件|検索結果|一覧|売却"))


async def navigate_to_okayama_list(page: Page) -> None:
    await click_region_or_prefecture(
        page, "中国", re.compile("岡山|鳥取|島根|広島|山口")
    )
    await click_region_or_prefecture(page, "岡山", OKAYAMA_ALL_PROPERTIES_BUTTON_TEXT)
    await click_all_selected_prefecture_properties(page)


async def save_error_artifacts(page: Page, artifact_dir: str) -> None:
    os.makedirs(artifact_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    screenshot_path = os.path.join(artifact_dir, f"bit-error-{timestamp}.png")
    html_path = os.path.join(artifact_dir, f"bit-error-{timestamp}.html")
    await page.screenshot(path=screenshot_path, full_page=True)
    html = await page.content()
    await asyncio.to_thread(lambda: open(html_path, "w", encoding="utf-8").write(html))
    print(
        f"Saved error artifacts: screenshot={screenshot_path} html={html_path}",
        file=sys.stderr,
    )


def dated_key(kind: str, stable_id: str, extension: str) -> str:
    now = datetime.now(timezone.utc)
    return f"okayama/{kind}/{now:%Y/%m/%d}/{stable_id}.{extension}"


async def save_detail_html(page: Page, storage: R2Storage, target: ScrapeTarget) -> str:
    html = await page.content()
    key = dated_key("html", target.stable_id, "html")
    return storage.put_bytes(key, html.encode("utf-8"), "text/html; charset=utf-8")


async def download_pdf_from_direct_link(
    page: Page, context: BrowserContext, storage: R2Storage, target: ScrapeTarget
) -> str | None:
    candidates = await page.locator("a").evaluate_all(r"""
        els => els
          .map(a => ({ href: a.href, text: a.innerText || a.textContent || '' }))
          .filter(a => /3\s*点\s*セット|３\s*点\s*セット|三\s*点\s*セット/.test(a.text) || /pdf/i.test(a.href))
        """)
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


async def download_pdf_by_click(
    page: Page, storage: R2Storage, target: ScrapeTarget
) -> str | None:
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


async def scrape(
    start_url: str,
    max_details: int | None,
    headless: bool,
    error_artifact_dir: str | None,
) -> None:
    storage = R2Storage(R2Config.from_env())
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True, locale="ja-JP")
        page = await context.new_page()
        try:
            await page.goto(start_url, wait_until="networkidle", timeout=60_000)
            if should_navigate_to_okayama(start_url):
                await navigate_to_okayama_list(page)
            list_html = await page.content()
            targets = collect_detail_links(list_html, page.url, max_details)
            print(f"Found {len(targets)} detail links")
            for index, target in enumerate(targets, start=1):
                print(f"[{index}/{len(targets)}] {target.detail_url}")
                await page.goto(
                    target.detail_url, wait_until="networkidle", timeout=60_000
                )
                html_key = await save_detail_html(page, storage, target)
                pdf_key = await download_pdf_from_direct_link(
                    page, context, storage, target
                )
                if pdf_key is None:
                    pdf_key = await download_pdf_by_click(page, storage, target)
                if pdf_key is None:
                    print(f"  saved html={html_key}; pdf=NOT_FOUND")
                else:
                    print(f"  saved html={html_key}; pdf={pdf_key}")
        except Exception:
            if error_artifact_dir:
                try:
                    await save_error_artifacts(page, error_artifact_dir)
                except Exception as artifact_error:
                    print(
                        f"Failed to save error artifacts: {artifact_error}",
                        file=sys.stderr,
                    )
            raise
        finally:
            await browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape BIT Okayama detail HTML and 3-piece-set PDFs to Cloudflare R2."
    )
    parser.add_argument(
        "--start-url",
        default=os.getenv("BIT_START_URL", DEFAULT_START_URL),
        help="BIT list/search-result URL to collect detail links from.",
    )
    parser.add_argument(
        "--max-details",
        type=parse_max_details,
        default=parse_max_details(os.getenv("SCRAPE_MAX_DETAILS")),
        help="Maximum number of detail pages to process. Unset means all links found on the start page.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium with a visible browser for local debugging.",
    )
    parser.add_argument(
        "--error-artifact-dir",
        default=os.getenv("BIT_ERROR_ARTIFACT_DIR", DEFAULT_ERROR_ARTIFACT_DIR),
        help="Directory where screenshots and HTML are saved when scraping fails.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    asyncio.run(
        scrape(
            start_url=args.start_url,
            max_details=args.max_details,
            headless=not args.headed,
            error_artifact_dir=args.error_artifact_dir,
        )
    )


if __name__ == "__main__":
    main()
