"""
Google Drive Operations Module

Handles document creation, caching, and folder management for Google Drive.
"""
import asyncio
import re
import sys
from collections import deque

from auth import get_docs_service, get_drive_service
from docs_converter import convert_markdown_to_doc_requests


def sanitize_doc_title(name: str) -> str:
    """
    Sanitize a string to be a valid Google Docs title.
    Similar to sanitize_filename but for document titles.
    """
    name = name.strip()
    # Normalize whitespace
    name = re.sub(r"\s+", " ", name)
    # Remove problematic characters
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    # Limit length
    name = name[:200]
    return name or "Untitled"


def _find_existing_doc_id_recursive_sync(
    drive_service,
    root_folder_id: str,
    title: str
) -> str | None:
    """
    Synchronous helper: Recursively search for a document by title in a folder
    and all nested subfolders.

    Args:
        drive_service: Authenticated Google Drive service
        root_folder_id: ID of the root folder to search in
        title: Document title to find

    Returns:
        str | None: Document ID if found, otherwise None
    """
    escaped_title = title.replace("'", "\\'")
    doc_query_template = (
        "name='{title}' and '{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.document' and trashed=false"
    )
    folder_query_template = (
        "'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )

    queue = deque([root_folder_id])
    visited = set()

    while queue:
        folder_id = queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        # Check for a matching document in this folder
        doc_query = doc_query_template.format(title=escaped_title, folder_id=folder_id)
        doc_results = drive_service.files().list(
            q=doc_query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=1
        ).execute()

        doc_files = doc_results.get('files', [])
        if doc_files:
            return doc_files[0].get('id')

        # Queue subfolders
        page_token = None
        while True:
            folder_query = folder_query_template.format(folder_id=folder_id)
            folder_results = drive_service.files().list(
                q=folder_query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for folder in folder_results.get('files', []):
                folder_id_child = folder.get('id')
                if folder_id_child:
                    queue.append(folder_id_child)

            page_token = folder_results.get('nextPageToken')
            if not page_token:
                break

    return None


def _build_doc_title_cache_sync(
    drive_service,
    root_folder_id: str
) -> dict[str, tuple[str, bool]]:
    """
    Synchronous helper: Build a cache of document titles to IDs by
    recursively traversing the root folder and all subfolders.

    Args:
        drive_service: Authenticated Google Drive service
        root_folder_id: ID of the root folder to search in

    Returns:
        dict[str, tuple[str, bool]]: Mapping of document title -> (document ID, created_this_run)
    """
    doc_cache: dict[str, tuple[str, bool]] = {}
    folder_query_template = (
        "'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.folder' and trashed=false"
    )
    doc_query_template = (
        "'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.document' and trashed=false"
    )

    queue = deque([root_folder_id])
    visited = set()

    while queue:
        folder_id = queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        # List documents in this folder
        page_token = None
        while True:
            doc_query = doc_query_template.format(folder_id=folder_id)
            doc_results = drive_service.files().list(
                q=doc_query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for doc in doc_results.get('files', []):
                doc_id = doc.get('id')
                doc_name = doc.get('name')
                if doc_id and doc_name and doc_name not in doc_cache:
                    doc_cache[doc_name] = (doc_id, False)

            page_token = doc_results.get('nextPageToken')
            if not page_token:
                break

        # Queue subfolders
        page_token = None
        while True:
            folder_query = folder_query_template.format(folder_id=folder_id)
            folder_results = drive_service.files().list(
                q=folder_query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageSize=1000,
                pageToken=page_token
            ).execute()

            for folder in folder_results.get('files', []):
                folder_id_child = folder.get('id')
                if folder_id_child:
                    queue.append(folder_id_child)

            page_token = folder_results.get('nextPageToken')
            if not page_token:
                break

    return doc_cache


async def create_google_doc(
    markdown_content: str,
    title: str,
    folder_id: str,
    doc_cache: dict[str, tuple[str, bool]] | None = None,
    cache_lock: asyncio.Lock | None = None
) -> str:
    """
    Create a Google Doc from markdown content.
    Creates fresh API service instances per call to avoid SSL issues
    with concurrent threads. Credentials are cached in auth.py so
    this is cheap.

    Args:
        markdown_content: Raw markdown text
        title: Document title
        folder_id: Google Drive folder ID

    Returns:
        str: URL of the created document
    """
    try:
        # Fresh service instances per call (thread-safe; credentials are cached)
        docs_service = get_docs_service()
        drive_service = get_drive_service()

        existing_doc_id = None
        created_this_run = False
        if doc_cache is not None:
            if cache_lock:
                async with cache_lock:
                    cached = doc_cache.get(title)
            else:
                cached = doc_cache.get(title)
            if cached:
                existing_doc_id, created_this_run = cached
        else:
            # Fallback: recursive search for existing doc by title
            existing_doc_id = await asyncio.to_thread(
                _find_existing_doc_id_recursive_sync, drive_service, folder_id, title
            )

        if existing_doc_id and not created_this_run:
            # Document already exists from a previous run, return its URL
            print(f"↺ Existing doc found for '{title}', reusing.", file=sys.stderr)
            return f"https://docs.google.com/document/d/{existing_doc_id}/edit"

        if existing_doc_id and created_this_run:
            # Reuse the doc created in this run (likely from a previous failed attempt)
            doc_id = existing_doc_id
        else:
            # Create a blank Google Doc without parent (avoids quota issues)
            file_metadata = {
                'name': title,
                'mimeType': 'application/vnd.google-apps.document'
            }

            doc = await asyncio.to_thread(
                lambda: drive_service.files().create(body=file_metadata, fields='id').execute()
            )
            doc_id = doc['id']

        # Update cache immediately to prevent duplicate docs on retries
        if doc_cache is not None:
            if cache_lock:
                async with cache_lock:
                    doc_cache[title] = (doc_id, True)
            else:
                doc_cache[title] = (doc_id, True)

        if not (existing_doc_id and created_this_run):
            # Move it to the target folder and transfer ownership to you
            await asyncio.to_thread(
                lambda: drive_service.files().update(
                    fileId=doc_id,
                    addParents=folder_id,
                    removeParents='root',
                    fields='id, parents'
                ).execute()
            )

        # Convert markdown to Docs API requests
        requests = convert_markdown_to_doc_requests(markdown_content, doc_title=title)

        # Apply all formatting in a single batchUpdate
        if requests:
            await asyncio.to_thread(
                lambda: docs_service.documents().batchUpdate(
                    documentId=doc_id,
                    body={'requests': requests}
                ).execute()
            )

        # Update cache with the newly created doc
        if doc_cache is not None:
            if cache_lock:
                async with cache_lock:
                    doc_cache[title] = (doc_id, False)
            else:
                doc_cache[title] = (doc_id, False)

        # Return shareable URL
        return f"https://docs.google.com/document/d/{doc_id}/edit"

    except Exception as e:
        raise RuntimeError(f"Failed to create Google Doc: {e}")
