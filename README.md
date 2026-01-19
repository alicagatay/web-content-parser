# Web Content Parser

A powerful Python CLI tool that fetches web content and automatically creates formatted Google Docs. Uses intelligent hybrid extraction combining fast HTTP requests with headless browser automation, enhanced with BeautifulSoup cleaning and content-aware pruning for maximum extraction quality.

## Features

### Core Capabilities

- **Intelligent dual extraction** - runs both aiohttp and Playwright in parallel, uses longest result
- **Multi-strategy content extraction** - 6 extraction strategies per HTML source (up to 12 attempts per URL)
- **BeautifulSoup HTML cleaning** - removes 60+ noise patterns (ads, navigation, footers, popups)
- **Content-aware pruning** - scores elements by text density, link ratio, and semantic importance
- **CSS-targeted extraction** - priority-ordered selector matching for common content patterns
- **Concurrent processing** - fetches up to 15 URLs in parallel, ~2x faster than sequential
- **Google Docs integration** - automatically creates formatted documents in your Drive
- **Smart formatting** - converts markdown to native Google Docs formatting (headings, bold, italic, links, lists)
- **Source + title header** - inserts the document title (Docs TITLE style) and clickable source URL at the top
- **Drive reuse with cache** - reuses existing Docs by title across nested folders with a one-time cache build

### Content Extraction Pipeline

- **HTML Cleaning** - BeautifulSoup removes scripts, styles, navigation, ads, popups, and hidden elements
- **Content Pruning** - PruningContentFilter scores nodes and removes low-quality boilerplate
- **trafilatura** - smart content detection with noise filtering
- **multi-div extraction** - handles sites that split articles across multiple containers (e.g., Ars Technica)
- **CSS-targeted extraction** - uses semantic selectors (`article`, `main`, `.content`, etc.)
- **Playwright browser automation** - handles JavaScript-rendered content (Medium, LinkedIn, etc.)
- **Stealth mode** - anti-detection headers and settings to bypass basic bot protection
- **Automatic method selection** - always uses the extraction that gets the most content

### Reliability & UX

- **Automatic retry** - failed URLs retry up to 3 rounds with exponential backoff
- **Automatic title extraction** - from metadata, H1 headings, or URL fallback
- **Duplicate avoidance** - reuses existing Docs when a matching title already exists (recursive Drive search)
- **Real-time progress bar** - shows completion status, speed, and extraction method used
- **Detailed logging** - shows extraction method, content length, and document title for each URL
- **Configurable CLI** - tune cleaning, pruning, and filtering via command-line flags
- **OAuth authentication** - one-time browser login, then automatic for future runs
- **Resilient error handling** - continues processing even if some URLs fail

## Prerequisites

- Python 3.10+
- Google Cloud account (free)
- Google Drive folder named "Resources" (or customize in code)
- Playwright browsers (automatically installed)

## Installation

### 1. Install dependencies

Using conda (recommended):

```bash
conda install -c conda-forge aiohttp tqdm google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client markdown-it-py
conda install anaconda::beautifulsoup4
pip install trafilatura playwright lxml html2text
```

Or using pip:

```bash
pip install -r requirements.txt
```

### 2. Install Playwright browsers

```bash
playwright install chromium
```

### 3. Set up Google Cloud OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable these APIs:
   - **Google Docs API**
   - **Google Drive API**
4. Configure OAuth consent screen:
   - User type: **External**
   - Add your email as a **test user**
   - Keep publishing status as **Testing** (no verification needed)
   - Add scopes:
     - `https://www.googleapis.com/auth/documents`
     - `https://www.googleapis.com/auth/drive`
5. Create credentials:
   - **APIs & Services → Credentials**
   - **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Download JSON and save as `credentials.json` in project root

### 4. Configure target folder (optional)

By default, docs are created in a folder named **"Resources"** in your Google Drive. To use a different folder:

1. Create/choose a folder in Google Drive
2. Edit `DRIVE_FOLDER_NAME` in `fetch_markdown.py`:
   ```python
   DRIVE_FOLDER_NAME = "Your Folder Name"
   ```

## Usage

Pass one or more URLs as command-line arguments:

```bash
python fetch_markdown.py "https://example.com/article1" "https://example.com/article2"
```

**First run:** Browser will open for Google authorization (one-time only)

**Subsequent runs:** Automatic authentication using saved token

### What happens:

1. Authenticates with Google (uses cached token after first run)
2. Finds your "Resources" folder in Google Drive
3. **Launches headless Chromium browser** (Playwright)
4. **Fetches content using BOTH methods in parallel:**
   - **aiohttp** - fast HTTP request (~1-2s)
   - **Playwright** - headless browser with JavaScript execution (~5-8s)
5. **Cleans HTML with BeautifulSoup:**
   - Removes scripts, styles, iframes, SVG, canvas
   - Removes 60+ noise selectors (nav, footer, ads, popups, etc.)
   - Removes hidden elements and empty containers
6. **Prunes content with PruningContentFilter:**
   - Scores each element by text density, link ratio, tag importance
   - Removes elements below quality threshold
7. **Runs 6 extraction strategies on each HTML source:**
   - raw HTML + trafilatura
   - cleaned HTML + trafilatura
   - pruned HTML + trafilatura
   - raw HTML + multi-div
   - cleaned HTML + multi-div
   - CSS-targeted extraction
8. **Automatically uses the longest result** from all 12 combinations
9. Filters short paragraphs (< 50 words by default)
10. Extracts article title (metadata → H1 → URL fallback)
11. Creates formatted Google Doc in your Resources folder
12. Returns shareable document URL with detailed extraction info

### Example with multiple URLs:

```bash
python fetch_markdown.py \
  "https://arstechnica.com/tech-policy/2024/01/..." \
  "https://www.theverge.com/2024/1/15/..." \
  "https://techcrunch.com/2024/01/15/..."
```

### Real-world batch processing (60 URLs):

```bash
python fetch_markdown.py \
  "https://www.a16z.news/p/state-of-consumer-ai-2025" \
  "https://www.growthunhinged.com/p/the-best-growth-tactics-of-2025" \
  ...  # 58 more URLs
```

**Performance:** 60 URLs in ~4.5 minutes (4.6s average per doc)

## Output

**Progress Output:**

```
Locating Google Drive folder...
✓ Found 'Resources' folder

Fetching & Creating: 100%|████████████| 60/60 [04:36<00:00, 4.61s/doc]

[OK] || [aiohttp with multi-div] || 53,008 chars || "SpaceX's historic rocket landing" || https://arstechnica.com/... -> https://docs.google.com/document/d/...
[OK] || [playwright with trafilatura] || 9,527 chars || "Introducing GPT-5.2-Codex" || https://openai.com/... -> https://docs.google.com/document/d/...
[OK] || [aiohttp with trafilatura] || 56,708 chars || "Let a thousand societies bloom" || https://vitalik.eth.limo/... -> https://docs.google.com/document/d/...

✓ Done: 60/60 succeeded, 0 failed.
```

**Log Format:**

```
[OK] || [extraction_method] || content_length || "Document Title" || source_url -> google_doc_url
```

**Extraction methods shown:**

- `aiohttp with raw with trafilatura` - Fast HTTP + raw HTML + trafilatura
- `aiohttp with cleaned with trafilatura` - Fast HTTP + cleaned HTML + trafilatura
- `aiohttp with pruned with trafilatura` - Fast HTTP + pruned HTML + trafilatura
- `playwright with css-targeted` - Browser + CSS selector extraction
- `playwright with cleaned with multi-div` - Browser + cleaned HTML + multi-container
- ...and more combinations

All Google Docs are created in your **Resources** folder with:

- Native Google Docs formatting (not plain text)
- Proper headings, bold, italic, links, lists, code blocks
- Top of doc includes:
  - Document title styled as **Google Docs TITLE**
  - Clickable **Source** link on the next line
- Content from whichever extraction method got the most text

## Project Structure

```
web-content-parser/
├── fetch_markdown.py      # Main CLI script with hybrid extraction
├── auth.py                # OAuth authentication & Google API clients
├── docs_converter.py      # Markdown → Google Docs formatting converter
├── html_cleaner.py        # BeautifulSoup HTML cleaning & noise removal
├── content_filter.py      # PruningContentFilter for content scoring
├── requirements.txt       # Python dependencies
├── credentials.json       # OAuth client secrets (git-ignored)
├── token.json             # User access tokens (git-ignored)
├── bin/
│   └── web-content-parser # Global command wrapper
└── README.md
```

## CLI Options

```bash
python fetch_markdown.py [OPTIONS] urls...
```

| Option                      | Description                                   | Default |
| --------------------------- | --------------------------------------------- | ------- |
| `--no-clean`                | Disable BeautifulSoup HTML cleaning           | Enabled |
| `--no-prune`                | Disable content scoring/pruning               | Enabled |
| `--pruning-threshold FLOAT` | Score threshold for keeping content (0.0-1.0) | 0.48    |
| `--min-words INT`           | Minimum words per markdown block              | 50      |
| `--min-word-threshold INT`  | Minimum words for pruning filter              | 10      |
| `--no-dynamic-threshold`    | Disable dynamic threshold adjustment          | Enabled |

### Examples

```bash
# Basic usage
python fetch_markdown.py "https://example.com/article"

# Disable cleaning for faster processing
python fetch_markdown.py --no-clean "https://example.com/article"

# More aggressive pruning (keeps less content)
python fetch_markdown.py --pruning-threshold 0.6 "https://example.com/article"

# Keep shorter paragraphs
python fetch_markdown.py --min-words 20 "https://example.com/article"

# Multiple URLs
python fetch_markdown.py url1 url2 url3
```

## Configuration

### Performance Settings

- **Concurrency**: 15 parallel tasks (both aiohttp and Playwright)
  - **Note**: While fetching runs fully in parallel, Google API operations have inherent rate limiting
  - **Actual speedup**: ~2x faster than sequential processing (not 15x)
  - **Why**: Google Docs/Drive APIs have connection pooling and rate limits that serialize some operations
  - **Benefit**: Still significantly faster, and content fetching (the slowest part) is fully parallelized
- **Timeout**: 30s for aiohttp, 45s for Playwright
- **Retries**: 2 per-URL retries + 3 batch-level retry rounds
- **Target folder**: "Resources" in Google Drive (customizable in `fetch_markdown.py`)
- **Startup cost**: one-time Drive cache build (faster reuse on larger batches)

### Extraction Strategy

- **Always runs both fetch methods** (aiohttp + Playwright) in parallel
- **Cleans HTML** with BeautifulSoup (60+ noise selectors removed)
- **Prunes content** with scoring algorithm (text density, link ratio, tag importance)
- **Runs 6 extraction strategies** on each HTML source (raw, cleaned, pruned × trafilatura, multi-div, CSS)
- **Compares all 12 combinations** and uses the longest result
- **Filters short blocks** (< 50 words by default)
- **No manual configuration needed** - fully automatic

### OAuth Scopes

- `documents` - create and edit Google Docs
- `drive` - access Drive folders and create files

## Security

Sensitive files are excluded from git via `.gitignore`:

- `credentials.json` - OAuth client secrets
- `token.json` - User access/refresh tokens

**Never commit these files to version control.**

## How It Works

### Architecture Overview

````
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              INPUT: URLs                                         │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        PHASE 1: PARALLEL FETCHING                               │
│  ┌─────────────────────────────┐    ┌─────────────────────────────────────────┐ │
│  │      aiohttp (Static)       │    │         Playwright (Dynamic)            │ │
│  │  • Fast HTTP requests       │    │  • Headless Chrome browser              │ │
│  │  • No JS execution          │    │  • Executes JavaScript                  │ │
│  │  • Good for static sites    │    │  • Smart wait for content               │ │
│  └──────────────┬──────────────┘    │  • Scroll triggers for lazy-load        │ │
│                 │                   └──────────────────┬──────────────────────┘ │
│                 │                                      │                        │
│                 └──────────────┬───────────────────────┘                        │
│                                ▼                                                │
│                    Both run in PARALLEL                                         │
│                    (up to 15 concurrent)                                        │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     PHASE 2: HTML CLEANING (BeautifulSoup)                      │
│                                                                                 │
│   Raw HTML ──► Remove:                                                          │
│                 • <script>, <style>, <noscript>, <iframe>                       │
│                 • HTML comments                                                 │
│                 • 60+ noise selectors:                                          │
│                   - nav, header, footer, aside                                  │
│                   - .sidebar, .menu, .navbar                                    │
│                   - .advertisement, .ad, .sponsored                             │
│                   - .social-share, .comments, .related-posts                    │
│                   - .popup, .modal, .cookie-notice                              │
│                 • Hidden elements (display:none, aria-hidden)                   │
│                 • Empty elements                                                │
│                                                                                 │
│   Output: cleaned_html                                                          │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    PHASE 3: CONTENT PRUNING (PruningContentFilter)              │
│                                                                                 │
│   For each HTML element, compute score (0.0 - 1.0):                             │
│                                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Score = (text_density × 0.4) + (1 - link_density) × 0.3 +              │   │
│   │          (tag_importance × 0.2) + (class_id_score × 0.1)                │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│   • text_density: ratio of text to total element size                          │
│   • link_density: ratio of link text to total text (high = navigation)         │
│   • tag_importance: article/main = 1.0, div = 0.5, nav = 0.1                   │
│   • class_id_score: "content/article" = boost, "sidebar/ad" = penalty          │
│                                                                                 │
│   Remove elements with score < threshold (default: 0.48)                        │
│                                                                                 │
│   Output: pruned_html                                                           │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    PHASE 4: MULTI-STRATEGY EXTRACTION                           │
│                                                                                 │
│   Run 6 strategies on EACH HTML source (aiohttp + playwright):                  │
│                                                                                 │
│   ┌─────────────────┬────────────────────────────────────────────────────────┐  │
│   │ Strategy        │ Description                                            │  │
│   ├─────────────────┼────────────────────────────────────────────────────────┤  │
│   │ raw+trafilatura │ trafilatura on original HTML                           │  │
│   │ cleaned+trafil. │ trafilatura on cleaned HTML                            │  │
│   │ pruned+trafil.  │ trafilatura on pruned HTML                             │  │
│   │ raw+multi-div   │ Combine multiple content divs (for split articles)     │  │
│   │ cleaned+multi   │ multi-div on cleaned HTML                              │  │
│   │ css-targeted    │ CSS selectors: article, main, .content, etc.           │  │
│   └─────────────────┴────────────────────────────────────────────────────────┘  │
│                                                                                 │
│   Total: up to 12 extraction attempts per URL                                   │
│   (6 strategies × 2 HTML sources)                                               │
│                                                                                 │
│   Winner: Strategy with LONGEST content                                         │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                       PHASE 5: MARKDOWN POST-PROCESSING                         │
│                                                                                 │
│   • Filter short blocks: Remove paragraphs with < 50 words                      │
│   • Keep: Headers (#), code blocks (```), lists (- *), blockquotes (>)          │
│   • Extract title: metadata → first H1 → URL fallback                           │
│   • Add source URL and timestamp header                                         │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        PHASE 6: GOOGLE DOCS CREATION                            │
│                                                                                 │
│   • Check doc cache (avoid duplicates)                                          │
│   • Create Google Doc with formatted content                                    │
│   • Move to "Resources" folder                                                  │
│   • Return shareable URL                                                        │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 OUTPUT                                          │
│                                                                                 │
│   [OK] || [playwright with css-targeted] || 39,307 chars || "Title" || url      │
│         ↑                                        ↑                              │
│         │                                        │                              │
│    Best strategy                          Content length                        │
└─────────────────────────────────────────────────────────────────────────────────┘
````

### Step-by-Step Breakdown

#### 1. **Authentication** (`auth.py`):

- First run: opens browser for OAuth consent
- Saves tokens to `token.json` for automatic refresh
- Creates authenticated Google Docs & Drive API clients

#### 2. **Parallel Fetching** (`fetch_markdown.py`):

- Launches headless Chromium browser (Playwright)
- For each URL, runs **both methods in parallel**:
  - **aiohttp**: Fast HTTP GET, static HTML (~1-2s)
  - **Playwright**: Headless browser, executes JavaScript, waits for content (~5-8s)
- Both complete simultaneously; tool waits for both results

#### 3. **HTML Cleaning** (`html_cleaner.py`):

- Removes non-content tags: `<script>`, `<style>`, `<noscript>`, `<iframe>`, `<svg>`
- Removes HTML comments
- Removes 60+ noise selectors:
  - Semantic: `nav`, `header`, `footer`, `aside`
  - Classes: `.sidebar`, `.menu`, `.navbar`, `.advertisement`, `.social-share`
  - IDs: `#comments`, `#sidebar`, `#cookie-notice`
  - ARIA roles: `[role="navigation"]`, `[role="banner"]`
- Removes hidden elements (`display:none`, `aria-hidden="true"`)
- Removes empty containers

#### 4. **Content Pruning** (`content_filter.py`):

- **PruningContentFilter** scores each element:
  - **Text density (40%)**: Ratio of text to element size
  - **Link density (30%)**: High link ratio = likely navigation
  - **Tag importance (20%)**: `article` = 1.0, `div` = 0.5, `nav` = 0.1
  - **Class/ID patterns (10%)**: "content" = boost, "sidebar" = penalty
- Removes elements below threshold (default: 0.48)
- Dynamic threshold adjustment based on content characteristics

#### 5. **Multi-Strategy Extraction**:

- Runs **6 strategies** on each HTML source:
  1. **raw + trafilatura**: Smart content detection on original HTML
  2. **cleaned + trafilatura**: trafilatura on cleaned HTML
  3. **pruned + trafilatura**: trafilatura on heavily pruned HTML
  4. **raw + multi-div**: XPath pattern matching, combines split articles
  5. **cleaned + multi-div**: multi-div on cleaned HTML
  6. **css-targeted**: Priority CSS selectors (`article`, `main`, `.content`, etc.)
- Compares **all 12 results** (6 strategies × 2 sources)
- Uses whichever got the **longest content**

#### 6. **Markdown Post-Processing**:

- Filters short paragraphs (< 50 words by default)
- Preserves headers, code blocks, lists, blockquotes
- Extracts title from metadata, H1, or URL

#### 7. **Document Creation** (`docs_converter.py`):

- Builds a one-time Drive title cache (recursive folder scan)
- Reuses existing Docs by title if found; otherwise creates new
- Parses markdown with `markdown-it-py`
- Converts to Google Docs API `batchUpdate` requests
- Applies formatting in single batch operation

## Why This Approach?

### Hybrid Fetching (aiohttp + Playwright)

- **aiohttp wins ~80% of the time** - faster for static sites
- **Playwright needed for ~20%** - JavaScript-heavy sites (Medium, LinkedIn, etc.)
- **Parallel execution** - total time ≈ slower method, not both added together
- **Some sites block Playwright** - aiohttp bypasses simple bot detection

### Multi-Stage Cleaning (BeautifulSoup + Pruning)

- **BeautifulSoup cleaning** - removes obvious noise (scripts, ads, navigation)
- **PruningContentFilter** - algorithmically scores and removes low-quality content
- **No manual rules needed** - works automatically on any site
- **Configurable thresholds** - tune aggressiveness via CLI flags

### Multi-Strategy Extraction (6 strategies × 2 sources)

- **trafilatura** - best for standard articles, smart noise filtering
- **multi-div** - catches split-content articles trafilatura misses
- **CSS-targeted** - reliable fallback using semantic HTML patterns
- **All complement each other** - different strengths for different site structures
- **12 total attempts** - maximum chance of successful extraction

### Real-World Results

- **60 URLs tested**: 100% success rate
- **Average**: 4.6s per document
- **Longest article**: 69,966 chars (Backlinko guide)
- **Mix**: ~80% used aiohttp, ~20% needed Playwright
- **Ars Technica**: 53,008 chars (20-page Google Doc) - multi-div won by 18.9x over trafilatura alone

## Troubleshooting

### "Folder 'Resources' not found"

- Ensure the folder exists in your Google Drive
- Check the folder name matches exactly (case-sensitive)
- Verify you completed OAuth authorization successfully

### "Access blocked: verification not completed"

- Go to OAuth consent screen in Google Cloud Console
- Add your email as a **test user**
- Ensure publishing status is **Testing** (not Production)

### "Invalid credentials"

- Delete `token.json` and re-run to re-authenticate
- Verify `credentials.json` is valid OAuth Desktop client

### Content still truncated/incomplete

The tool uses maximum extraction with 12 attempts per URL:

- Runs both aiohttp and Playwright in parallel
- Cleans HTML with BeautifulSoup (60+ noise selectors)
- Prunes with content scoring algorithm
- Tries 6 extraction strategies on each HTML source
- Uses the longest result automatically

If content is still incomplete, the site may:

- Use client-side rendering that Playwright can't access
- Block automated access with sophisticated bot detection
- Require authentication/subscription (paywalled content)
- Use infinite scroll or pagination (not yet supported)

### Playwright errors

```bash
# Reinstall Playwright browsers
playwright install chromium

# Or install all browsers
playwright install
```

### Slow performance

- Default: 15 parallel tasks (both aiohttp + Playwright running)
- Adjust `MAX_CONCURRENCY` and `PLAYWRIGHT_CONCURRENCY` in `fetch_markdown.py`
- Lower for slower machines, higher for faster (max recommended: 20)
