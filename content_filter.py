"""
Content Filtering Module

Provides scoring and pruning functionality to identify and extract
main content from web pages while removing boilerplate/noise.

Inspired by crawl4ai's PruningContentFilter and BM25ContentFilter.
"""
import re
import math
from dataclasses import dataclass, field
from typing import Optional
from bs4 import BeautifulSoup, Tag, Comment


@dataclass
class FilterConfig:
    """Configuration for content filtering."""

    # Pruning threshold (0.0 - 1.0). Nodes scoring below this are removed.
    # Lower = keep more content, Higher = more aggressive pruning
    pruning_threshold: float = 0.48

    # Minimum word count for a block to be kept
    min_word_threshold: int = 10

    # Whether to use dynamic threshold adjustment based on content
    dynamic_threshold: bool = True

    # Weight factors for scoring
    text_density_weight: float = 0.4
    link_density_weight: float = 0.3
    tag_weight: float = 0.2
    class_id_weight: float = 0.1


class ContentScorer:
    """
    Score HTML elements to identify main content vs boilerplate.

    Uses multiple signals:
    - Text density (text length / HTML length)
    - Link density (link text / total text)
    - Tag importance (article, main, p vs nav, footer, aside)
    - Class/ID patterns (content, article vs ad, sidebar, menu)
    """

    # Tags that typically contain main content
    CONTENT_TAGS = {
        'article': 1.5,
        'main': 1.4,
        'section': 1.1,
        'p': 1.2,
        'h1': 1.3,
        'h2': 1.2,
        'h3': 1.1,
        'h4': 1.0,
        'h5': 1.0,
        'h6': 1.0,
        'blockquote': 1.1,
        'pre': 1.0,
        'code': 1.0,
        'figure': 1.0,
        'figcaption': 1.0,
        'table': 0.9,
        'ul': 0.8,
        'ol': 0.9,
        'li': 0.8,
        'div': 0.7,
        'span': 0.6,
    }

    # Tags that typically contain noise/boilerplate
    NOISE_TAGS = {
        'nav': 0.1,
        'header': 0.2,
        'footer': 0.1,
        'aside': 0.2,
        'menu': 0.1,
        'form': 0.3,
        'button': 0.2,
        'input': 0.1,
        'select': 0.1,
        'iframe': 0.1,
    }

    # Patterns indicating content in class/id attributes
    CONTENT_PATTERNS = re.compile(
        r'(article|content|post|entry|main|body|text|story|blog|news)',
        re.IGNORECASE
    )

    # Patterns indicating noise in class/id attributes
    NOISE_PATTERNS = re.compile(
        r'(nav|menu|sidebar|footer|header|ad|banner|promo|social|share|'
        r'comment|related|widget|popup|modal|cookie|newsletter|subscribe|'
        r'sponsored|advertisement|tracking|analytics|breadcrumb|pagination)',
        re.IGNORECASE
    )

    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig()

    def get_tag_weight(self, tag: Tag) -> float:
        """Get weight based on tag name."""
        tag_name = tag.name.lower() if tag.name else 'div'

        if tag_name in self.NOISE_TAGS:
            return self.NOISE_TAGS[tag_name]

        return self.CONTENT_TAGS.get(tag_name, 0.5)

    def get_class_id_weight(self, tag: Tag) -> float:
        """Get weight based on class and id attributes."""
        class_list = tag.get('class', [])
        if isinstance(class_list, str):
            class_list = [class_list]

        class_id_str = ' '.join(class_list) + ' ' + (tag.get('id', '') or '')

        if not class_id_str.strip():
            return 1.0  # Neutral if no class/id

        # Check for noise patterns
        if self.NOISE_PATTERNS.search(class_id_str):
            return 0.3

        # Check for content patterns
        if self.CONTENT_PATTERNS.search(class_id_str):
            return 1.3

        return 1.0

    def compute_text_density(self, tag: Tag) -> float:
        """
        Compute text density = text length / HTML length.
        Higher density indicates more text content, less markup.
        """
        text = tag.get_text(strip=True)
        text_len = len(text)

        if text_len == 0:
            return 0.0

        try:
            html_len = len(str(tag))
        except Exception:
            html_len = text_len

        if html_len == 0:
            return 0.0

        # Normalize to 0-1 range (typical density is 0.1-0.5)
        density = text_len / html_len
        return min(density * 2, 1.0)

    def compute_link_density(self, tag: Tag) -> float:
        """
        Compute link density = link text / total text.
        Lower is better for content (high link density = navigation).
        Returns inverted score (1 - density) so higher = better.
        """
        text = tag.get_text(strip=True)
        text_len = len(text)

        if text_len == 0:
            return 0.0

        links = tag.find_all('a')
        link_text_len = sum(len(a.get_text(strip=True)) for a in links)

        link_density = link_text_len / text_len

        # Invert so higher score = less links = more likely content
        return 1.0 - min(link_density, 1.0)

    def compute_score(self, tag: Tag) -> float:
        """
        Compute overall content score for an element.

        Returns:
            Float between 0 and 1. Higher = more likely to be content.
        """
        config = self.config

        # Get individual metrics
        text_density = self.compute_text_density(tag)
        link_density_score = self.compute_link_density(tag)
        tag_weight = self.get_tag_weight(tag)
        class_id_weight = self.get_class_id_weight(tag)

        # Compute weighted score
        score = (
            config.text_density_weight * text_density +
            config.link_density_weight * link_density_score +
            config.tag_weight * min(tag_weight, 1.0) +
            config.class_id_weight * min(class_id_weight, 1.0)
        )

        # Apply tag and class/id multipliers
        score *= tag_weight * class_id_weight

        # Normalize to 0-1 range
        return min(max(score, 0.0), 1.0)


class PruningContentFilter:
    """
    Filter that prunes low-scoring nodes from HTML.

    Uses ContentScorer to identify and remove boilerplate content,
    leaving only the main content for extraction.
    """

    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig()
        self.scorer = ContentScorer(self.config)

    def filter_content(self, html: str) -> str:
        """
        Filter HTML content by pruning low-scoring nodes.

        Args:
            html: Raw HTML string

        Returns:
            Cleaned HTML string with low-scoring nodes removed
        """
        if not html or not isinstance(html, str):
            return ""

        soup = BeautifulSoup(html, 'lxml')

        if not soup.body:
            soup = BeautifulSoup(f"<body>{html}</body>", 'lxml')

        body = soup.find('body')
        if not body:
            return html

        # Remove comments
        self._remove_comments(soup)

        # Remove script and style tags
        self._remove_unwanted_tags(soup)

        # Prune tree based on scores
        self._prune_tree(body)

        return str(soup)

    def _remove_comments(self, soup: BeautifulSoup) -> None:
        """Remove all HTML comments."""
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

    def _remove_unwanted_tags(self, soup: BeautifulSoup) -> None:
        """Remove script, style, and other non-content tags."""
        unwanted_tags = [
            'script', 'style', 'noscript', 'svg', 'path',
            'meta', 'link', 'template'
        ]

        for tag_name in unwanted_tags:
            for tag in soup.find_all(tag_name):
                tag.decompose()

    def _prune_tree(self, node: Tag) -> None:
        """
        Recursively prune the tree by removing low-scoring nodes.
        """
        if not node or not hasattr(node, 'name') or node.name is None:
            return

        # Get list of child elements (not text nodes)
        children = [
            child for child in node.children
            if hasattr(child, 'name') and child.name is not None
        ]

        for child in children:
            # Check word count
            text = child.get_text(strip=True)
            word_count = len(text.split())

            if word_count < self.config.min_word_threshold:
                # Keep headers even if short
                if child.name not in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                    # Check if it contains important children
                    important_children = child.find_all(['h1', 'h2', 'h3', 'article', 'main', 'p'])
                    if not important_children:
                        child.decompose()
                        continue

            # Compute score
            score = self.scorer.compute_score(child)

            # Determine threshold
            threshold = self.config.pruning_threshold

            if self.config.dynamic_threshold:
                # Adjust threshold based on tag importance
                tag_importance = self.scorer.get_tag_weight(child)
                if tag_importance > 1.0:
                    threshold *= 0.8  # Lower threshold for important tags
                elif tag_importance < 0.5:
                    threshold *= 1.2  # Higher threshold for noise tags

            if score < threshold:
                # Check if it contains important nested content
                important_nested = child.find_all(['article', 'main', 'p', 'h1', 'h2', 'h3'])
                important_nested = [el for el in important_nested if len(el.get_text(strip=True)) > 50]

                if not important_nested:
                    child.decompose()
                    continue

            # Recurse into children
            self._prune_tree(child)

    def get_text_blocks(self, html: str) -> list[str]:
        """
        Get list of text blocks from filtered HTML.

        Returns:
            List of text strings from remaining content nodes
        """
        filtered_html = self.filter_content(html)
        soup = BeautifulSoup(filtered_html, 'lxml')

        blocks = []
        for tag in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre']):
            text = tag.get_text(strip=True)
            if text and len(text.split()) >= self.config.min_word_threshold:
                blocks.append(text)

        return blocks
