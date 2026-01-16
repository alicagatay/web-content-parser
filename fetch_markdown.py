"""
Web Content Parser - Fetch markdown versions of web pages and create Google Docs
"""
import asyncio
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
import aiohttp
from tqdm.asyncio import tqdm
from playwright.async_api import async_playwright, Browser, TimeoutError as PlaywrightTimeoutError
from lxml import html as lxml_html, etree
import html2text

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None

# Import Google API modules
from auth import get_docs_service, get_drive_service, find_folder_id
from docs_converter import convert_markdown_to_doc_requests

TIMEOUT_SECS = 30
DRIVE_FOLDER_NAME = "Resources"  # Google Drive folder name for created docs
MAX_CONCURRENCY = 15
FETCH_RETRIES = 2
FETCH_RETRY_BASE_DELAY_SECS = 1.0
MAX_RETRY_ROUNDS = 3

# Playwright settings
PLAYWRIGHT_CONCURRENCY = 15
PLAYWRIGHT_TIMEOUT = 45000  # 45 seconds (in milliseconds)
PLAYWRIGHT_RETRIES = 3      # Playwright fetch retries


def sanitize_doc_title(name: str) -> str:
    """
    Sanitize a string to be a valid Google Docs title.
    Similar to sanitize_filename but for document titles.
    """
    name = name.strip()
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name)
    # Remove problematic characters
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    # Limit length
    name = name[:200]
    return name or "Untitled"


def _find_existing_doc_id_recursive_sync(
    drive_service,
    root_folder_id: str,
    title: str
) -> str | None:
    """
    Synchronous helper: Recursively search for a document by title in a folder
    and all nested subfolders.

    Args:
        drive_service: Authenticated Google Drive service
        root_folder_id: ID of the root folder to search in
        title: Document title to find

    Returns:
        str | None: Document ID if found, otherwise None
    """
    escaped_title = title.replace("'", "\\'")
    doc_query_template = (
        "name='{title}' and '{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.document' and trashed=false"
    )
    folder_query_template = (
        "'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )

    queue = deque([root_folder_id])
    visited = set()

    while queue:
        folder_id = queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        # Check for a matching document in this folder
        doc_query = doc_query_template.format(title=escaped_title, folder_id=folder_id)
        doc_results = drive_service.files().list(
            q=doc_query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()

        doc_files = doc_results.get('files', [])
        if doc_files:
            return doc_files[0].get('id')

        # Queue subfolders
        page_token = None
        while True:
            folder_query = folder_query_template.format(folder_id=folder_id)
            folder_results = drive_service.files().list(
                q=folder_query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for folder in folder_results.get('files', []):
                folder_id_child = folder.get('id')
                if folder_id_child:
                    queue.append(folder_id_child)

            page_token = folder_results.get('nextPageToken')
            if not page_token:
                break

    return None


def _build_doc_title_cache_sync(
    drive_service,
    root_folder_id: str
) -> dict[str, tuple[str, bool]]:
    """
    Synchronous helper: Build a cache of document titles to IDs by
    recursively traversing the root folder and all subfolders.

    Args:
        drive_service: Authenticated Google Drive service
        root_folder_id: ID of the root folder to search in

    Returns:
        dict[str, tuple[str, bool]]: Mapping of document title -> (document ID, created_this_run)
    """
    doc_cache: dict[str, tuple[str, bool]] = {}
    folder_query_template = (
        "'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    doc_query_template = (
        "'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.document' and trashed=false"
    )

    queue = deque([root_folder_id])
    visited = set()

    while queue:
        folder_id = queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        # List documents in this folder
        page_token = None
        while True:
            doc_query = doc_query_template.format(folder_id=folder_id)
            doc_results = drive_service.files().list(
                q=doc_query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for doc in doc_results.get('files', []):
                doc_id = doc.get('id')
                doc_name = doc.get('name')
                if doc_id and doc_name and doc_name not in doc_cache:
                    doc_cache[doc_name] = (doc_id, False)

            page_token = doc_results.get('nextPageToken')
            if not page_token:
                break

        # Queue subfolders
        page_token = None
        while True:
            folder_query = folder_query_template.format(folder_id=folder_id)
            folder_results = drive_service.files().list(
                q=folder_query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for folder in folder_results.get('files', []):
                folder_id_child = folder.get('id')
                if folder_id_child:
                    queue.append(folder_id_child)

            page_token = folder_results.get('nextPageToken')
            if not page_token:
                break

    return doc_cache


async def create_google_doc(
    markdown_content: str,
    title: str,
    folder_id: str,
    doc_cache: dict[str, tuple[str, bool]] | None = None,
    cache_lock: asyncio.Lock | None = None
) -> str:
    """
    Create a Google Doc from markdown content

    Args:
        markdown_content: Raw markdown text
        title: Document title
        folder_id: Google Drive folder ID

    Returns:
        str: URL of the created document
    """
    try:
        docs_service = get_docs_service()
        drive_service = get_drive_service()

        existing_doc_id = None
        created_this_run = False
        if doc_cache is not None:
            if cache_lock:
                async with cache_lock:
                    cached = doc_cache.get(title)
            else:
                cached = doc_cache.get(title)
            if cached:
                existing_doc_id, created_this_run = cached
        else:
            # Fallback: recursive search for existing doc by title
            existing_doc_id = await asyncio.to_thread(
                _find_existing_doc_id_recursive_sync, drive_service, folder_id, title
            )

        if existing_doc_id and not created_this_run:
            # Document already exists from a previous run, return its URL
            print(f"↺ Existing doc found for '{title}', reusing.", file=sys.stderr)
            return f"https://docs.google.com/document/d/{existing_doc_id}/edit"

        if existing_doc_id and created_this_run:
            # Reuse the doc created in this run (likely from a previous failed attempt)
            doc_id = existing_doc_id
        else:
            # Create a blank Google Doc without parent (avoids quota issues)
            file_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document'
            }

            doc = await asyncio.to_thread(
                lambda: drive_service.files().create(body=file_metadata, fields='id').execute()
            )
            doc_id = doc['id']

        # Update cache immediately to prevent duplicate docs on retries
        if doc_cache is not None:
            if cache_lock:
                async with cache_lock:
                    doc_cache[title] = (doc_id, True)
            else:
                doc_cache[title] = (doc_id, True)

        if not (existing_doc_id and created_this_run):
            # Move it to the target folder and transfer ownership to you
            await asyncio.to_thread(
                lambda: drive_service.files().update(
                    fileId=doc_id,
                    addParents=folder_id,
                    removeParents='root',
                    fields='id, parents'
                ).execute()
            )

        # Convert markdown to Docs API requests
        requests = convert_markdown_to_doc_requests(markdown_content, doc_title=title)

        # Apply all formatting in a single batchUpdate
        if requests:
            await asyncio.to_thread(
                lambda: docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={'requests': requests}
                ).execute()
            )

        # Update cache with the newly created doc
        if doc_cache is not None:
            if cache_lock:
                async with cache_lock:
                    doc_cache[title] = (doc_id, False)
            else:
                doc_cache[title] = (doc_id, False)

        # Return shareable URL
        return f"https://docs.google.com/document/d/{doc_id}/edit"

    except Exception as e:
        raise RuntimeError(f"Failed to create Google Doc: {e}")


def extract_title_from_metadata(html: str, url: str) -> str | None:
    """
    Extract title from page metadata using trafilatura.

    Returns:
        The title from metadata, or None if not found
    """
    if trafilatura is None:
        return None

    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
        if metadata and metadata.title:
            return metadata.title.strip()
    except Exception:
        pass
    return None


def extract_title_from_metadata(html: str, url: str) -> str | None:
    """
    Extract title from page metadata using trafilatura.

    Returns:
        The title from metadata, or None if not found
    """
    if trafilatura is None:
        return None

    try:
        metadata = trafilatura.extract_metadata(html, default_url=url)
        if metadata and metadata.title:
            return metadata.title.strip()
    except Exception:
        pass
    return None


def extract_h1_title(markdown: str) -> str | None:
    """
    Extract the first H1 heading from markdown content.

    Returns:
        The title text without the # prefix, or None if no H1 found
    """
    for line in markdown.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            return match.group(1)
    return None


def fallback_name_from_url(original_url: str) -> str:
    """
    Generate a filename from the URL structure when no title is found.
    """
    # Ensure URL has a scheme for parsing
    if "://" not in original_url:
        original_url = "https://" + original_url

    parsed = urlparse(original_url)
    base = (parsed.netloc + parsed.path).strip("/").replace("/", " - ")
    base = re.sub(r"[^A-Za-z0-9._ -]+", "", base).strip()
    return base or "page"


def unique_path(path: Path) -> Path:
    """
    Generate a unique file path by appending (2), (3), etc. if needed.
    """
    if not path.exists():
        return path

    stem, suffix = path.stem, path.suffix
    for i in range(2, 10_000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not find unique filename for {path}")


async def fetch_markdown(
    session: aiohttp.ClientSession,
    url: str
) -> tuple[str, str]:
    """
    Fetch HTML and extract markdown content using trafilatura.

    Returns:
        tuple[html, markdown]: Raw HTML and extracted markdown
    """
    if trafilatura is None:
        raise RuntimeError(
            "trafilatura is not installed. Install it with: pip install trafilatura"
        )

    # Ensure URL has scheme
    url = url.strip()
    if "://" not in url:
        url = "https://" + url

    last_error: Exception | None = None

    for attempt in range(FETCH_RETRIES + 1):
        try:
            async with session.get(url) as resp:
                resp.raise_for_status()
                html = await resp.text()

            # Extract markdown using trafilatura
            md = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=True,
                favor_recall=True,
                output_format="markdown",
                url=url,
            )

            if not md or not md.strip():
                raise RuntimeError("Content extraction produced empty result")

            return (html, md)

        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            last_error = e
            if attempt >= FETCH_RETRIES:
                break

            await asyncio.sleep(FETCH_RETRY_BASE_DELAY_SECS * (2 ** attempt))

    raise RuntimeError(f"Failed to extract content from {url}: {last_error}")


def extract_with_multi_div(html: str) -> str | None:
    """
    Extract content by finding and combining multiple content divs.
    This handles sites like Ars Technica that split articles across multiple divs.

    Args:
        html: Raw HTML content

    Returns:
        Markdown string or None if extraction fails
    """
    try:
        tree = lxml_html.fromstring(html)

        # Common content container patterns
        patterns = [
            '//div[contains(@class, "post-content")]',
            '//div[contains(@class, "article-content")]',
            '//div[contains(@class, "article-body")]',
            '//div[contains(@class, "entry-content")]',
            '//article//p/..',  # Parent of paragraphs within article tags
        ]

        best_result = None
        best_length = 0

        for pattern in patterns:
            try:
                content_divs = tree.xpath(pattern)

                if len(content_divs) > 1:  # Only worth it if multiple divs found
                    # Combine all matching divs
                    all_parts = []
                    for div in content_divs:
                        html_part = etree.tostring(div, encoding='unicode')
                        all_parts.append(html_part)

                    combined = "\n".join(all_parts)

                    # Convert to markdown
                    h = html2text.HTML2Text()
                    h.ignore_links = False
                    h.body_width = 0
                    md = h.handle(combined)

                    if md and len(md.strip()) > best_length:
                        best_result = md.strip()
                        best_length = len(best_result)

            except Exception:
                continue

        return best_result

    except Exception:
        return None


async def fetch_with_playwright(
    browser: Browser,
    url: str
) -> str:
    """
    Fetch page content using Playwright (headless browser).
    This handles JavaScript-rendered content.

    Args:
        browser: Playwright browser instance
        url: Target URL

    Returns:
        str: Rendered HTML after JavaScript execution
    """
    last_error: Exception | None = None

    for attempt in range(PLAYWRIGHT_RETRIES + 1):
        context = None
        try:
            # Create new browser context with anti-detection settings
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1920, 'height': 1080},
                # Additional stealth settings
                locale='en-US',
                timezone_id='America/New_York',
                permissions=['geolocation'],
                java_script_enabled=True,
                bypass_csp=False,  # Don't bypass to seem more like real browser
            )

            # Add extra headers to look more like a real browser
            await context.set_extra_http_headers({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
            })

            page = await context.new_page()

            # Hide webdriver property
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            # Navigate and wait for network to be mostly idle
            await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")

            # Additional wait for dynamic content to load
            # Try waiting for article content (common selectors)
            try:
                await page.wait_for_selector('article, main, [role="main"], .article-content', timeout=5000)
            except:
                # If specific selectors don't exist, just wait a bit more
                await asyncio.sleep(2)

            # Give extra time for lazy-loaded content
            await asyncio.sleep(1)

            # Get fully rendered HTML
            html = await page.content()

            # Cleanup
            await context.close()

            return html

        except (PlaywrightTimeoutError, Exception) as e:
            last_error = e
            if context:
                try:
                    await context.close()
                except:
                    pass
            if attempt >= PLAYWRIGHT_RETRIES:
                break
            await asyncio.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Playwright fetch failed after {PLAYWRIGHT_RETRIES + 1} attempts: {last_error}")


async def process_url(
    session: aiohttp.ClientSession,
    original_url: str,
    folder_id: str,
    semaphore: asyncio.Semaphore,
    browser: Browser | None,
    playwright_sem: asyncio.Semaphore | None,
    doc_cache: dict[str, tuple[str, bool]] | None,
    cache_lock: asyncio.Lock | None
) -> tuple[str, str, str, bool, str, int]:
    """
    Process a single URL: fetch HTML, extract markdown, create Google Doc.
    Always runs BOTH aiohttp and Playwright in parallel for maximum content extraction.

    Returns:
        (original_url, doc_url, doc_title, used_title, extraction_method, content_length)
    """
    html = None
    md = None
    extraction_method = ""
    content_length = 0

    # Always run BOTH aiohttp and Playwright in parallel (unless Playwright unavailable)
    if browser and playwright_sem:
        # Launch both fetch methods in parallel
        async def fetch_aiohttp():
            try:
                return await fetch_markdown(session, original_url)
            except Exception as e:
                return (None, None, e)

        async def fetch_playwright_wrapper():
            try:
                async with playwright_sem:
                    return await fetch_with_playwright(browser, original_url)
            except Exception as e:
                return (None, e)

        # Run both in parallel
        aiohttp_result, playwright_html_result = await asyncio.gather(
            fetch_aiohttp(),
            fetch_playwright_wrapper()
        )

        # Process aiohttp results - track both trafilatura and multi-div separately
        aiohttp_results = []
        if len(aiohttp_result) == 2:  # Success case
            aiohttp_html, aiohttp_md_traf = aiohttp_result
            if aiohttp_md_traf:
                aiohttp_results.append(("aiohttp+trafilatura", aiohttp_html, aiohttp_md_traf, len(aiohttp_md_traf.strip())))

            # Try multi-div extraction on aiohttp HTML
            aiohttp_md_multi = extract_with_multi_div(aiohttp_html)
            if aiohttp_md_multi:
                aiohttp_results.append(("aiohttp+multi-div", aiohttp_html, aiohttp_md_multi, len(aiohttp_md_multi.strip())))

        # Process Playwright results - track both trafilatura and multi-div separately
        playwright_results = []
        if not isinstance(playwright_html_result, tuple):  # Success case (got HTML string)
            playwright_html = playwright_html_result

            # Extract using trafilatura
            pw_md_traf = trafilatura.extract(
                playwright_html,
                include_comments=False,
                include_tables=True,
                include_links=True,
                favor_recall=True,
                output_format="markdown",
                url=original_url,
            )
            if pw_md_traf:
                playwright_results.append(("playwright+trafilatura", playwright_html, pw_md_traf, len(pw_md_traf.strip())))

            # Extract using multi-div
            pw_md_multi = extract_with_multi_div(playwright_html)
            if pw_md_multi:
                playwright_results.append(("playwright+multi-div", playwright_html, pw_md_multi, len(pw_md_multi.strip())))

        # Compare ALL results (all 4 possible combinations) and use the longest
        all_results = aiohttp_results + playwright_results

        if not all_results:
            # Both methods failed
            if len(aiohttp_result) == 3:  # aiohttp exception
                raise aiohttp_result[2]
            elif isinstance(playwright_html_result, tuple):  # Playwright exception
                raise playwright_html_result[1]
            else:
                raise RuntimeError("Both aiohttp and Playwright failed to extract content")

        # Use the longest result
        extraction_method, html, md, content_length = max(all_results, key=lambda x: x[3])

    else:
        # Playwright not available, use aiohttp only
        html, md_traf = await fetch_markdown(session, original_url)

        # Try both trafilatura and multi-div
        results = []
        if md_traf:
            results.append(("aiohttp+trafilatura", md_traf, len(md_traf.strip())))

        md_multi = extract_with_multi_div(html)
        if md_multi:
            results.append(("aiohttp+multi-div", md_multi, len(md_multi.strip())))

        if not results:
            raise RuntimeError("Content extraction failed")

        # Use whichever got more content
        extraction_method, md, content_length = max(results, key=lambda x: x[2])

    # Try to extract title: metadata → H1 → URL fallback
    title = extract_title_from_metadata(html, original_url)
    used_metadata = bool(title)

    if not title:
        title = extract_h1_title(md)

    if title:
        doc_title = sanitize_doc_title(title)
    else:
        doc_title = sanitize_doc_title(fallback_name_from_url(original_url))

    # Add source URL at top as a clickable link
    md_with_source = f"Source: [{original_url}]({original_url})\n\n{md}"

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
    browser: Browser | None,
    playwright_sem: asyncio.Semaphore | None,
    doc_cache: dict[str, tuple[str, bool]] | None,
    cache_lock: asyncio.Lock | None
) -> tuple[str, tuple[str, str, str, bool, str, int] | Exception]:
    try:
        async with semaphore:
            return (original_url, await process_url(
                session, original_url, folder_id, semaphore,
                browser, playwright_sem, doc_cache, cache_lock
            ))
    except Exception as e:
        return (original_url, e)


async def main(urls: list[str]) -> None:
    """
    Main async entry point: process all URLs concurrently with automatic retry.

    Args:
        urls: List of URLs to process
    """
    try:
        # Find the Resources folder
        print("Locating Google Drive folder...", file=sys.stderr)
        folder_id = find_folder_id(DRIVE_FOLDER_NAME)
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
        drive_service = get_drive_service()
        doc_cache = await asyncio.to_thread(
            _build_doc_title_cache_sync, drive_service, folder_id
        )
        cache_lock = asyncio.Lock()
        print(f"✓ Cached {len(doc_cache)} existing docs from Drive", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to build doc cache, falling back to recursive lookups: {e}", file=sys.stderr)
        doc_cache = None
        cache_lock = None

    # Launch Playwright browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
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
                        browser, playwright_sem, doc_cache, cache_lock
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

        # Close browser
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


if __name__ == "__main__":
    urls = sys.argv[1:]

    if not urls:
        print("Usage: python fetch_markdown.py <url1> <url2> ...", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print('  python fetch_markdown.py "https://example.com/article"', file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(urls))
