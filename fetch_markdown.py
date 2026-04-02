"""
Web Content Parser - Fetch markdown versions of web pages and create Google Docs
"""
import asyncio
import argparse
import sys
from datetime import datetime
from urllib.parse import urlparse

import aiohttp
from tqdm.asyncio import tqdm
from playwright.async_api import async_playwright, BrowserContext

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None

from html_cleaner import filter_short_blocks
from auth import get_docs_service, get_drive_service, find_folder_id
from google_drive import (
    sanitize_doc_title,
    _build_doc_title_cache_sync,
    create_google_doc,
)
from title_extractor import (
    extract_title_from_metadata,
    extract_h1_title,
    fallback_name_from_url,
)
from extraction import (
    ExtractionConfig,
    fetch_html,
    extract_with_multi_div,
    extract_with_css_selectors,
    apply_extraction_pipeline,
)
from playwright_fetch import fetch_with_playwright

TIMEOUT_SECS = 30
DRIVE_FOLDER_NAME = "Resources"  # Google Drive folder name for created docs
MAX_CONCURRENCY = 15
MAX_RETRY_ROUNDS = 3

# Playwright settings
PLAYWRIGHT_CONCURRENCY = 15


async def process_url(
    session: aiohttp.ClientSession,
    original_url: str,
    folder_id: str,
    semaphore: asyncio.Semaphore,
    pw_context: BrowserContext | None,
    playwright_sem: asyncio.Semaphore | None,
    doc_cache: dict[str, tuple[str, bool]] | None,
    cache_lock: asyncio.Lock | None,
    config: ExtractionConfig,
) -> tuple[str, str, str, bool, str, int]:
    """
    Process a single URL: fetch HTML, extract markdown, create Google Doc.

    Returns:
        (original_url, doc_url, doc_title, used_title, extraction_method, content_length)
    """
    html = None
    md = None
    extraction_method = ""
    content_length = 0

    def run_extractions_on_html(
        source_name: str,
        raw_html: str,
        url: str,
    ) -> list[tuple[str, str, str, int]]:
        """
        Run all extraction strategies on HTML (raw and cleaned variants).
        Returns list of (method_name, html, markdown, length) tuples.
        """
        results = []

        # Apply cleaning pipeline
        if config.enable_cleaning or config.enable_pruning:
            cleaned_html, pruned_html = apply_extraction_pipeline(raw_html, url, config)
        else:
            cleaned_html = raw_html
            pruned_html = raw_html

        # Strategy 1: trafilatura on raw HTML
        try:
            md_raw_traf = trafilatura.extract(
                raw_html,
                include_comments=False,
                include_tables=True,
                include_links=True,
                favor_recall=True,
                output_format="markdown",
                url=url,
            )
            if md_raw_traf:
                results.append((f"{source_name}+raw+trafilatura", raw_html, md_raw_traf, len(md_raw_traf.strip())))
        except Exception:
            pass

        # Strategy 2: trafilatura on cleaned HTML
        if config.enable_cleaning:
            try:
                md_clean_traf = trafilatura.extract(
                    cleaned_html,
                    include_comments=False,
                    include_tables=True,
                    include_links=True,
                    favor_recall=True,
                    output_format="markdown",
                    url=url,
                )
                if md_clean_traf:
                    results.append((f"{source_name}+cleaned+trafilatura", raw_html, md_clean_traf, len(md_clean_traf.strip())))
            except Exception:
                pass

        # Strategy 3: trafilatura on pruned HTML
        if config.enable_pruning:
            try:
                md_prune_traf = trafilatura.extract(
                    pruned_html,
                    include_comments=False,
                    include_tables=True,
                    include_links=True,
                    favor_recall=True,
                    output_format="markdown",
                    url=url,
                )
                if md_prune_traf:
                    results.append((f"{source_name}+pruned+trafilatura", raw_html, md_prune_traf, len(md_prune_traf.strip())))
            except Exception:
                pass

        # Strategy 4: multi-div on raw HTML
        try:
            md_multi = extract_with_multi_div(raw_html)
            if md_multi:
                results.append((f"{source_name}+raw+multi-div", raw_html, md_multi, len(md_multi.strip())))
        except Exception:
            pass

        # Strategy 5: multi-div on cleaned HTML
        if config.enable_cleaning:
            try:
                md_multi_clean = extract_with_multi_div(cleaned_html)
                if md_multi_clean:
                    results.append((f"{source_name}+cleaned+multi-div", raw_html, md_multi_clean, len(md_multi_clean.strip())))
            except Exception:
                pass

        # Strategy 6: CSS-targeted extraction on cleaned HTML
        try:
            md_css = extract_with_css_selectors(
                cleaned_html if config.enable_cleaning else raw_html,
                url,
            )
            if md_css:
                results.append((f"{source_name}+css-targeted", raw_html, md_css, len(md_css.strip())))
        except Exception:
            pass

        return results

    def select_best_result(results: list[tuple[str, str, str, int]]) -> tuple[str, str, str, int] | None:
        """Select the best extraction result by content length."""
        if not results:
            return None
        return max(results, key=lambda x: x[3])

    # Fetch HTML from both sources
    aiohttp_html = None
    playwright_html = None

    if pw_context and playwright_sem:
        # Launch both fetch methods in parallel
        async def fetch_aiohttp():
            try:
                return await fetch_html(session, original_url)
            except Exception as e:
                return e

        async def fetch_playwright_wrapper():
            try:
                async with playwright_sem:
                    return await fetch_with_playwright(pw_context, original_url)
            except Exception as e:
                return e

        # Run both in parallel
        aiohttp_result, playwright_result = await asyncio.gather(
            fetch_aiohttp(),
            fetch_playwright_wrapper()
        )

        # Extract HTML from results
        if not isinstance(aiohttp_result, Exception):
            aiohttp_html = aiohttp_result

        if not isinstance(playwright_result, Exception):
            playwright_html = playwright_result

        if not aiohttp_html and not playwright_html:
            # Both methods failed -- raise the first available error
            aiohttp_error = aiohttp_result if isinstance(aiohttp_result, Exception) else None
            playwright_error = playwright_result if isinstance(playwright_result, Exception) else None
            raise aiohttp_error or playwright_error or RuntimeError("Both aiohttp and Playwright failed to extract content")
    else:
        # Playwright not available, use aiohttp only
        aiohttp_html = await fetch_html(session, original_url)

    # Run extraction on all available HTML sources
    all_results = []
    if aiohttp_html:
        all_results.extend(run_extractions_on_html("aiohttp", aiohttp_html, original_url))
    if playwright_html:
        all_results.extend(run_extractions_on_html("playwright", playwright_html, original_url))

    # Select best result
    result = select_best_result(all_results)

    if not result:
        raise RuntimeError("Content extraction failed")

    extraction_method, html, md, content_length = result

    if not md:
        raise RuntimeError("Content extraction failed")

    # Apply final markdown filtering (remove short blocks)
    if config.min_words > 0:
        md = filter_short_blocks(md, min_words=config.min_words)
        content_length = len(md.strip())

    # Try to extract title: metadata → H1 → URL fallback
    title = extract_title_from_metadata(html, original_url)
    used_metadata = bool(title)

    if not title:
        title = extract_h1_title(md)

    if title:
        doc_title = sanitize_doc_title(title)
    else:
        doc_title = sanitize_doc_title(fallback_name_from_url(original_url))

    # Add source URL and timestamp at top as plain text lines
    timestamp = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    md_with_source = (
        f"Source: [{original_url}]({original_url})\n\n"
        f"Date of Addition: {timestamp}\n\n"
        f"{md}"
    )

    # Create Google Doc
    doc_url = await create_google_doc(
        md_with_source,
        doc_title,
        folder_id,
        doc_cache=doc_cache,
        cache_lock=cache_lock
    )

    return (original_url, doc_url, doc_title, used_metadata or bool(title), extraction_method, content_length)


async def process_url_safe(
    session: aiohttp.ClientSession,
    original_url: str,
    folder_id: str,
    semaphore: asyncio.Semaphore,
    pw_context: BrowserContext | None,
    playwright_sem: asyncio.Semaphore | None,
    doc_cache: dict[str, tuple[str, bool]] | None,
    cache_lock: asyncio.Lock | None,
    config: ExtractionConfig,
) -> tuple[str, tuple[str, str, str, bool, str, int] | Exception]:
    try:
        async with semaphore:
            return (original_url, await process_url(
                session, original_url, folder_id, semaphore,
                pw_context, playwright_sem,
                doc_cache, cache_lock, config
            ))
    except Exception as e:
        return (original_url, e)


async def main(urls: list[str], config: ExtractionConfig) -> None:
    """
    Main async entry point: process all URLs concurrently with automatic retry.

    Args:
        urls: List of URLs to process
        config: Extraction configuration
    """
    # Warm up credentials cache (authenticates once, reused by all service builds)
    try:
        drive_service = get_drive_service()
    except Exception as e:
        print(f"\n❌ Error creating Google API services: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        # Find the Resources folder
        print("Locating Google Drive folder...", file=sys.stderr)
        folder_id = find_folder_id(DRIVE_FOLDER_NAME, drive_service=drive_service)
        print(f"✓ Found '{DRIVE_FOLDER_NAME}' folder\n", file=sys.stderr)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECS)
    # Use browser-like headers for better compatibility with target sites.
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    all_results: dict[str, tuple[str, str, str, bool, bool] | Exception] = {}
    urls_to_process = list(urls)
    retry_round = 0

    # Build a doc title cache once per run to avoid repeated recursive searches
    try:
        doc_cache = await asyncio.to_thread(
            _build_doc_title_cache_sync, drive_service, folder_id
        )
        cache_lock = asyncio.Lock()
        print(f"✓ Cached {len(doc_cache)} existing docs from Drive", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to build doc cache, falling back to recursive lookups: {e}", file=sys.stderr)
        doc_cache = None
        cache_lock = None

    # Launch Playwright browser with shared context
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        pw_context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York',
            permissions=['geolocation'],
            java_script_enabled=True,
        )
        await pw_context.set_extra_http_headers({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
        })
        await pw_context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        playwright_sem = asyncio.Semaphore(PLAYWRIGHT_CONCURRENCY)

        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            while urls_to_process and retry_round < MAX_RETRY_ROUNDS:
                retry_round += 1

                if retry_round == 1:
                    desc = "Fetching & Creating"
                else:
                    desc = f"Retry {retry_round - 1}/{MAX_RETRY_ROUNDS - 1}"

                semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
                tasks = [
                    asyncio.create_task(process_url_safe(
                        session, u, folder_id, semaphore,
                        pw_context, playwright_sem,
                        doc_cache, cache_lock, config
                    ))
                    for u in urls_to_process
                ]

                # Run all tasks concurrently with progress bar
                results: list[tuple[str, tuple[str, str, str, bool, str, int] | Exception]] = []
                with tqdm(total=len(urls_to_process), desc=desc, unit="doc") as pbar:
                    for coro in asyncio.as_completed(tasks):
                        results.append(await coro)
                        pbar.update(1)

                # Update all_results and collect failed URLs for retry
                failed_urls = []
                for original_url, outcome in results:
                    if isinstance(outcome, Exception):
                        # Only retry if not already succeeded
                        if original_url not in all_results or isinstance(all_results.get(original_url), Exception):
                            all_results[original_url] = outcome
                            failed_urls.append(original_url)
                    else:
                        # Success - update result
                        all_results[original_url] = outcome

                # Prepare for next retry round
                urls_to_process = failed_urls

                # Add small delay before retry to avoid hammering sites
                if urls_to_process and retry_round < MAX_RETRY_ROUNDS:
                    await asyncio.sleep(2)

        # Close context and browser
        await pw_context.close()
        await browser.close()

    # Report final results
    ok = 0
    failed = 0
    print()  # Add newline after progress bar

    for original_url in urls:
        outcome = all_results.get(original_url)
        if isinstance(outcome, Exception):
            failed += 1
            print(f"[FAIL] || {original_url} || {outcome}", file=sys.stderr)
            continue

        ok += 1
        _, doc_url, doc_title, used_title, extraction_method, content_length = outcome

        # Format extraction method for display
        method_display = extraction_method.replace("+", " with ")

        print(f'[OK] || [{method_display}] || {content_length:,} chars || "{doc_title}" || {original_url} -> {doc_url}')

    print(f"\n✓ Done: {ok}/{len(urls)} succeeded, {failed} failed.")
    if failed > 0 and retry_round >= MAX_RETRY_ROUNDS:
        print(f"  (Failed URLs were retried {MAX_RETRY_ROUNDS - 1} times)", file=sys.stderr)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch web pages, extract content as markdown, and create Google Docs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  web-content-parser                          # interactive mode
  web-content-parser "https://example.com/article"
  web-content-parser --no-clean "https://example.com/article"
  web-content-parser --pruning-threshold 0.6 --min-words 30 url1 url2

Configuration:
  The extraction pipeline cleans HTML and prunes low-quality content by default.
  Use --no-clean and --no-prune to disable these features for faster processing.
  Run without URLs to enter interactive mode (enter URLs one at a time).
        """
    )

    parser.add_argument(
        'urls',
        nargs='*',
        help='URLs to fetch and convert (omit to use interactive mode)'
    )

    # Cleaning options
    parser.add_argument(
        '--no-clean',
        action='store_true',
        help='Disable HTML cleaning (removes nav, footer, ads, etc.)'
    )

    parser.add_argument(
        '--no-prune',
        action='store_true',
        help='Disable content pruning (scores and removes low-quality nodes)'
    )

    # Threshold options
    parser.add_argument(
        '--pruning-threshold',
        type=float,
        default=0.48,
        metavar='FLOAT',
        help='Pruning score threshold (0.0-1.0). Lower keeps more content. Default: 0.48'
    )

    parser.add_argument(
        '--min-words',
        type=int,
        default=0,
        metavar='INT',
        help='Minimum words per markdown block. Default: 0 (disabled)'
    )

    parser.add_argument(
        '--min-word-threshold',
        type=int,
        default=10,
        metavar='INT',
        help='Minimum words for pruning filter. Default: 10'
    )

    # Advanced options
    parser.add_argument(
        '--no-dynamic-threshold',
        action='store_true',
        help='Disable dynamic threshold adjustment in pruning'
    )

    return parser.parse_args()


def _validate_url(url: str) -> tuple[bool, str]:
    """Minimal URL validation. Auto-prepends https:// if no scheme."""
    url = url.strip()
    if not url:
        return False, url
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return False, url
    return True, url


def interactive_prompt() -> list[str]:
    """Interactive URL entry mode. Returns list of URLs."""
    print("\n" + "=" * 44, file=sys.stderr)
    print("  Web Content Parser - Interactive Mode", file=sys.stderr)
    print("=" * 44, file=sys.stderr)
    print("\nEnter URLs one at a time, then type \"ready\" to start.", file=sys.stderr)
    print("Press Ctrl+C twice to exit.\n", file=sys.stderr)

    import time

    urls = []
    last_ctrl_c = 0.0
    while True:
        try:
            raw = input(f"[{len(urls) + 1}] Enter a URL (or \"ready\" to start): ")
            raw = raw.strip()

            if not raw:
                continue

            if raw.lower() == "ready":
                break

            valid, normalized = _validate_url(raw)
            if not valid:
                print(f"  Invalid URL: \"{raw}\". Please enter a valid URL.", file=sys.stderr)
                continue

            urls.append(normalized)
            print(f"  Added: {normalized}", file=sys.stderr)

        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_ctrl_c < 1.0:
                print("\nExiting.", file=sys.stderr)
                sys.exit(0)
            last_ctrl_c = now
            print("\nPress Ctrl+C again to exit.", file=sys.stderr)
        except EOFError:
            print("\nExiting.", file=sys.stderr)
            sys.exit(0)

    if not urls:
        print("No URLs entered. Exiting.", file=sys.stderr)
        sys.exit(0)

    print(f"\nURLs to process ({len(urls)}):", file=sys.stderr)
    for i, url in enumerate(urls, 1):
        print(f"  {i}. {url}", file=sys.stderr)
    print(file=sys.stderr)

    return urls


if __name__ == "__main__":
    args = parse_args()

    # Determine URLs: from args or interactive mode
    if args.urls:
        urls = args.urls
    elif sys.stdin.isatty():
        urls = interactive_prompt()
    else:
        print("Error: No URLs provided. In non-interactive mode, pass URLs as arguments.", file=sys.stderr)
        sys.exit(1)

    # Configure extraction settings from CLI args
    extraction_config = ExtractionConfig(
        enable_cleaning=not args.no_clean,
        enable_pruning=not args.no_prune,
        pruning_threshold=args.pruning_threshold,
        min_words=args.min_words,
        min_word_threshold=args.min_word_threshold,
        dynamic_threshold=not args.no_dynamic_threshold,
    )

    # Print config summary
    print("Extraction config:", file=sys.stderr)
    print(f"  Cleaning: {'enabled' if extraction_config.enable_cleaning else 'disabled'}", file=sys.stderr)
    print(f"  Pruning: {'enabled' if extraction_config.enable_pruning else 'disabled'}", file=sys.stderr)
    if extraction_config.enable_pruning:
        print(f"  Pruning threshold: {extraction_config.pruning_threshold}", file=sys.stderr)
    print(f"  Min words per block: {extraction_config.min_words}", file=sys.stderr)
    print(file=sys.stderr)

    asyncio.run(main(urls, extraction_config))
