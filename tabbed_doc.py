"""
Create a single Google Doc with one tab per page.

Uses the Google Docs API tabs feature: creates the doc, adds named tabs
for all pages via batchUpdate, inserts content per-tab, then deletes
the empty default tab.
"""

import asyncio
import copy
import sys

from auth import get_docs_service, get_drive_service
from docs_converter import convert_markdown_to_doc_requests
from google_drive import sanitize_doc_title
from recursive_crawler import normalize_url


def _inject_tab_id(requests: list[dict], tab_id: str) -> list[dict]:
    """Add tabId to all location/range objects in batchUpdate requests.

    Walks each request dict recursively and injects 'tabId' into every
    'location' and 'range' dict found. Returns a deep copy — does not
    mutate the input.
    """
    patched = copy.deepcopy(requests)

    def _walk(obj):
        if isinstance(obj, dict):
            if 'index' in obj and 'tabId' not in obj:
                # This looks like a location dict: {index: N}
                obj['tabId'] = tab_id
            if 'startIndex' in obj and 'endIndex' in obj and 'tabId' not in obj:
                # This looks like a range dict: {startIndex, endIndex}
                obj['tabId'] = tab_id
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(patched)
    return patched


async def create_tabbed_google_doc(
    pages: list[tuple[str, str, str]],
    doc_title: str,
    folder_id: str,
    base_url: str | None = None,
) -> str:
    """Create a single Google Doc with one tab per page.

    Args:
        pages: List of (url, title, markdown_with_source) tuples,
               ordered by desired tab position.
        doc_title: Title of the overall Google Doc.
        base_url: The base URL prefix used for recursive crawl (for metadata tagging).
        folder_id: Google Drive folder ID to place the doc in.

    Returns:
        URL of the created Google Doc.
    """
    docs_service = get_docs_service()
    drive_service = get_drive_service()

    # Check for existing doc with the same title (direct query, fast)
    safe_title = doc_title.replace("'", "\\'")
    query = (
        f"name='{safe_title}' and '{folder_id}' in parents "
        f"and mimeType='application/vnd.google-apps.document' and trashed=false"
    )
    existing = await asyncio.to_thread(
        lambda: drive_service.files().list(q=query, fields='files(id)', pageSize=1).execute()
    )
    if existing.get('files'):
        existing_id = existing['files'][0]['id']
        doc_url = f"https://docs.google.com/document/d/{existing_id}/edit"
        print(f"  ↺ Existing doc found for '{doc_title}', reusing: {doc_url}", file=sys.stderr)
        return doc_url

    # Step 1: Create blank doc via Drive API
    file_metadata = {
        'name': doc_title,
        'mimeType': 'application/vnd.google-apps.document',
    }
    doc = await asyncio.to_thread(
        lambda: drive_service.files().create(body=file_metadata, fields='id').execute()
    )
    doc_id = doc['id']

    # Step 2: Move to target folder and tag with metadata
    update_body = {}
    if base_url:
        update_body['appProperties'] = {
            'doc_mode': 'tabbed',
            'base_url': normalize_url(base_url)[:124],
        }
    await asyncio.to_thread(
        lambda: drive_service.files().update(
            fileId=doc_id,
            addParents=folder_id,
            removeParents='root',
            body=update_body,
            fields='id, parents',
        ).execute()
    )

    print(f"  Created doc: {doc_title}", file=sys.stderr)

    # Step 3: Get the default tab ID (we'll delete it later)
    doc_info = await asyncio.to_thread(
        lambda: docs_service.documents().get(
            documentId=doc_id, fields='tabs'
        ).execute()
    )
    default_tab_id = doc_info['tabs'][0]['tabProperties']['tabId']

    # Step 4: Create named tabs for ALL pages in a single batchUpdate
    # Google Docs requires unique tab titles — disambiguate with URL path
    seen_titles: set[str] = set()
    tab_titles: list[str] = []
    for url, title, md in pages:
        tab_title = title[:100]
        if tab_title in seen_titles:
            # Append the last path segment to disambiguate
            from urllib.parse import urlparse
            path = urlparse(url).path.rstrip("/")
            slug = path.split("/")[-1] if "/" in path else path
            tab_title = f"{title[:90]} ({slug})"
        seen_titles.add(tab_title)
        tab_titles.append(tab_title)

    tab_requests = []
    for i, tab_title in enumerate(tab_titles):
        tab_requests.append({
            'addDocumentTab': {
                'tabProperties': {
                    'title': tab_title,
                    'index': i,
                }
            }
        })

    tab_response = await asyncio.to_thread(
        lambda: docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={'requests': tab_requests},
        ).execute()
    )

    # Step 5: Extract tab IDs from response
    tab_ids = []
    for reply in tab_response.get('replies', []):
        if 'addDocumentTab' in reply:
            tab_id = reply['addDocumentTab']['tabProperties']['tabId']
            tab_ids.append(tab_id)

    # Step 6: Insert content into each tab
    for i, (tab_id, (url, title, md)) in enumerate(zip(tab_ids, pages), start=1):
        requests = convert_markdown_to_doc_requests(md, doc_title=title)
        if requests:
            patched = _inject_tab_id(requests, tab_id)
            try:
                await asyncio.to_thread(
                    lambda patched=patched: docs_service.documents().batchUpdate(
                        documentId=doc_id,
                        body={'requests': patched},
                    ).execute()
                )
            except Exception as e:
                print(f"  [{i}/{len(pages)}] FAILED: {title} ({e})", file=sys.stderr)
                continue

        print(f"  [{i}/{len(pages)}] {title}", file=sys.stderr)

        # Rate limit between tab insertions
        await asyncio.sleep(0.5)

    # Step 7: Delete the empty default tab
    try:
        await asyncio.to_thread(
            lambda: docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': [{'deleteDocumentTab': {'tabId': default_tab_id}}]},
            ).execute()
        )
    except Exception:
        pass  # Non-critical — just leaves an empty "Tab 1"

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    return doc_url
