"""
Web Content Parser - Fetch markdown versions of web pages using into.md
"""
import asyncio
import configparser
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
import aiohttp
from tqdm.asyncio import tqdm

# Load configuration
CONFIG_FILE = Path(__file__).parent / "config.ini"
config = configparser.ConfigParser()

if CONFIG_FILE.exists():
    config.read(CONFIG_FILE)
    OUT_DIR = Path(config.get("Paths", "output_directory", fallback="markdown"))
else:
    print("Warning: config.ini not found. Using default 'markdown' directory.", file=sys.stderr)
    print("Copy config.example.ini to config.ini and set your output directory.", file=sys.stderr)
    OUT_DIR = Path("markdown")

TIMEOUT_SECS = 30


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


def sanitize_filename(name: str) -> str:
    """
    Sanitize a string to be a valid cross-platform filename.
    """
    name = name.strip()
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name)
    # Remove characters illegal on Windows/Mac/Linux
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    # Avoid trailing dots/spaces (Windows issue)
    name = name.strip(" .")
    # Limit length
    name = name[:180]
    return name or "untitled"


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
    original_url: str
) -> tuple[str, str, bool]:
    """
    Process a single URL: fetch, extract title, save to file.

    Returns:
        (original_url, output_path, used_title)
    """
    target = into_md_url(original_url)
    md = await fetch_markdown(session, target)

    # Try to extract title from markdown
    title = extract_h1_title(md)
    if title:
        filename_base = sanitize_filename(title)
    else:
        filename_base = sanitize_filename(fallback_name_from_url(original_url))

    # Ensure output directory exists
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Find unique filename
    out_path = unique_path(OUT_DIR / f"{filename_base}.md")

    # Write markdown to file
    out_path.write_text(md, encoding="utf-8")

    return (original_url, str(out_path), bool(title))


async def main(urls: list[str]) -> None:
    """
    Main async entry point: process all URLs concurrently.
    """
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECS)
    headers = {"User-Agent": "web-content-parser/1.0"}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        tasks = [process_url(session, u) for u in urls]

        # Use tqdm to show progress bar as tasks complete
        results = []
        for coro in tqdm.as_completed(tasks, total=len(urls), desc="Fetching", unit="url"):
            results.append(await coro)

    # Report results
    ok = 0
    print()  # Add newline after progress bar
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            print(f"[FAIL] {url} -> {result}", file=sys.stderr)
        else:
            ok += 1
            original_url, out_path, used_title = result
            note = "title" if used_title else "fallback"
            print(f"[OK] ({note}) {original_url} -> {out_path}")

    print(f"\n✓ Done: {ok}/{len(urls)} succeeded.")


if __name__ == "__main__":
    urls = sys.argv[1:]
    if not urls:
        print("Usage: python fetch_markdown.py <url1> <url2> ...", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print('  python fetch_markdown.py "https://example.com/article"', file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(urls))
