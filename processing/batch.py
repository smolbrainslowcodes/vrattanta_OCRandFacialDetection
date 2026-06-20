"""
Batch processing pipeline — runs OCR and face embedding on all photos for an event.

Usage:
    python processing/batch.py <event_id>

This is what you run after a photographer bulk-uploads photos post-event.
"""

import os
import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import execute_query
from processing.ocr import extract_bib
from processing.face import extract_face_embedding

# Tesseract runs as a subprocess per call, so OCR calls can run concurrently
# without fighting over Python's GIL — limited by host CPU, not by an
# external service.
OCR_WORKERS = int(os.getenv("OCR_WORKERS", "4"))

# CompreFace (docker-compose.yml) is configured for UWSGI_PROCESSES=2,
# UWSGI_THREADS=1 — it can only actually work on 2 requests at once
# regardless of how many we send. Raise this only after raising that.
FACE_WORKERS = int(os.getenv("FACE_WORKERS", "2"))

# How old an unattempted photo needs to be before the periodic sweep
# (see sweep_stalled_events) treats it as abandoned rather than "upload
# session still in progress."
SWEEP_GRACE_MINUTES = int(os.getenv("SWEEP_GRACE_MINUTES", "15"))

# How old a batch_runs claim row needs to be before it's treated as
# abandoned (e.g. the process crashed mid-run) and safe to reclaim.
BATCH_RUN_STALE_MINUTES = int(os.getenv("BATCH_RUN_STALE_MINUTES", "60"))


def _record_failure(photo_id: str, stage: str, error: Exception):
    execute_query(
        """
        INSERT INTO media_ai.processing_errors (photo_id, stage, error_message)
        VALUES (%s, %s, %s)
        """,
        (photo_id, stage, str(error)),
    )


def _mark_attempted(photo_id: str, column: str):
    """column must be 'ocr_attempted_at' or 'face_attempted_at' — both are
    hardcoded call sites below, never user input."""
    execute_query(
        f"UPDATE media_ai.event_photos SET {column} = NOW() WHERE photo_id = %s",
        (photo_id,),
    )


def _process_ocr(photo_id: str, image_path: str) -> bool:
    """Run OCR for one photo and store results. Returns True on success."""
    success = False
    try:
        bibs = extract_bib(image_path)
        bib_numbers = [b["bib_number"] for b in bibs]
        print(f"[OCR] photo {photo_id} — found BIBs: {bib_numbers}")

        for bib in bibs:
            execute_query(
                """
                INSERT INTO media_ai.bib_detections
                    (photo_id, bib_number, confidence, bounding_box)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    photo_id,
                    bib["bib_number"],
                    bib["confidence"],
                    json.dumps(bib["bounding_box"]),
                ),
            )
        success = True
    except Exception as e:
        print(f"[OCR] photo {photo_id} — failed ({e}), skipping")
        _record_failure(photo_id, "ocr", e)

    # Mark attempted regardless of outcome — including "found nothing" —
    # so we never retry this photo's OCR again on a future sweep/re-run.
    _mark_attempted(photo_id, "ocr_attempted_at")
    return success


def _process_face(photo_id: str, image_path: str) -> bool:
    """Run face embedding for one photo and store results. Returns True on success."""
    success = False
    try:
        faces = extract_face_embedding(image_path)

        if faces is None:
            print(f"[FACE] photo {photo_id} — no embedding returned, skipping")
        else:
            for face in faces:
                execute_query(
                    """
                    INSERT INTO media_ai.face_embeddings
                        (photo_id, embedding, confidence, face_box)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        photo_id,
                        face["embedding"],
                        face.get("confidence"),
                        json.dumps(face.get("bounding_box")),
                    ),
                )
            print(f"[FACE] photo {photo_id} — stored {len(faces)} face(s)")
            success = True
    except Exception as e:
        print(f"[FACE] photo {photo_id} — failed ({e}), skipping")
        _record_failure(photo_id, "face", e)

    _mark_attempted(photo_id, "face_attempted_at")
    return success


def _claim_batch_run(event_id: str) -> bool:
    """
    Claim the right to run a batch for this event, so two triggers firing
    around the same time (e.g. the periodic sweep and a manual admin
    trigger) can't both spin up a full OCR/face worker pool for the same
    event at once. A stale claim (older than BATCH_RUN_STALE_MINUTES —
    e.g. a previous run crashed mid-way) is reclaimed automatically.
    Returns False if another run currently holds the claim.
    """
    claimed = execute_query(
        """
        INSERT INTO media_ai.batch_runs (event_id, started_at)
        VALUES (%s, NOW())
        ON CONFLICT (event_id) DO UPDATE
            SET started_at = NOW()
            WHERE media_ai.batch_runs.started_at < NOW() - (%s * INTERVAL '1 minute')
        RETURNING event_id
        """,
        (event_id, BATCH_RUN_STALE_MINUTES),
        fetch="one",
    )
    return claimed is not None


def _release_batch_run(event_id: str):
    execute_query("DELETE FROM media_ai.batch_runs WHERE event_id = %s", (event_id,))


def batch_process_event(event_id: str) -> dict:
    """
    Process all photos for an event:
    1. OCR — extract BIB numbers for any photo not yet attempted (OCR_WORKERS at a time)
    2. Face embedding — extract face vectors for any photo not yet attempted (FACE_WORKERS at a time)

    OCR and face embedding don't depend on each other's output, so both run
    concurrently rather than as separate sequential phases — face embedding
    for earlier photos overlaps with OCR still running on later ones.

    Safe to call repeatedly for the same event (e.g. from the periodic
    sweep) — photos already attempted are skipped, and only one run per
    event can be active at a time.

    Returns a summary dict with counts.
    """
    if not _claim_batch_run(event_id):
        print(f"[BATCH] Event {event_id} already has a run in progress, skipping")
        return {"photos_found": 0, "ocr_processed": 0, "face_processed": 0, "skipped": "already_running"}

    try:
        photos = execute_query(
            """
            SELECT photo_id, image_url, ocr_attempted_at, face_attempted_at
            FROM media_ai.event_photos WHERE event_id = %s
            """,
            (event_id,),
            fetch="all",
        )

        if not photos:
            print(f"[BATCH] No photos found for event {event_id}")
            return {"photos_found": 0, "ocr_processed": 0, "face_processed": 0}

        total = len(photos)
        ocr_processed = 0
        face_processed = 0

        print(f"[BATCH] Starting processing for event {event_id} — {total} photos")

        # image_url is stored as the path relative to STORAGE_PATH
        # When running locally, resolve against the project root
        resolved = [
            (
                photo_id,
                image_url if Path(image_url).exists() else str(Path(".") / image_url.lstrip("/")),
                ocr_attempted_at,
                face_attempted_at,
            )
            for photo_id, image_url, ocr_attempted_at, face_attempted_at in photos
        ]

        # ── Figure out what actually needs doing ────────────────────────────
        ocr_targets = []
        face_targets = []
        for photo_id, image_path, ocr_attempted_at, face_attempted_at in resolved:
            file_exists = Path(image_path).exists()

            if ocr_attempted_at is not None:
                print(f"[OCR] photo {photo_id} — already attempted, skipping")
            elif not file_exists:
                print(f"[OCR] photo {photo_id} — image file not found at {image_path}, skipping")
            else:
                ocr_targets.append((photo_id, image_path))

            if face_attempted_at is not None:
                print(f"[FACE] photo {photo_id} — already attempted, skipping")
            elif not file_exists:
                print(f"[FACE] photo {photo_id} — image file not found at {image_path}, skipping")
            else:
                face_targets.append((photo_id, image_path))

        # ── Run OCR and face embedding concurrently ─────────────────────────
        print(
            f"[BATCH] {len(ocr_targets)} photo(s) need OCR ({OCR_WORKERS} worker(s)), "
            f"{len(face_targets)} need face embedding ({FACE_WORKERS} worker(s))"
        )

        with ThreadPoolExecutor(max_workers=OCR_WORKERS) as ocr_pool, \
             ThreadPoolExecutor(max_workers=FACE_WORKERS) as face_pool:

            future_stage = {}
            for photo_id, image_path in ocr_targets:
                future_stage[ocr_pool.submit(_process_ocr, photo_id, image_path)] = "ocr"
            for photo_id, image_path in face_targets:
                future_stage[face_pool.submit(_process_face, photo_id, image_path)] = "face"

            for future in as_completed(future_stage):
                if not future.result():
                    continue
                if future_stage[future] == "ocr":
                    ocr_processed += 1
                else:
                    face_processed += 1

        summary = {
            "photos_found": total,
            "ocr_processed": ocr_processed,
            "face_processed": face_processed,
        }
        print(f"[BATCH] Done — {summary}")
        return summary
    finally:
        _release_batch_run(event_id)


def sweep_stalled_events():
    """
    Catches events with photos that were uploaded but never got OCR'd or
    face-embedded — e.g. the photographer's browser closed before the
    upload portal's auto-trigger (and its retries) could fire. Runs on a
    schedule (see api/main.py).

    Cheap when there's nothing to do: the query below only scans photos
    that haven't been attempted yet (backed by a partial index in
    db/schema.sql), and batch_process_event() is a near-no-op for events
    that are already fully processed or already mid-run.
    """
    stalled = execute_query(
        """
        SELECT DISTINCT event_id
        FROM media_ai.event_photos
        WHERE (ocr_attempted_at IS NULL OR face_attempted_at IS NULL)
          AND uploaded_at < NOW() - (%s * INTERVAL '1 minute')
        """,
        (SWEEP_GRACE_MINUTES,),
        fetch="all",
    )

    if not stalled:
        return

    print(f"[SWEEP] {len(stalled)} event(s) have unprocessed photos — triggering batch runs")
    for (event_id,) in stalled:
        print(f"[SWEEP] Triggering batch processing for event {event_id}")
        batch_process_event(str(event_id))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python processing/batch.py <event_id>")
        sys.exit(1)

    event_id = sys.argv[1]
    batch_process_event(event_id)
