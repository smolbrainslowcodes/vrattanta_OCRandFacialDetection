import os
import requests

COMPREFACE_URL = os.getenv("COMPREFACE_URL", "http://localhost:8000")
COMPREFACE_API_KEY = os.getenv("COMPREFACE_API_KEY", "")


def extract_face_embedding(image_path: str) -> list[dict] | None:
    """
    Send an image to CompreFace detection endpoint and return all detected
    face embeddings.

    Uses /detection/detect with the calculator plugin, which returns embeddings
    without needing a subject collection — ideal for batch processing event
    photos where we store vectors ourselves in pgvector.

    Returns a list of dicts (one per detected face):
        {
            "embedding": [float, ...],   # 512-dim vector
            "confidence": float,         # face detection probability
            "bounding_box": {x, y, w, h}
        }

    Returns None if CompreFace is unreachable, returns an error, or detects
    no faces in the image.
    """
    if not COMPREFACE_API_KEY:
        print("[FACE] COMPREFACE_API_KEY not set — skipping face embedding")
        return None

    url = f"{COMPREFACE_URL}/api/v1/detection/detect"
    headers = {"x-api-key": COMPREFACE_API_KEY}
    params = {
        "face_plugins": "calculator",  # calculator plugin returns the embedding
        "limit": 0,  # 0 = no limit on number of faces returned.
        # CompreFace 1.2.0 has a bug where omitting `limit` can incorrectly
        # raise "No face is found" even when a face IS detected internally
        # (visible in compreface-core logs as a BoundingBoxDTO with a valid
        # probability, immediately followed by a NoFaceFoundError). Passing
        # limit=0 explicitly avoids that code path.
    }

    try:
        with open(image_path, "rb") as f:
            files = {"file": (os.path.basename(image_path), f, "image/jpeg")}
            response = requests.post(url, headers=headers, params=params, files=files, timeout=30)
    except requests.exceptions.ConnectionError:
        print(f"[FACE] Could not connect to CompreFace at {COMPREFACE_URL} — is it running?")
        return None
    except requests.exceptions.Timeout:
        print(f"[FACE] CompreFace request timed out for {image_path}")
        return None

    if response.status_code != 200:
        print(f"[FACE] CompreFace returned {response.status_code}: {response.text[:200]}")
        return None

    data = response.json()

    # Response shape from /detection/detect with calculator plugin:
    # {
    #   "result": [
    #     {
    #       "box": {
    #         "probability": 0.9997,
    #         "x_max": 1420, "y_max": 1368,
    #         "x_min": 548,  "y_min": 295
    #       },
    #       "plugins_versions": {...},
    #       "embedding": [0.0231, -0.0412, ...]   <- 512-dim float list
    #     },
    #     ... one entry per detected face
    #   ]
    # }

    results = data.get("result", [])
    if not results:
        print(f"[FACE] No faces detected in {image_path}")
        return None

    faces = []
    for face in results:
        embedding = face.get("embedding")
        box = face.get("box", {})
        confidence = box.get("probability", 0.0)

        if not embedding:
            continue

        faces.append({
            "embedding": embedding,
            "confidence": round(float(confidence), 4),
            "bounding_box": {
                "x": box.get("x_min", 0),
                "y": box.get("y_min", 0),
                "w": box.get("x_max", 0) - box.get("x_min", 0),
                "h": box.get("y_max", 0) - box.get("y_min", 0),
            },
        })

    return faces if faces else None


def get_best_face(faces: list[dict]) -> dict | None:
    """
    From a list of detected faces, return the most prominent one —
    the largest by bounding box area (usually the runner closest to camera).
    """
    if not faces:
        return None
    return max(faces, key=lambda f: f["bounding_box"]["w"] * f["bounding_box"]["h"])
