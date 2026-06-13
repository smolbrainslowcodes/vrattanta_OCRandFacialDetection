# FitFunda Media AI — Photo Discovery

AI-powered race photo discovery using BIB OCR + Face Search.

**Stack:** Python 3.11 · FastAPI · PostgreSQL + pgvector · Tesseract OCR · Docker

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- Python 3.11+
- Visual Studio Code (recommended)

---

## First Time Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd media-ai

# 2. Create your .env file
cp .env.example .env

# 3. Build and start all services
docker-compose up --build
```

The API will be available at **http://localhost:8000**

---

## Load the Database Schema

After `docker-compose up`, run the schema once to create all tables.

**PowerShell (Windows):**
```powershell
Get-Content db/schema.sql | docker exec -i media_ai_db psql -U media_user -d media_ai
```

**Bash (Mac/Linux):**
```bash
docker exec -i media_ai_db psql -U media_user -d media_ai < db/schema.sql
```

Verify it worked:
```bash
docker exec -i media_ai_db psql -U media_user -d media_ai -c "\dt media_ai.*"
```

You should see 5 tables: `event_photos`, `bib_detections`, `face_embeddings`, `search_requests`, `search_results`.

---

## Daily Startup (Local Dev Mode)

For faster iteration, run the API locally (with hot reload) while keeping only the DB in Docker:

```powershell
# 1. Activate virtual environment (PowerShell)
.\venv\Scripts\Activate.ps1

# If you get an execution policy error, run once:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 2. Start the database container only
docker start media_ai_db

# 3. Start the API with hot reload
uvicorn api.main:app --reload
```

---

## Test the API

**Swagger UI (interactive docs):**
```
http://localhost:8000/docs
```

**Upload a photo (curl):**
```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@/path/to/photo.jpg" \
  -F "event_id=00000000-0000-0000-0000-000000000001"
```

**BIB search:**
```bash
curl "http://localhost:8000/search/bib?event_id=00000000-0000-0000-0000-000000000001&bib=2451"
```

**Health check:**
```bash
curl http://localhost:8000/health
```

---

## Portals

| Portal | URL |
|---|---|
| Photographer Upload Portal | http://localhost:8000/static/upload_portal.html |
| Participant Photo Search | http://localhost:8000/static/search.html |
| API Docs (Swagger) | http://localhost:8000/docs |

> **Note:** The templates are served from the `templates/` folder. If you're running in Docker, they're volume-mounted so changes reflect immediately without rebuild.

---

## Run Batch Processing

After a photographer bulk-uploads photos, run OCR and face processing for the entire event:

```bash
python processing/batch.py 00000000-0000-0000-0000-000000000001
```

Replace the UUID with your actual event ID. The script:
1. Skips photos already processed (safe to re-run)
2. Extracts BIB numbers via Tesseract OCR
3. Attempts face embedding (stub in Week 2 — returns None)
4. Logs progress: `[OCR] photo 12/50 — found BIBs: [2451]`

---

## Admin API Endpoints

**Trigger batch processing (async):**
```bash
curl -X POST http://localhost:8000/admin/batch/00000000-0000-0000-0000-000000000001
```

**Event stats:**
```bash
curl http://localhost:8000/admin/events/00000000-0000-0000-0000-000000000001/stats
```

---

## Selfie Auto-Deletion

Raw selfie images are automatically deleted after 24 hours. Only the face embedding is retained.

The cleanup job runs every hour via APScheduler (starts with the API). To simulate in development, you can call the cleanup function directly:

```python
from processing.cleanup import delete_expired_selfies
delete_expired_selfies()
```

---

## Week 3 — Adding CompreFace (Face Search)

Face search currently returns HTTP 501. When you're ready to implement it:

**1. File to edit:** `processing/face.py`

**2. What to do:** Replace the `extract_face_embedding()` stub with the real implementation. The full code is in the comment block at the top of that file — it includes the exact CompreFace endpoint, request format, response parsing, and how to handle multiple faces per photo.

**3. Add CompreFace to docker-compose.yml:** The service definition is also documented in `processing/face.py`.

**4. Add to .env:**
```
COMPREFACE_API_KEY=your_key_here
```

No other files need changes — `api/routes/search.py` already calls `extract_face_embedding()` and handles the real response path.

---

## Project Structure

```
media-ai/
├── api/
│   ├── main.py              # FastAPI app, scheduler, startup
│   ├── models.py            # Pydantic models
│   └── routes/
│       ├── upload.py        # POST /upload, POST /upload/bulk
│       ├── search.py        # GET /search/bib, POST /search/face
│       └── admin.py         # POST /admin/batch, GET /admin/events/stats
├── db/
│   ├── schema.sql           # All CREATE TABLE + index statements
│   └── connection.py        # psycopg2 connection pool
├── processing/
│   ├── ocr.py               # Tesseract BIB extraction
│   ├── face.py              # Face embedding stub (Week 3 guide inside)
│   ├── batch.py             # Full batch pipeline script
│   └── cleanup.py           # Selfie auto-deletion
├── templates/
│   ├── search.html          # Participant photo discovery UI
│   └── upload_portal.html   # Photographer bulk upload portal
├── storage/                 # Local photo storage (dev only)
├── static/                  # Static assets
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```
