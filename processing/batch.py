"""
Batch processing pipeline — runs OCR and face embedding on all photos for an event.

Usage:
    python processing/batch.py <event_id>

This is what you run after a photographer bulk-uploads photos post-event.
"""

import sys
import json
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import execute_query
from processing.ocr import extract_bib
from processing.face import extract_face_embedding


def batch_process_event(event_id: str) -> dict:
    """
    Process all photos for an event:
    1. OCR — extract BIB numbers for any photo not yet processed
    2. Face embedding — extract face vectors for any photo not yet processed

    Returns a summary dict with counts.
    """
    photos = execute_query(
        "SELECT photo_id, image_url FROM media_ai.event_photos WHERE event_id = %s",
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

    for i, (photo_id, image_url) in enumerate(photos, start=1):
        # image_url is stored as the path relative to STORAGE_PATH
        # When running locally, resolve against the project root
        image_path = image_url if Path(image_url).exists() else str(Path(".") / image_url.lstrip("/"))

        # ── OCR ──────────────────────────────────────────────────────────────
        existing_bib = execute_query(
            "SELECT detection_id FROM media_ai.bib_detections WHERE photo_id = %s LIMIT 1",
            (photo_id,),
            fetch="one",
        )

        if existing_bib:
            print(f"[OCR] photo {i}/{total} — already processed, skipping")
        else:
            if Path(image_path).exists():
                bibs = extract_bib(image_path)
                bib_numbers = [b["bib_number"] for b in bibs]
                print(f"[OCR] photo {i}/{total} — found BIBs: {bib_numbers}")

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
                ocr_processed += 1
            else:
                print(f"[OCR] photo {i}/{total} — image file not found at {image_path}, skipping")

        # ── Face Embedding ───────────────────────────────────────────────────
        existing_face = execute_query(
            "SELECT face_id FROM media_ai.face_embeddings WHERE photo_id = %s LIMIT 1",
            (photo_id,),
            fetch="one",
        )

        if existing_face:
            print(f"[FACE] photo {i}/{total} — already processed, skipping")
        else:
            if Path(image_path).exists():
                faces = extract_face_embedding(image_path)

                if faces is None:
                    print(f"[FACE] photo {i}/{total} — stub returned None (Week 3 pending)")
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
                    face_processed += 1
                    print(f"[FACE] photo {i}/{total} — stored {len(faces)} face(s)")

    summary = {
        "photos_found": total,
        "ocr_processed": ocr_processed,
        "face_processed": face_processed,
    }
    print(f"[BATCH] Done — {summary}")
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python processing/batch.py <event_id>")
        sys.exit(1)

    event_id = sys.argv[1]
    batch_process_event(event_id)
