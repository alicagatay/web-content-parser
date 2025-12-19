"""
Web Content Parser - Fetch markdown versions of web pages and create Google Docs
"""
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
import aiohttp
from tqdm.asyncio import tqdm

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None

# Import Google API modules
from auth import get_docs_service, get_drive_service, find_folder_id
from docs_converter import convert_markdown_to_doc_requests

TIMEOUT_SECS = 30
DRIVE_FOLDER_NAME = "Resources"  # Google Drive folder name for created docs
MAX_CONCURRENCY = 20
FETCH_RETRIES = 2
FETCH_RETRY_BASE_DELAY_SECS = 1.0
MAX_RETRY_ROUNDS = 3


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


def check_existing_doc(drive_service, folder_id: str, title: str) -> str:
    """
    Check if a document with the given title exists in the folder.
    If it exists, return a unique title by appending (2), (3), etc.

    Args:
        drive_service: Authenticated Google Drive service
        folder_id: ID of the folder to search in
        title: Proposed document title

    Returns:
        str: Unique document title
    """
    base_title = title
    counter = 2

    while True:
        # Search for exact title match in the folder
        # Escape single quotes in title for Drive API query
        escaped_title = title.replace("'", "\\'")
        query = f"name='{escaped_title}' and '{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false"
        results = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()

        files = results.get('files', [])

        if not files:
            # Title is unique
            return title

        # Title exists, try next number
        title = f"{base_title} ({counter})"
        counter += 1

        if counter > 100:  # Safety limit
            raise RuntimeError(f"Too many documents with similar titles: {base_title}")


async def create_google_doc(markdown_content: str, title: str, folder_id: str) -> str:
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

        # Check for existing docs and get unique title
        unique_title = check_existing_doc(drive_service, folder_id, title)

        # Create a blank Google Doc without parent (avoids quota issues)
        file_metadata = {
            'name': unique_title,
            'mimeType': 'application/vnd.google-apps.document'
        }

        doc = drive_service.files().create(body=file_metadata, fields='id').execute()
        doc_id = doc['id']

        # Move it to the target folder and transfer ownership to you
        drive_service.files().update(
            fileId=doc_id,
            addParents=folder_id,
            removeParents='root',
            fields='id, parents'
        ).execute()

        # Convert markdown to Docs API requests
        requests = convert_markdown_to_doc_requests(markdown_content)

        # Apply all formatting in a single batchUpdate
        if requests:
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()

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


async def process_url(
    session: aiohttp.ClientSession,
    original_url: str,
    folder_id: str,
    semaphore: asyncio.Semaphore
) -> tuple[str, str, str, bool]:
    """
    Process a single URL: fetch HTML, extract markdown, create Google Doc.

    Returns:
        (original_url, doc_url, doc_title, used_title)
    """
    async with semaphore:
        html, md = await fetch_markdown(session, original_url)

    # Try to extract title: metadata → H1 → URL fallback
    title = extract_title_from_metadata(html, original_url)
    used_metadata = bool(title)

    if not title:
        title = extract_h1_title(md)

    if title:
        doc_title = sanitize_doc_title(title)
    else:
        doc_title = sanitize_doc_title(fallback_name_from_url(original_url))

    # Create Google Doc
    doc_url = await create_google_doc(md, doc_title, folder_id)

    return (original_url, doc_url, doc_title, used_metadata or bool(title))


async def process_url_safe(
    session: aiohttp.ClientSession,
    original_url: str,
    folder_id: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, tuple[str, str, str, bool] | Exception]:
    try:
        return (original_url, await process_url(session, original_url, folder_id, semaphore))
    except Exception as e:
        return (original_url, e)


async def main(urls: list[str]) -> None:
    """
    Main async entry point: process all URLs concurrently with automatic retry.
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

    all_results: dict[str, tuple[str, str, str, bool] | Exception] = {}
    urls_to_process = list(urls)
    retry_round = 0

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        while urls_to_process and retry_round < MAX_RETRY_ROUNDS:
            retry_round += 1

            if retry_round == 1:
                desc = "Fetching & Creating"
            else:
                desc = f"Retry {retry_round - 1}/{MAX_RETRY_ROUNDS - 1}"

            semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
            tasks = [
                asyncio.create_task(process_url_safe(session, u, folder_id, semaphore))
                for u in urls_to_process
            ]

            # Use tqdm to show progress bar as tasks complete
            results: list[tuple[str, tuple[str, str, str, bool] | Exception]] = []
            for task in tqdm.as_completed(tasks, total=len(urls_to_process), desc=desc, unit="doc"):
                results.append(await task)

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
        _, doc_url, doc_title, used_title = outcome
        note = "title" if used_title else "fallback"
        print(f'[OK] || "{doc_title}" || ({note}) || {original_url} -> {doc_url}')

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
