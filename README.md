# Web Content Parser

A Python CLI tool to fetch markdown versions of web pages using [into.md](https://into.md/) and save them locally.

## Features

- Fully concurrent fetching of unlimited URLs using async/await
- Automatic title extraction from markdown (first H1)
- Smart filename sanitization and duplicate handling
- Real-time progress bar showing completion status and speed
- Configurable output directory via config file
- Resilient error handling - continues processing even if some URLs fail

## Installation

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Set up configuration:

```bash
cp config.example.ini config.ini
```

3. Edit `config.ini` and set your desired output directory:

```ini
[Paths]
output_directory = /path/to/your/output/folder
```

## Usage

Pass one or more URLs as command-line arguments:

```bash
python fetch_markdown.py "https://example.com/article1" "https://example.com/article2"
```

The script will:

1. Convert each URL to use into.md's API (`https://into.md/https:/example.com/...`)
2. Fetch all markdown content concurrently with a real-time progress bar
3. Extract the article title from the first `#` heading
4. Save each article as `Title.md` in your configured output directory

**Example with multiple URLs:**

```bash
python fetch_markdown.py \
  "https://example.com/article1" \
  "https://example.com/article2" \
  "https://example.com/article3"
```

## Output

All markdown files are saved to the directory specified in `config.ini`. If two articles have the same title, they'll be saved as `Title.md`, `Title (2).md`, etc.

**Progress Output:**

```
Fetching: 100%|████████████████| 7/7 [00:03<00:00, 2.11url/s]

[OK] (title) https://example.com/article1 -> /path/to/Article Title.md
[OK] (fallback) https://example.com/article2 -> /path/to/example.com - article2.md

✓ Done: 7/7 succeeded.
```

## Configuration

- **Timeout**: 30 seconds per request
- **Output directory**: Configured in `config.ini` (defaults to `markdown/` if not set)
- **Concurrency**: Unlimited - all URLs are fetched simultaneously

## How It Works

The script uses Python's `asyncio` and `aiohttp` for concurrent HTTP requests, allowing it to fetch many URLs simultaneously without blocking. Each request has an independent timeout, and failures don't affect other URLs in progress.
