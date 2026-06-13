import os
import re
import tempfile
from pathlib import Path

import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Users\wadar\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
from PIL import Image, ImageEnhance

MIN_BIB_CONFIDENCE = float(os.getenv("MIN_BIB_CONFIDENCE", "60"))
MAX_DIMENSION = 1080


def preprocess_image(image_path: str) -> str:
    """
    Prepare an image for OCR:
    - Convert to grayscale
    - Boost contrast
    - Resize to max 1080px on longest side (preserving aspect ratio)
    - Save to a temp file and return its path
    """
    img = Image.open(image_path).convert("L")  # grayscale

    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(2.0)

    w, h = img.size
    if max(w, h) > MAX_DIMENSION:
        scale = MAX_DIMENSION / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    tmp.close()
    return tmp.name


def extract_bib(image_path: str) -> list[dict]:
    """
    Run Tesseract OCR on an image and extract BIB numbers.

    Returns a list of dicts:
        {
            "bib_number": str,          # 3–5 digit number
            "confidence": float,        # Tesseract confidence 0–100
            "bounding_box": {x, y, w, h}
        }

    Filters:
        - digits only (no letters)
        - length 3–5 digits
        - confidence > MIN_BIB_CONFIDENCE (env, default 60)
    """
    preprocessed_path = preprocess_image(image_path)

    try:
        data = pytesseract.image_to_data(
            preprocessed_path,
            output_type=pytesseract.Output.DICT,
            config="--psm 11 --oem 3 -c tessedit_char_whitelist=0123456789",
        )
    finally:
        Path(preprocessed_path).unlink(missing_ok=True)

    results = []
    seen = set()

    for i, text in enumerate(data["text"]):
        text = text.strip()
        if not text:
            continue

        # digits only, 3–5 characters
        if not re.fullmatch(r"\d{3,5}", text):
            continue

        conf = float(data["conf"][i])
        if conf < MIN_BIB_CONFIDENCE:
            continue

        # deduplicate same bib in same image
        if text in seen:
            continue
        seen.add(text)

        results.append(
            {
                "bib_number": text,
                "confidence": round(conf/100, 4),
                "bounding_box": {
                    "x": data["left"][i],
                    "y": data["top"][i],
                    "w": data["width"][i],
                    "h": data["height"][i],
                },
            }
        )

    return results
