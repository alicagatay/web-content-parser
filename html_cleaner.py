"""
HTML Cleaning Module

Pre-processes HTML to remove noise elements before content extraction.
This improves extraction quality by eliminating navigation, ads, footers,
and other boilerplate before trafilatura/html2text processes the content.
"""
import re
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Comment


# Default noise selectors to remove
DEFAULT_NOISE_SELECTORS = [
    # Semantic noise tags
    'nav', 'header', 'footer', 'aside',

    # ARIA roles
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[role="complementary"]', '[role="menu"]', '[role="menubar"]',
    '[role="search"]',

    # Common noise classes
    '.sidebar', '.menu', '.nav', '.navbar', '.header', '.footer',
    '.navigation', '.site-header', '.site-footer', '.site-nav',

    # Ads and promotions
    '.advertisement', '.ad', '.ads', '.advert', '.sponsored',
    '.banner', '.promo', '.promotion',
    '[class*="ad-"]', '[class*="ads-"]', '[id*="ad-"]', '[id*="ads-"]',

    # Social and sharing
    '.social-share', '.share-buttons', '.social-links', '.social-icons',
    '.sharing', '.share', '[class*="share"]',

    # Comments
    '.comments', '.comment-section', '#comments', '#disqus_thread',
    '.comment-list', '.comments-area',

    # Related content
    '.related-posts', '.related-articles', '.recommended',
    '.more-stories', '.also-read', '.read-more',

    # Popups and modals
    '.popup', '.modal', '.overlay', '.lightbox',
    '.cookie-notice', '.cookie-banner', '.cookie-consent',
    '.newsletter-popup', '.subscribe-popup',

    # Widgets and misc
    '.widget', '.widgets', '.sidebar-widget',
    '.breadcrumb', '.breadcrumbs',
    '.pagination', '.pager',
    '.tags', '.tag-cloud',
    '.author-bio', '.author-box',
    '.print-only', '.screen-reader-text',

    # Common ID patterns
    '#sidebar', '#menu', '#nav', '#navigation', '#comments',
    '#footer', '#header', '#cookie-notice',
]

# Tags that should always be removed
REMOVE_TAGS = [
    'script', 'style', 'noscript', 'svg', 'canvas',
    'iframe', 'object', 'embed', 'applet',
    'meta', 'link', 'template',
]


def clean_html_for_extraction(
    html: str,
    url: Optional[str] = None,
    extra_noise_selectors: Optional[list[str]] = None,
    remove_hidden: bool = True,
    remove_empty: bool = True,
) -> str:
    """
    Pre-clean HTML to remove noise before content extraction.

    Args:
        html: Raw HTML string
        url: Optional URL (for future use)
        extra_noise_selectors: Additional CSS selectors to remove
        remove_hidden: Remove elements with display:none or visibility:hidden
        remove_empty: Remove empty elements (no text content)

    Returns:
        Cleaned HTML string
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, 'lxml')

    # Step 1: Remove script, style, and other non-content tags
    for tag_name in REMOVE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Step 2: Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Step 3: Build selector list
    selectors = DEFAULT_NOISE_SELECTORS.copy()

    # Add extra selectors
    if extra_noise_selectors:
        selectors.extend(extra_noise_selectors)

    # Step 4: Remove noise elements
    for selector in selectors:
        try:
            for element in soup.select(selector):
                element.decompose()
        except Exception:
            # Skip invalid selectors
            continue

    # Step 5: Remove hidden elements
    if remove_hidden:
        # Inline style hidden
        hidden_pattern = re.compile(r'display:\s*none|visibility:\s*hidden', re.IGNORECASE)
        for element in soup.find_all(style=hidden_pattern):
            element.decompose()

        # Hidden attribute
        for element in soup.find_all(attrs={'hidden': True}):
            element.decompose()

        # aria-hidden
        for element in soup.find_all(attrs={'aria-hidden': 'true'}):
            # Keep if it might contain important content
            if not element.find_all(['article', 'main', 'p']):
                element.decompose()

    # Step 6: Remove empty elements
    if remove_empty:
        # Tags that are allowed to be empty
        self_closing = {'img', 'video', 'audio', 'br', 'hr', 'input', 'source', 'track', 'wbr'}

        # Multiple passes to handle nested empty elements
        for _ in range(3):
            for element in soup.find_all():
                if element.name in self_closing:
                    continue

                # Check if element has no meaningful content
                text = element.get_text(strip=True)
                has_media = element.find_all(['img', 'video', 'audio', 'picture', 'figure'])

                if not text and not has_media:
                    element.decompose()

    return str(soup)


def extract_main_content(html: str, url: Optional[str] = None) -> Optional[str]:
    """
    Extract main content area from HTML using CSS selectors.

    Uses common content patterns to find the main content area.

    Args:
        html: HTML string (can be raw or pre-cleaned)
        url: Optional URL (for future use)

    Returns:
        HTML string of main content, or None if not found
    """
    soup = BeautifulSoup(html, 'lxml')

    # Common content selectors in priority order
    selectors = [
        'article.post-content',
        'article.article-content',
        'article.entry-content',
        'article',
        'main article',
        '[role="main"] article',
        '[role="main"]',
        'main',
        '.article-content',
        '.post-content',
        '.entry-content',
        '.content-body',
        '.article-body',
        '.story-body',
        '.post-body',
        '#article-body',
        '#content',
        '.content',
    ]

    for selector in selectors:
        try:
            element = soup.select_one(selector)
            if element:
                # Verify it has substantial content
                text = element.get_text(strip=True)
                if len(text) > 200:  # Minimum content threshold
                    return str(element)
        except Exception:
            continue

    return None


def filter_short_blocks(markdown: str, min_words: int = 50) -> str:
    """
    Remove markdown blocks that are too short to be meaningful content.

    Args:
        markdown: Markdown string
        min_words: Minimum word count for a block (default 50)

    Returns:
        Filtered markdown string
    """
    if not markdown:
        return ""

    # Split on double newlines (paragraph boundaries)
    blocks = markdown.split('\n\n')
    filtered = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Always keep headers
        if block.startswith('#'):
            filtered.append(block)
            continue

        # Always keep code blocks
        if block.startswith('```') or block.startswith('    '):
            filtered.append(block)
            continue

        # Always keep lists
        if block.startswith('- ') or block.startswith('* ') or re.match(r'^\d+\.', block):
            filtered.append(block)
            continue

        # Always keep blockquotes
        if block.startswith('>'):
            filtered.append(block)
            continue

        # Check word count for regular paragraphs
        word_count = len(block.split())
        if word_count >= min_words:
            filtered.append(block)

    return '\n\n'.join(filtered)
