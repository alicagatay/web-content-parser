"""
Playwright Fetching Module

Handles headless browser fetching with smart content waiting strategies.
"""
import asyncio
from playwright.async_api import BrowserContext


PLAYWRIGHT_TIMEOUT = 45000  # 45 seconds (in milliseconds)


async def smart_wait_for_content(page) -> None:
    """
    Smart waiting for dynamic content with multiple strategies.
    """
    # Wait for any common article content container
    combined_selector = (
        'article, main, [role="main"], .article-content, '
        '.post-content, .entry-content, [itemprop="articleBody"]'
    )
    try:
        await page.wait_for_selector(combined_selector, timeout=3000)
        return
    except Exception:
        pass

    # Fallback: wait for network to settle
    try:
        await page.wait_for_load_state('networkidle', timeout=5000)
    except Exception:
        pass

    # Scroll to trigger lazy loading
    try:
        await page.evaluate("""
            () => {
                // Scroll to middle
                window.scrollTo(0, document.body.scrollHeight / 2);
            }
        """)
        await asyncio.sleep(0.5)

        await page.evaluate("""
            () => {
                // Scroll to bottom
                window.scrollTo(0, document.body.scrollHeight);
            }
        """)
        await asyncio.sleep(0.5)

        # Scroll back to top
        await page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass


async def fetch_with_playwright(
    context: BrowserContext,
    url: str
) -> str:
    """
    Fetch page content using Playwright (headless browser).
    This handles JavaScript-rendered content.

    Args:
        context: Shared Playwright browser context
        url: Target URL

    Returns:
        str: Rendered HTML after JavaScript execution
    """
    page = await context.new_page()
    try:
        # Navigate and wait for network to be mostly idle
        await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")

        # Use smart waiting for dynamic content
        await smart_wait_for_content(page)

        # Get fully rendered HTML
        html = await page.content()

        return html
    finally:
        await page.close()
