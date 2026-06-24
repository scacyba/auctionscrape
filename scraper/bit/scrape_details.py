from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import (
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
DETAIL_ONCLICK_PATTERN = re.compile(
    r"tranPropertyDetail\(\s*[\"'](?P<sale_unit_id>\d+)[\"']\s*,\s*[\"'](?P<court_id>\d+)[\"']"
)
LOG_PREFIX = "[bit-scrape]"
PDF_DOWNLOAD_TEXT = re.compile(
    r"3\s*点\s*セット.*ダウンロード|３\s*点\s*セット.*ダウンロード|三\s*点\s*セット.*ダウンロード"
)
DETAIL_PAGE_TEXT = re.compile("3点セット|３点セット|物件明細|現況調査|評価書")


@dataclass(frozen=True)
class ScrapeTarget:
    detail_url: str
    stable_id: str
    sale_unit_id: str | None = None
    court_id: str | None = None
    title: str | None = None
    page_number: int = 1


def env_value_or_default(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value


def normalize_start_url(start_url: str | None) -> str:
    if start_url is None or start_url.strip() == "":
        return DEFAULT_START_URL
    return start_url.strip()


def log_progress(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


async def log_page_state(page: Page, label: str) -> None:
    try:
        title = await page.title()
    except Exception as title_error:
        title = f"<title unavailable: {title_error}>"
    log_progress(f"{label}: url={page.url} title={title!r}")


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


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def title_from_anchor(anchor) -> str | None:
    text = normalize_text(anchor.get_text(" ", strip=True))
    if not text:
        return None
    first_line = normalize_text(text.split("売却基準価額")[0])
    return first_line or text


def scrape_target_from_anchor(
    anchor, base_url: str, page_number: int = 1
) -> ScrapeTarget | None:
    href = anchor.get("href", "")
    absolute_url = urljoin(base_url, href)
    if DETAIL_URL_PATTERN.search(urlparse(absolute_url).path):
        return ScrapeTarget(
            detail_url=absolute_url,
            stable_id=stable_id_from_url(absolute_url),
            title=title_from_anchor(anchor),
            page_number=page_number,
        )

    onclick = anchor.get("onclick", "")
    match = DETAIL_ONCLICK_PATTERN.search(onclick)
    if not match:
        return None

    # BIT renders detail rows as href="#" links and stores the transition
    # parameters in tranPropertyDetail(saleUnitId, courtId, ...). The URL below
    # is only an identifier/storage key; actual navigation must click the link
    # because direct detail URLs are rejected by the site.
    sale_unit_id = match.group("sale_unit_id")
    court_id = match.group("court_id")
    detail_url = urljoin(
        base_url,
        f"/app/propertyresult/pr001/h05?saleUnitId={sale_unit_id}&courtId={court_id}",
    )
    return ScrapeTarget(
        detail_url=detail_url,
        stable_id=stable_id_from_url(detail_url),
        sale_unit_id=sale_unit_id,
        court_id=court_id,
        title=title_from_anchor(anchor),
        page_number=page_number,
    )


def collect_detail_links(
    html: str, base_url: str, max_details: int | None, page_number: int = 1
) -> list[ScrapeTarget]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    targets: list[ScrapeTarget] = []
    for anchor in soup.find_all("a"):
        target = scrape_target_from_anchor(anchor, base_url, page_number)
        if target is None:
            continue
        if target.detail_url in seen:
            continue
        seen.add(target.detail_url)
        targets.append(target)
        if max_details is not None and len(targets) >= max_details:
            break
    return targets


GET_DATA_PATTERN = re.compile(r"getData\(\s*(?P<page>\d+)\s*\)")


def collect_pagination_pages(html: str) -> list[int]:
    soup = BeautifulSoup(html, "lxml")
    pages: set[int] = set()
    for anchor in soup.select(".pagination a[onclick]"):
        match = GET_DATA_PATTERN.search(anchor.get("onclick", ""))
        if not match:
            continue
        pages.add(int(match.group("page")))
    return sorted(page for page in pages if page >= 1)


async def navigate_to_result_page(page: Page, page_number: int) -> None:
    log_progress(f"navigating list pagination to page {page_number}")
    locator = page.locator(f'.pagination a[onclick*="getData({page_number})"]').first
    try:
        await locator.wait_for(state="visible", timeout=5_000)
        await locator.click()
    except PlaywrightTimeoutError:
        invoked = await page.evaluate(
            r"""
            pageNumber => {
              if (typeof getData !== 'function') return false;
              getData(pageNumber);
              return true;
            }
            """,
            page_number,
        )
        if not invoked:
            raise PlaywrightTimeoutError(f"Could not navigate to page {page_number}")
    await wait_after_navigation_click(page, re.compile("物件|検索結果|一覧|売却"))


async def collect_targets_from_all_pages(
    page: Page, max_details: int | None
) -> tuple[list[ScrapeTarget], str, int]:
    base_url = page.url
    seen_targets: set[str] = set()
    visited_pages: set[int] = set()
    pending_pages: list[int] = [1]
    targets: list[ScrapeTarget] = []
    current_page_number = 1

    while pending_pages:
        page_number = pending_pages.pop(0)
        if page_number in visited_pages:
            continue
        if page_number != current_page_number:
            await navigate_to_result_page(page, page_number)
            current_page_number = page_number
        list_html = await page.content()
        await log_link_summary(list_html, base_url)
        visited_pages.add(page_number)

        for target in collect_detail_links(list_html, base_url, None, page_number):
            if target.detail_url in seen_targets:
                continue
            seen_targets.add(target.detail_url)
            targets.append(target)
            if max_details is not None and len(targets) >= max_details:
                return targets, base_url, current_page_number

        for discovered_page in collect_pagination_pages(list_html):
            if discovered_page not in visited_pages and discovered_page not in pending_pages:
                pending_pages.append(discovered_page)
        pending_pages.sort()

    return targets, base_url, current_page_number


def should_navigate_to_okayama(start_url: str) -> bool:
    start_url = normalize_start_url(start_url)
    parsed = urlparse(start_url)
    if parsed.netloc != BIT_HOST:
        return False
    return parsed.path.rstrip("/") in {path.rstrip("/") for path in BIT_ENTRY_PATHS}


async def wait_after_navigation_click(
    page: Page, expected_text: str | re.Pattern[str] | None = None
) -> None:
    # BIT often performs full-page transitions after clicks; prefer networkidle but
    # fall back to waiting for text that identifies the expected next screen.
    try:
        log_progress("waiting for networkidle after click")
        await page.wait_for_load_state("networkidle", timeout=30_000)
        await log_page_state(page, "networkidle reached")
        if expected_text is None:
            return
        await page.get_by_text(expected_text).first.wait_for(timeout=10_000)
        await log_page_state(page, "expected text is visible after networkidle")
        return
    except PlaywrightTimeoutError:
        log_progress("networkidle or expected text wait timed out; falling back to expected text wait")
    if expected_text is None:
        await page.wait_for_load_state("domcontentloaded", timeout=30_000)
        return
    await page.get_by_text(expected_text).first.wait_for(timeout=30_000)
    await log_page_state(page, "expected text is visible")


async def click_region_or_prefecture(
    page: Page, label: str, expected_text: str | re.Pattern[str] | None = None
) -> None:
    log_progress(f"looking for region/prefecture control: {label}")
    # Prefer user-visible/accessible locators; use attributes only as fallback for
    # image maps and non-standard clickable controls on the court site.
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
            log_progress(f"clicking region/prefecture control: {label}")
            await locator.click()
            await wait_after_navigation_click(page, expected_text)
            return
        except PlaywrightTimeoutError:
            continue

    log_progress(f"falling back to DOM search for: {label}")
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
    log_progress(f"looking for all-properties button: {button_text}")
    # After selecting Okayama, BIT requires this final confirmation button before
    # it shows the property-result/list page.
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
            log_progress(f"clicking all-properties button: {button_text}")
            await locator.click()
            await wait_after_navigation_click(
                page, re.compile("物件|検索結果|一覧|売却")
            )
            return
        except PlaywrightTimeoutError:
            continue

    log_progress(f"falling back to DOM search for button: {button_text}")
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
    log_progress("starting top-page navigation to Okayama list")
    await log_page_state(page, "before Okayama navigation")
    await click_region_or_prefecture(
        page, "中国", re.compile("岡山|鳥取|島根|広島|山口")
    )
    await click_region_or_prefecture(page, "岡山", OKAYAMA_ALL_PROPERTIES_BUTTON_TEXT)
    await click_all_selected_prefecture_properties(page)
    await log_page_state(page, "after Okayama list navigation")


async def log_link_summary(html: str, base_url: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    anchors = soup.find_all("a")
    detail_hrefs = [
        target.detail_url
        for anchor in anchors
        if (target := scrape_target_from_anchor(anchor, base_url)) is not None
    ]
    log_progress(
        f"collected list HTML: bytes={len(html.encode('utf-8'))} anchors={len(anchors)} detail_candidates={len(detail_hrefs)}"
    )
    if detail_hrefs:
        for href in detail_hrefs[:5]:
            log_progress(f"sample detail link: {href}")
        return
    # If no detail links were found, print a few visible anchors to identify which
    # screen the workflow actually reached without dumping full page contents.
    for index, anchor in enumerate(anchors[:10], start=1):
        text = anchor.get_text(" ", strip=True)[:80]
        href = urljoin(base_url, anchor.get("href", ""))
        log_progress(f"sample anchor {index}: text={text!r} href={href}")
    if not anchors:
        body_text = soup.get_text(" ", strip=True)[:500]
        log_progress(f"no anchors found; body text sample={body_text!r}")


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


def utc_today() -> datetime:
    return datetime.now(timezone.utc)


def dated_key(kind: str, stable_id: str, extension: str, now: datetime | None = None) -> str:
    now = now or utc_today()
    return f"okayama/{kind}/{now:%Y/%m/%d}/{stable_id}.{extension}"


def manifest_key(now: datetime | None = None) -> str:
    now = now or utc_today()
    return f"okayama/html/{now:%Y/%m/%d}/items.json"


async def save_detail_html(page: Page, storage: R2Storage, target: ScrapeTarget) -> str:
    html = await page.content()
    key = dated_key("html", target.stable_id, "html")
    return storage.put_bytes(key, html.encode("utf-8"), "text/html; charset=utf-8")


async def download_pdf_by_click(
    page: Page, storage: R2Storage, target: ScrapeTarget, pdf_stable_id: str | None = None
) -> str | None:
    locator = page.locator(
        'button:has(span.bit__download_btn_font:has-text("3点セット")), '
        'button:has-text("3点セット"), #threeSetPDF'
    ).or_(page.get_by_text(PDF_DOWNLOAD_TEXT)).first
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
        key = dated_key("pdf", pdf_stable_id or target.stable_id, "pdf")
        return storage.put_bytes(key, pdf_bytes, "application/pdf")
    except PlaywrightTimeoutError:
        return None


async def open_detail_by_click(page: Page, target: ScrapeTarget) -> Page:
    if target.sale_unit_id and target.court_id:
        locator = page.locator(
            f'a[onclick*="{target.sale_unit_id}"][onclick*="{target.court_id}"]'
        ).first
    else:
        locator = page.locator(f'a[href="{target.detail_url}"]').first
    log_progress(f"clicking detail link via Playwright: {target.detail_url}")
    await locator.wait_for(state="visible", timeout=10_000)
    try:
        async with page.expect_popup(timeout=10_000) as popup_info:
            await locator.click()
        detail_page = await popup_info.value
        log_progress("detail link opened in a popup/tab")
        await wait_after_navigation_click(detail_page, DETAIL_PAGE_TEXT)
        await log_page_state(detail_page, "after detail popup click")
        return detail_page
    except PlaywrightTimeoutError:
        log_progress("detail link did not open a popup; falling back to same-tab navigation")

    await wait_after_navigation_click(page, DETAIL_PAGE_TEXT)
    await log_page_state(page, "after detail same-tab click")
    return page


def extract_detail_title(html: str, fallback: str | None = None) -> str:
    soup = BeautifulSoup(html, "lxml")
    for selector in ('input[name$=".caseNoLink"]', 'input#caseNoLink'):
        element = soup.select_one(selector)
        if element and element.get("value"):
            return normalize_text(element["value"])
    text = normalize_text(soup.get_text(" ", strip=True))
    match = re.search(r"岡山地方裁判所[^\s　]*[　\s]+令和\d+年[（(][ケヌ][）)]第\d+号", text)
    if match:
        return normalize_text(match.group(0))
    return fallback or ""


def save_manifest(storage: R2Storage, items: list[dict[str, str]]) -> str:
    now = utc_today()
    body = {"date": f"{now:%Y-%m-%d}", "items": items}
    return storage.put_bytes(
        manifest_key(now),
        json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json; charset=utf-8",
    )


async def scrape(
    start_url: str,
    max_details: int | None,
    headless: bool,
    error_artifact_dir: str | None,
) -> None:
    start_url = normalize_start_url(start_url)
    storage = R2Storage(R2Config.from_env())
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(accept_downloads=True, locale="ja-JP")
        page = await context.new_page()
        try:
            log_progress(
                f"starting scrape: start_url={start_url} max_details={max_details}"
            )
            await page.goto(start_url, wait_until="networkidle", timeout=60_000)
            await log_page_state(page, "after initial goto")
            if should_navigate_to_okayama(start_url):
                log_progress(
                    "start URL is a BIT entry page; navigating through region UI"
                )
                await navigate_to_okayama_list(page)
            else:
                log_progress(
                    "start URL is not an entry page; collecting links from current page"
                )
            targets, list_page_url, current_list_page_number = await collect_targets_from_all_pages(
                page, max_details
            )
            log_progress(f"found {len(targets)} detail links across list pages")
            if not targets:
                # Treat an empty list as a scrape failure so GitHub Actions uploads
                # the captured page state instead of silently succeeding.
                raise RuntimeError(
                    f"No detail links found after navigation; current_url={page.url}"
                )
            manifest_items: list[dict[str, str]] = []
            for index, target in enumerate(targets, start=1):
                log_progress(
                    f"processing detail {index}/{len(targets)}: {target.detail_url}"
                )
                if target.page_number != current_list_page_number:
                    await navigate_to_result_page(page, target.page_number)
                    current_list_page_number = target.page_number
                detail_page = await open_detail_by_click(page, target)
                try:
                    detail_html = await detail_page.content()
                    title = extract_detail_title(detail_html, target.title)
                    html_key = storage.put_bytes(
                        dated_key("html", target.stable_id, "html"),
                        detail_html.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                    pdf_key = await download_pdf_by_click(
                        detail_page, storage, target, f"{index:03d}"
                    )
                    if pdf_key is None:
                        log_progress(
                            f"saved detail artifacts: html={html_key}; pdf=NOT_FOUND"
                        )
                    else:
                        manifest_items.append({"title": title, "pdf": pdf_key})
                        log_progress(
                            f"saved detail artifacts: html={html_key}; pdf={pdf_key}"
                        )
                finally:
                    if detail_page is not page:
                        log_progress("closing detail popup/tab")
                        await detail_page.close()
                    else:
                        log_progress("returning from same-tab detail via browser history")
                        await page.go_back(wait_until="networkidle", timeout=60_000)
                        await log_page_state(page, "after returning from same-tab detail")
                        current_list_page_number = target.page_number
            manifest_storage_key = save_manifest(storage, manifest_items)
            log_progress(f"saved JSON manifest: {manifest_storage_key}")
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
        default=env_value_or_default("BIT_START_URL", DEFAULT_START_URL),
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
        default=env_value_or_default(
            "BIT_ERROR_ARTIFACT_DIR", DEFAULT_ERROR_ARTIFACT_DIR
        ),
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
