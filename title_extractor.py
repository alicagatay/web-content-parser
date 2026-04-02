"""
Title Extraction Module

Extracts article titles from HTML metadata, markdown headings, or URL fallback.
"""
import re
from urllib.parse import urlparse

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None


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
