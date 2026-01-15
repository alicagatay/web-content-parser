# Web Content Parser

A powerful Python CLI tool that fetches web content and automatically creates formatted Google Docs. Uses intelligent hybrid extraction combining fast HTTP requests with headless browser automation for JavaScript-heavy sites.

## Features

### Core Capabilities

- **Intelligent dual extraction** - runs both aiohttp and Playwright in parallel, uses longest result
- **Multi-method content extraction** - combines trafilatura and multi-div extraction for maximum completeness
- **Concurrent processing** - fetches up to 15 URLs in parallel, ~2x faster than sequential (configurable)
- **Google Docs integration** - automatically creates formatted documents in your Drive
- **Smart formatting** - converts markdown to native Google Docs formatting (headings, bold, italic, links, lists)
- **Source + title header** - inserts the document title (Docs TITLE style) and clickable source URL at the top
- **Drive reuse with cache** - reuses existing Docs by title across nested folders with a one-time cache build

### Content Extraction

- **trafilatura** - smart content detection with noise filtering
- **multi-div extraction** - handles sites that split articles across multiple containers (e.g., Ars Technica)
- **Playwright browser automation** - handles JavaScript-rendered content (Medium, LinkedIn, etc.)
- **Stealth mode** - anti-detection headers and settings to bypass basic bot protection
- **Automatic method selection** - always uses the extraction that gets the most content

### Reliability & UX

- **Automatic retry** - failed URLs retry up to 3 rounds with exponential backoff
- **Automatic title extraction** - from metadata, H1 headings, or URL fallback
- **Duplicate avoidance** - reuses existing Docs when a matching title already exists (recursive Drive search)
- **Real-time progress bar** - shows completion status, speed, and extraction method used
- **Detailed logging** - shows extraction method, content length, and document title for each URL
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
5. **Tries BOTH extraction methods on each HTML source:**
   - **trafilatura** - smart content detection with noise filtering
   - **multi-div** - combines multiple content containers (for split articles)
6. **Automatically uses the longest result** from all 4 combinations
7. Extracts article title (metadata → H1 → URL fallback)
8. Creates formatted Google Doc in your Resources folder
9. Returns shareable document URL with detailed extraction info

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

- `aiohttp with trafilatura` - Fast HTTP + smart content detection
- `aiohttp with multi-div` - Fast HTTP + multi-container extraction
- `playwright with trafilatura` - Browser automation + smart detection
- `playwright with multi-div` - Browser automation + multi-container extraction

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
├── requirements.txt       # Python dependencies
├── credentials.json      # OAuth client secrets (git-ignored)
├── token.json           # User access tokens (git-ignored)
├── bin/
│   └── web-content-parser # Global command wrapper
└── README.md
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
- **Always tries both extraction methods** (trafilatura + multi-div) on each HTML source
- **Compares all 4 combinations** and uses the longest result
- **No manual mode selection needed** - fully automatic

### OAuth Scopes

- `documents` - create and edit Google Docs
- `drive` - access Drive folders and create files

## Security

Sensitive files are excluded from git via `.gitignore`:

- `credentials.json` - OAuth client secrets
- `token.json` - User access/refresh tokens

**Never commit these files to version control.**

## How It Works

### 1. **Authentication** (`auth.py`):

- First run: opens browser for OAuth consent
- Saves tokens to `token.json` for automatic refresh
- Creates authenticated Google Docs & Drive API clients

### 2. **Parallel Fetching** (`fetch_markdown.py`):

- Launches headless Chromium browser (Playwright)
- For each URL, runs **both methods in parallel**:
  - **aiohttp**: Fast HTTP GET, static HTML (~1-2s)
  - **Playwright**: Headless browser, executes JavaScript, waits for content (~5-8s)
- Both complete simultaneously; tool waits for both results

### 3. **Dual Extraction**:

- Runs **both extraction methods** on each HTML source:
  - **trafilatura**: Content scoring algorithm, finds single best content block
    - Smart noise filtering (ads, navigation, footers)
    - Content density analysis
    - Returns clean markdown
  - **multi-div**: XPath pattern matching, combines ALL matching containers
    - Patterns: `post-content`, `article-content`, `article-body`, etc.
    - Handles split articles (e.g., Ars Technica with 26 separate divs)
    - Converts combined HTML to markdown with html2text

### 4. **Maximum Content Selection**:

- Compares **all 4 results**:
  1.  aiohttp HTML + trafilatura extraction
  2.  aiohttp HTML + multi-div extraction
  3.  Playwright HTML + trafilatura extraction
  4.  Playwright HTML + multi-div extraction
- Uses whichever got the **longest content** (measured in characters)
- Tracks winning method for logging

### 5. **Document Creation**:

- Builds a one-time Drive title cache (recursive folder scan)
- Reuses existing Docs by title if found; otherwise creates a new document
- Parses markdown with `markdown-it-py`
- Converts to Google Docs API `batchUpdate` requests
- Inserts the document title as **TITLE** and the clickable **Source** link at top
- Applies formatting with Docs API in single batch operation

### 6. **Formatting** (`docs_converter.py`):

- Reverse insertion strategy (insertions from end to start)
- Handles headings (H1-H6), bold, italic, links, lists, code blocks
- Preserves markdown structure as native Google Docs elements

## Why This Approach?

### Hybrid Fetching (aiohttp + Playwright)

- **aiohttp wins 82% of the time** - faster for static sites
- **Playwright needed for 18%** - JavaScript-heavy sites (Medium, LinkedIn, etc.)
- **Parallel execution** - total time ≈ slower method, not both added together
- **Some sites block Playwright** - aiohttp bypasses simple bot detection

### Dual Extraction (trafilatura + multi-div)

- **trafilatura** - best for standard articles, smart noise filtering
- **multi-div** - catches split-content articles trafilatura misses
- **Both complement each other** - different strengths for different site structures

### Real-World Results

- **60 URLs tested**: 100% success rate
- **Average**: 4.6s per document
- **Longest article**: 69,966 chars (Backlinko guide)
- **Mix**: 49 used aiohttp, 11 needed Playwright
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

The tool already uses maximum extraction:

- Runs both aiohttp and Playwright in parallel
- Tries both trafilatura and multi-div on each
- Uses the longest result automatically

If content is still incomplete, the site may:

- Use client-side rendering that Playwright can't access
- Block automated access with sophisticated bot detection
- Require authentication/subscription
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
