"""
Cleanup task — deletes expired selfie images from disk and nulls their DB paths.

Selfies older than 24 hours are deleted; only the face embedding is retained.
This runs on a schedule via APScheduler (configured in api/main.py).
"""

import os
from pathlib import Path

from db.connection import execute_query


def delete_expired_selfies() -> int:
    """
    Find search_requests where:
        - search_image_url is not NULL (selfie was stored)
        - created_at is older than 24 hours

    For each:
        1. Delete the file from disk if it exists
        2. Set search_image_url = NULL in the DB

    Returns the number of selfies deleted.
    """
    expired = execute_query(
        """
        SELECT search_id, search_image_url
        FROM media_ai.search_requests
        WHERE search_image_url IS NOT NULL
          AND created_at < NOW() - INTERVAL '24 hours'
        """,
        fetch="all",
    )

    if not expired:
        return 0

    deleted_count = 0

    for search_id, image_url in expired:
        # Delete file from disk
        if image_url:
            file_path = Path(image_url)
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError as e:
                    print(f"[CLEANUP] Failed to delete {image_url}: {e}")

        # Null out the path in DB regardless of whether file existed
        execute_query(
            "UPDATE media_ai.search_requests SET search_image_url = NULL WHERE search_id = %s",
            (str(search_id),),
        )
        deleted_count += 1

    print(f"[CLEANUP] Deleted {deleted_count} expired selfie(s)")
    return deleted_count
