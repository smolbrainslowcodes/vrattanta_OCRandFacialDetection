from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class UploadResponse(BaseModel):
    photo_id: str
    event_id: str
    image_url: str
    thumbnail_url: Optional[str]
    width: Optional[int]
    height: Optional[int]
    uploaded_at: datetime


class BibDetectionResult(BaseModel):
    detection_id: str
    bib_number: str
    confidence: float
    bounding_box: Optional[dict]


class PhotoMatch(BaseModel):
    photo_id: str
    event_id: str
    image_url: str
    thumbnail_url: Optional[str]
    confidence: Optional[float]


class BibSearchResponse(BaseModel):
    event_id: str
    bib_number: str
    total_matches: int
    photos: list[PhotoMatch]


class FaceSearchResponse(BaseModel):
    event_id: str
    total_matches: int
    photos: list[PhotoMatch]


class ConsentRequest(BaseModel):
    consent: bool


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
