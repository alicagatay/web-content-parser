# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that fetches web content, extracts it as markdown, and creates formatted Google Docs. Uses a hybrid extraction pipeline: parallel aiohttp + Playwright fetching, BeautifulSoup HTML cleaning, content scoring/pruning, and multi-strategy extraction (trafilatura, multi-div, CSS-targeted) to maximize content quality.

## Commands

```bash
# Run the tool
python fetch_markdown.py "https://example.com/article"

# Multiple URLs
python fetch_markdown.py url1 url2 url3

# With options
python fetch_markdown.py --no-clean --pruning-threshold 0.6 --min-words 30 url1

# Install dependencies (pip)
pip install -r requirements.txt

# Install dependencies (conda)
conda install -c conda-forge aiohttp tqdm google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client markdown-it-py
conda install anaconda::beautifulsoup4
pip install trafilatura playwright lxml html2text

# Install Playwright browser
playwright install chromium
```

## Architecture

The pipeline flows: **URLs -> Parallel Fetch -> HTML Clean -> Content Prune -> Multi-Strategy Extract -> Markdown Post-Process -> Google Doc Creation**

### Module Responsibilities

- **`fetch_markdown.py`** — Main CLI entry point and orchestrator. Contains `main()` async loop, `process_url()` with 6 extraction strategies, batch retry logic, CLI argument parsing. `ExtractionConfig` is created from CLI args and passed through the call chain (no global state).
- **`extraction.py`** — `ExtractionConfig` dataclass, `fetch_html()` for aiohttp fetching, `extract_with_multi_div()`, `extract_with_css_selectors()`, and `apply_extraction_pipeline()` for the clean/prune pipeline.
- **`playwright_fetch.py`** — `fetch_with_playwright()` using a shared `BrowserContext` (created once in `main()`), and `smart_wait_for_content()` with combined CSS selector waiting.
- **`google_drive.py`** — `create_google_doc()`, `_build_doc_title_cache_sync()`, `_find_existing_doc_id_recursive_sync()`, and `sanitize_doc_title()`. Google API services are created once in `main()` and passed as parameters.
- **`title_extractor.py`** — `extract_title_from_metadata()` (trafilatura), `extract_h1_title()`, `fallback_name_from_url()`.
- **`auth.py`** — OAuth2 flow for Google APIs. Loads/refreshes credentials from `token.json`, falls back to browser-based OAuth. Exports `get_docs_service()`, `get_drive_service()`, `find_folder_id()`.
- **`html_cleaner.py`** — BeautifulSoup-based noise removal. `clean_html_for_extraction()` strips 60+ noise selectors (nav, ads, popups, etc.). `extract_main_content()` for targeted extraction. `filter_short_blocks()` for post-extraction markdown filtering.
- **`content_filter.py`** — `PruningContentFilter` with `ContentScorer` that scores HTML elements on text density (40%), link density (30%), tag importance (20%), and class/ID patterns (10%). Configurable via `FilterConfig` dataclass.
- **`docs_converter.py`** — `MarkdownToDocsConverter` class that parses markdown with `markdown-it-py` and builds Google Docs API `batchUpdate` requests. Handles headings, bold, italic, links, lists, code blocks. Uses reverse insertion strategy for correct index tracking.

### Key Design Decisions

- **Best-of-12 extraction**: Runs 6 strategies on each of 2 HTML sources (aiohttp + Playwright), selects the longest result. This maximizes content capture across diverse site structures.
- **Doc title cache**: Built once per run via `_build_doc_title_cache_sync()` with recursive Drive folder traversal, avoiding repeated API calls for duplicate detection.
- **Shared resources**: Google API services and Playwright browser context are created once in `main()` and passed through the call chain. No global mutable state.
- **Single retry layer**: Only batch-level retries in `main()` (`MAX_RETRY_ROUNDS`). Per-fetch retry loops were removed to simplify retry reasoning.
- **Concurrency**: 15 parallel tasks for both aiohttp and Playwright (`MAX_CONCURRENCY`, `PLAYWRIGHT_CONCURRENCY`). Google API rate limits mean actual speedup is ~2x, not 15x.
- **Credentials files** (`credentials.json`, `token.json`) are gitignored and live in project root. Required for Google API access.

### Constants

- `DRIVE_FOLDER_NAME = "Resources"` — target Google Drive folder (in `fetch_markdown.py`)
- `TIMEOUT_SECS = 30` — aiohttp fetch timeout (in `fetch_markdown.py`)
- `PLAYWRIGHT_TIMEOUT = 45000` — Playwright navigation timeout (in `playwright_fetch.py`)
- `MAX_RETRY_ROUNDS = 3` — batch-level retry rounds for failed URLs (in `fetch_markdown.py`)

## Prerequisites

- Python 3.10+
- Google Cloud project with Docs + Drive APIs enabled
- `credentials.json` from Google Cloud OAuth (Desktop app type)
- Playwright Chromium browser (`playwright install chromium`)
