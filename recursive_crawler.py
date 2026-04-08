"""
Recursive URL discovery for crawling all sub-pages under a URL prefix.

Uses a sitemap-first strategy (fast, complete) with BFS crawl fallback
(follows links from pages). Strict URL prefix enforcement ensures the
crawler never leaves the target path.
"""

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup


@dataclass
class CrawlConfig:
    """Configuration for recursive URL discovery."""
    rate_limit: float = 0.5       # Seconds between crawl requests
    request_timeout: int = 30     # Timeout per HTTP request


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    Lowercases scheme/netloc, strips fragments and trailing slashes.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    # Reconstruct without fragment, with normalized path
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def is_within_prefix(candidate_url: str, base_url: str) -> bool:
    """Check if candidate_url is under the base_url prefix.

    Given base https://example.com/docs:
      - https://example.com/docs              -> True  (exact match)
      - https://example.com/docs/intro        -> True  (child path)
      - https://example.com/docs-old          -> False (different path)
      - https://example.com/                  -> False (parent)
      - https://other.com/docs/intro          -> False (different domain)
    """
    p_cand = urlparse(candidate_url)
    p_base = urlparse(base_url)

    # Scheme and domain must match exactly (case-insensitive)
    if p_cand.scheme.lower() != p_base.scheme.lower():
        return False
    if p_cand.netloc.lower() != p_base.netloc.lower():
        return False

    # Normalize paths: strip trailing slashes
    cand_path = p_cand.path.rstrip("/") or "/"
    base_path = p_base.path.rstrip("/") or "/"

    # Exact match or child path (must have / after prefix to prevent
    # /docs matching /documentation)
    return cand_path == base_path or cand_path.startswith(base_path + "/")


def _extract_links_from_html(html: str, page_url: str) -> list[str]:
    """Extract all href links from HTML, resolved to absolute URLs."""
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        # Skip anchors, javascript, mailto, tel
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(page_url, href)
        # Strip fragment
        if "#" in absolute:
            absolute = absolute.split("#")[0]
        if absolute:
            links.append(absolute)
    return list(set(links))


def _sort_urls_by_depth(urls: list[str]) -> list[str]:
    """Sort URLs by path depth (shallow first), then alphabetically."""
    def sort_key(url: str) -> tuple[int, str]:
        path = urlparse(url).path.rstrip("/")
        depth = path.count("/")
        return (depth, url.lower())
    return sorted(urls, key=sort_key)


async def _fetch_sitemap_urls(
    base_url: str,
    session: aiohttp.ClientSession,
    config: CrawlConfig,
) -> list[str]:
    """Try to discover URLs from sitemap.xml.

    Handles both regular sitemaps and sitemap index files.
    Returns URLs filtered to the base_url prefix.
    """
    parsed = urlparse(base_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"

    try:
        timeout = aiohttp.ClientTimeout(total=config.request_timeout)
        async with session.get(sitemap_url, timeout=timeout) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
    except Exception:
        return []

    return await _parse_sitemap_xml(text, base_url, session, config)


async def _parse_sitemap_xml(
    xml_text: str,
    base_url: str,
    session: aiohttp.ClientSession,
    config: CrawlConfig,
) -> list[str]:
    """Parse sitemap XML and extract matching URLs.

    Handles sitemap index files by recursively fetching child sitemaps.
    """
    urls = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Strip XML namespace for easier tag matching
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    # Check if this is a sitemap index
    sitemap_tags = root.findall(f"{ns}sitemap")
    if sitemap_tags:
        # Sitemap index: fetch each child sitemap
        for sitemap_tag in sitemap_tags:
            loc = sitemap_tag.find(f"{ns}loc")
            if loc is not None and loc.text:
                try:
                    timeout = aiohttp.ClientTimeout(total=config.request_timeout)
                    async with session.get(loc.text.strip(), timeout=timeout) as resp:
                        if resp.status == 200:
                            child_text = await resp.text()
                            child_urls = await _parse_sitemap_xml(
                                child_text, base_url, session, config
                            )
                            urls.extend(child_urls)
                except Exception:
                    continue
                await asyncio.sleep(config.rate_limit)
    else:
        # Regular sitemap: extract <loc> URLs
        for url_tag in root.findall(f"{ns}url"):
            loc = url_tag.find(f"{ns}loc")
            if loc is not None and loc.text:
                candidate = loc.text.strip()
                if is_within_prefix(candidate, base_url):
                    urls.append(candidate)

    return urls


async def _bfs_crawl(
    base_url: str,
    session: aiohttp.ClientSession,
    config: CrawlConfig,
) -> list[str]:
    """Discover URLs by crawling pages and following links (BFS).

    Starts from base_url, extracts links from each page, and follows
    only those within the URL prefix.
    """
    visited: set[str] = set()
    queue: list[str] = [base_url]
    discovered: list[str] = []

    timeout = aiohttp.ClientTimeout(total=config.request_timeout)

    while queue:
        url = queue.pop(0)
        normalized = normalize_url(url)

        if normalized in visited:
            continue
        visited.add(normalized)

        if not is_within_prefix(url, base_url):
            continue

        discovered.append(url)

        # Fetch page and extract links
        try:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    continue
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                html = await resp.text()
        except Exception:
            continue

        # Extract and filter links
        links = _extract_links_from_html(html, url)
        for link in links:
            if is_within_prefix(link, base_url) and normalize_url(link) not in visited:
                queue.append(link)

        await asyncio.sleep(config.rate_limit)

    return discovered


async def discover_urls(
    base_url: str,
    session: aiohttp.ClientSession,
    config: CrawlConfig | None = None,
) -> list[str]:
    """Discover all sub-pages under a URL prefix.

    Strategy:
    1. Try sitemap.xml first (fast, complete)
    2. Fall back to BFS crawl if sitemap unavailable or returns no matches

    Args:
        base_url: The base URL prefix to crawl under
        session: aiohttp session for HTTP requests
        config: Crawl configuration (rate limit, timeout)

    Returns:
        List of discovered URLs sorted by path depth then alphabetically.
        Includes the base_url itself if it resolves to a page.
    """
    if config is None:
        config = CrawlConfig()

    # Strategy 1: Try sitemap
    sitemap_urls = await _fetch_sitemap_urls(base_url, session, config)

    if sitemap_urls:
        # Deduplicate
        seen: set[str] = set()
        unique: list[str] = []
        for url in sitemap_urls:
            norm = normalize_url(url)
            if norm not in seen:
                seen.add(norm)
                unique.append(url)

        # Ensure base_url itself is included
        base_norm = normalize_url(base_url)
        if base_norm not in seen:
            unique.insert(0, base_url)

        return _sort_urls_by_depth(unique)

    # Strategy 2: BFS crawl
    crawled_urls = await _bfs_crawl(base_url, session, config)

    # Deduplicate
    seen = set()
    unique = []
    for url in crawled_urls:
        norm = normalize_url(url)
        if norm not in seen:
            seen.add(norm)
            unique.append(url)

    return _sort_urls_by_depth(unique)
