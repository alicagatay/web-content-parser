"""
Content Extraction Module

Provides HTML fetching, multi-strategy content extraction, and the extraction pipeline.
"""
import aiohttp
from dataclasses import dataclass
from lxml import html as lxml_html, etree
import html2text
from bs4 import BeautifulSoup

try:
    import trafilatura
except Exception:  # pragma: no cover
    trafilatura = None

from content_filter import FilterConfig, PruningContentFilter
from html_cleaner import (
    clean_html_for_extraction,
    extract_main_content,
)


@dataclass
class ExtractionConfig:
    """Configuration for content extraction."""
    # Enable HTML cleaning before extraction
    enable_cleaning: bool = True

    # Enable content pruning/scoring
    enable_pruning: bool = True

    # Pruning threshold (0.0-1.0). Lower keeps more, higher is more aggressive
    pruning_threshold: float = 0.48

    # Minimum word count for blocks (in final markdown filtering)
    min_words: int = 0

    # Minimum word count for pruning filter
    min_word_threshold: int = 10

    # Use dynamic threshold adjustment
    dynamic_threshold: bool = True


async def fetch_html(
    session: aiohttp.ClientSession,
    url: str
) -> str:
    """
    Fetch raw HTML from a URL using aiohttp.

    Returns:
        str: Raw HTML content
    """
    # Ensure URL has scheme
    url = url.strip()
    if "://" not in url:
        url = "https://" + url

    async with session.get(url) as resp:
        resp.raise_for_status()
        html = await resp.text()

    if not html or not html.strip():
        raise RuntimeError(f"Empty response from {url}")

    return html


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


def extract_with_css_selectors(html: str, url: str | None = None) -> str | None:
    """
    Extract content using priority-ordered CSS selectors.

    Uses common content patterns to find main content.

    Args:
        html: HTML content
        url: Optional URL (for future use)

    Returns:
        Markdown string or None if extraction fails
    """
    try:
        # Try to extract main content
        main_content = extract_main_content(html, url)

        if main_content:
            # Convert to markdown
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.body_width = 0
            md = h.handle(main_content)

            if md and len(md.strip()) > 200:
                return md.strip()

        # Fallback: try common selectors manually
        soup = BeautifulSoup(html, 'lxml')

        selectors = [
            'article.post-content',
            'article.entry-content',
            'article',
            'main article',
            '[role="main"]',
            'main',
            '.article-content',
            '.post-content',
            '.entry-content',
            '.content-body',
        ]

        for selector in selectors:
            try:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(strip=True)
                    if len(text) > 500:  # Minimum threshold
                        h = html2text.HTML2Text()
                        h.ignore_links = False
                        h.body_width = 0
                        md = h.handle(str(element))
                        if md:
                            return md.strip()
            except Exception:
                continue

        return None

    except Exception:
        return None


def apply_extraction_pipeline(html: str, url: str, config: ExtractionConfig) -> tuple[str, str]:
    """
    Apply the full extraction pipeline: clean, prune, extract.

    Returns both cleaned HTML and the HTML after pruning.

    Args:
        html: Raw HTML
        url: Page URL
        config: Extraction configuration

    Returns:
        Tuple of (cleaned_html, pruned_html)
    """
    # Step 1: Basic cleaning (remove scripts, noise elements, etc.)
    if config.enable_cleaning:
        cleaned_html = clean_html_for_extraction(html, url=url)
    else:
        cleaned_html = html

    # Step 2: Content-aware pruning
    if config.enable_pruning:
        filter_config = FilterConfig(
            pruning_threshold=config.pruning_threshold,
            min_word_threshold=config.min_word_threshold,
            dynamic_threshold=config.dynamic_threshold,
        )
        pruning_filter = PruningContentFilter(filter_config)
        pruned_html = pruning_filter.filter_content(cleaned_html)
    else:
        pruned_html = cleaned_html

    return cleaned_html, pruned_html
