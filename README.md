# Web Content Parser

A Python CLI tool to fetch markdown versions of web pages using [into.md](https://into.md/) and automatically create formatted Google Docs in your Drive.

## Features

- **Concurrent fetching** of unlimited URLs using async/await
- **Google Docs integration** - automatically creates formatted documents in your Drive
- **Smart formatting** - converts markdown to native Google Docs formatting (headings, bold, italic, links, lists)
- **Automatic title extraction** from markdown (first H1)
- **Duplicate detection** - adds (2), (3) suffixes for docs with same title
- **Real-time progress bar** showing completion status and speed
- **OAuth authentication** - one-time browser login, then automatic for future runs
- **Resilient error handling** - continues processing even if some URLs fail

## Prerequisites

- Python 3.10+
- Google Cloud account (free)
- Google Drive folder named "Resources" (or customize in code)

## Installation

### 1. Install dependencies

Using conda (recommended):

```bash
conda install -c conda-forge aiohttp tqdm google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client markdown-it-py
```

Or using pip:

```bash
pip install -r requirements.txt
```

### 2. Set up Google Cloud OAuth

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

### 3. Configure target folder (optional)

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
3. Fetches markdown from into.md for each URL concurrently
4. Extracts article title from first H1 heading
5. Creates formatted Google Doc in your Resources folder
6. Applies markdown formatting (headings, bold, italic, links, lists)
7. Returns shareable document URL

### Example with multiple URLs:

```bash
python fetch_markdown.py \
  "https://arstechnica.com/tech-policy/2024/01/..." \
  "https://www.theverge.com/2024/1/15/..." \
  "https://techcrunch.com/2024/01/15/..."
```

## Output

**Progress Output:**

```
Locating Google Drive folder...
✓ Found 'Resources' folder

Fetching & Creating: 100%|████████████| 3/3 [00:08<00:00, 2.67s/doc]

[OK] (title) https://example.com/article -> https://docs.google.com/document/d/...

✓ Done: 3/3 succeeded.
```

All Google Docs are created in your **Resources** folder with:

- Native Google Docs formatting (not plain text)
- Proper headings, bold, italic, links, lists, code blocks
- Duplicate handling: `Title`, `Title (2)`, `Title (3)`, etc.

## Project Structure

```
web-content-parser/
├── fetch_markdown.py      # Main CLI script
├── auth.py                # OAuth authentication & Google API clients
├── docs_converter.py      # Markdown → Google Docs formatting converter
├── requirements.txt       # Python dependencies
├── credentials.json      # OAuth client secrets (git-ignored)
├── token.json           # User access tokens (git-ignored)
└── README.md
```

## Configuration

- **Timeout**: 30 seconds per request
- **Target folder**: "Resources" in Google Drive (hardcoded in `fetch_markdown.py`)
- **Concurrency**: Unlimited - all URLs are fetched simultaneously
- **OAuth scopes**:
  - `documents` - create and edit Google Docs
  - `drive` - access Drive folders and create files

## Security

Sensitive files are excluded from git via `.gitignore`:

- `credentials.json` - OAuth client secrets
- `token.json` - User access/refresh tokens

**Never commit these files to version control.**

## How It Works

1. **Authentication** (`auth.py`):

   - First run: opens browser for OAuth consent
   - Saves tokens to `token.json` for automatic refresh
   - Creates authenticated Google Docs & Drive API clients

2. **Fetching** (`fetch_markdown.py`):

   - Uses `aiohttp` for concurrent HTTP requests to into.md
   - Each URL is processed independently with timeout protection
   - Failures don't block other URLs

3. **Document Creation**:

   - Parses markdown with `markdown-it-py`
   - Converts to Google Docs API `batchUpdate` requests
   - Creates doc via Drive API (bypasses service account quota issues)
   - Applies formatting with Docs API in single batch operation

4. **Formatting** (`docs_converter.py`):
   - Reverse insertion strategy (insertions from end to start)
   - Handles headings (H1-H6), bold, italic, links, lists, code blocks
   - Preserves markdown structure as native Google Docs elements

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

### Formatting issues

- The script uses a simplified markdown parser
- Complex nested structures may not render perfectly
- Manual touch-ups in Google Docs may be needed for some articles
