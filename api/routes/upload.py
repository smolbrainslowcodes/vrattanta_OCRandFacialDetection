import os
import uuid
import json
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, HTTPException
from PIL import Image

from db.connection import execute_query
from processing.ocr import extract_bib
from processing.batch import batch_process_event
from api.models import UploadResponse

router = APIRouter()

STORAGE_PATH = Path(os.getenv("STORAGE_PATH", "./storage"))
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/jpg"}


def _save_photo(file_content: bytes, event_id: str, filename: str) -> tuple[Path, Path]:
    """Save original and generate thumbnail. Returns (original_path, thumbnail_path)."""
    original_dir = STORAGE_PATH / event_id / "original"
    thumb_dir = STORAGE_PATH / event_id / "thumbnails"
    original_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex}_{Path(filename).name}"
    original_path = original_dir / safe_name
    original_path.write_bytes(file_content)

    # Generate thumbnail (300px max on longest side)
    img = Image.open(original_path)
    img.thumbnail((300, 300), Image.LANCZOS)
    thumb_path = thumb_dir / safe_name
    img.save(thumb_path)

    return original_path, thumb_path


def _run_ocr_and_store(image_path: str, photo_id: str):
    """Background task: run OCR and store BIB detections in DB."""
    bibs = extract_bib(image_path)
    for bib in bibs:
        execute_query(
            """
            INSERT INTO media_ai.bib_detections
                (photo_id, bib_number, confidence, bounding_box)
            VALUES (%s, %s, %s, %s)
            """,
            (photo_id, bib["bib_number"], bib["confidence"], json.dumps(bib["bounding_box"])),
        )


def _ingest_single(file_content: bytes, filename: str, content_type: str, event_id: str) -> UploadResponse:
    """Core ingestion logic shared by single and bulk upload."""
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"File type not allowed: {content_type}. Use JPEG or PNG.")

    original_path, thumb_path = _save_photo(file_content, event_id, filename)

    with Image.open(original_path) as img:
        width, height = img.size

    photo_id = str(uuid.uuid4())
    image_url = str(original_path)
    thumbnail_url = str(thumb_path)
    now = datetime.utcnow()

    execute_query(
        """
        INSERT INTO media_ai.event_photos
            (photo_id, event_id, image_url, thumbnail_url, width, height, captured_at, uploaded_at, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (photo_id, event_id, image_url, thumbnail_url, width, height, now, now, "upload"),
    )

    return UploadResponse(
        photo_id=photo_id,
        event_id=event_id,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        width=width,
        height=height,
        uploaded_at=now,
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_photo(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    event_id: str = Form(...),
):
    content = await file.read()
    response = _ingest_single(content, file.filename, file.content_type, event_id)
    background_tasks.add_task(_run_ocr_and_store, response.image_url, response.photo_id)
    background_tasks.add_task(batch_process_event, event_id)
    return response


@router.post("/upload/bulk", response_model=list[UploadResponse])
async def upload_bulk(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    event_id: str = Form(...),
):
    responses = []
    for file in files:
        content = await file.read()
        try:
            resp = _ingest_single(content, file.filename, file.content_type, event_id)
            background_tasks.add_task(_run_ocr_and_store, resp.image_url, resp.photo_id)
            responses.append(resp)
        except HTTPException as e:
            # Log and skip bad files in bulk upload rather than aborting everything
            print(f"[UPLOAD] Skipping {file.filename}: {e.detail}")

    # Trigger face embedding for all uploaded photos once the loop is done
    background_tasks.add_task(batch_process_event, event_id)
    return responses
