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

# Import Google API modules
from auth import get_docs_service, get_drive_service, find_folder_id
from docs_converter import convert_markdown_to_doc_requests

TIMEOUT_SECS = 30
DRIVE_FOLDER_NAME = "Resources"  # Google Drive folder name for created docs


def into_md_url(url: str) -> str:
    """
    Transform a regular URL into an into.md API URL.

    Example:
        https://example.com/article -> https://into.md/https:/example.com/article
    """
    url = url.strip()

    # If already an into.md URL, return as-is
    if url.startswith("https://into.md/"):
        return url

    # Remove protocol prefix to reconstruct with into.md
    if url.startswith("https://"):
        clean_url = url[len("https://"):]
        return f"https://into.md/https:/{clean_url}"
    elif url.startswith("http://"):
        clean_url = url[len("http://"):]
        return f"https://into.md/http:/{clean_url}"
    else:
        # Assume https if no protocol given
        return f"https://into.md/https:/{url}"


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
    target: str
) -> str:
    """
    Fetch markdown content from a URL.
    """
    async with session.get(target) as resp:
        resp.raise_for_status()
        return await resp.text()


async def process_url(
    session: aiohttp.ClientSession,
    original_url: str,
    folder_id: str
) -> tuple[str, str, str, bool]:
    """
    Process a single URL: fetch markdown, create Google Doc.

    Returns:
        (original_url, doc_url, doc_title, used_title)
    """
    target = into_md_url(original_url)
    md = await fetch_markdown(session, target)

    # Try to extract title from markdown
    title = extract_h1_title(md)
    if title:
        doc_title = sanitize_doc_title(title)
    else:
        doc_title = sanitize_doc_title(fallback_name_from_url(original_url))

    # Create Google Doc
    doc_url = await create_google_doc(md, doc_title, folder_id)

    return (original_url, doc_url, doc_title, bool(title))


async def main(urls: list[str]) -> None:
    """
    Main async entry point: process all URLs concurrently.
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
    headers = {"User-Agent": "web-content-parser/1.0"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        tasks = [process_url(session, u, folder_id) for u in urls]

        # Use tqdm to show progress bar as tasks complete
        results = []
        for coro in tqdm.as_completed(tasks, total=len(urls), desc="Fetching & Creating", unit="doc"):
            results.append(await coro)

    # Report results
    ok = 0
    print()  # Add newline after progress bar
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            print(f"[FAIL] {url} -> {result}", file=sys.stderr)
        else:
            ok += 1
            original_url, doc_url, doc_title, used_title = result
            note = "title" if used_title else "fallback"
            print(f'[OK] || "{doc_title}" || ({note}) || {original_url} -> {doc_url}')

    print(f"\n✓ Done: {ok}/{len(urls)} succeeded.")


if __name__ == "__main__":
    urls = sys.argv[1:]
    if not urls:
        print("Usage: python fetch_markdown.py <url1> <url2> ...", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print('  python fetch_markdown.py "https://example.com/article"', file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(urls))
