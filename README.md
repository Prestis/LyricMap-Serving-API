# LyricMap Serving API (Production)

Lightweight FastAPI service that serves the places mentioned in Greek rap lyrics to the LyricMap web frontend (lyricmap.gr), handles user error reports, and lets an admin correct places.

## 🚀 Features
- **Ultra-Lightweight**: about 75 MB RAM at runtime, so it fits a 512 MB cloud container (DigitalOcean, Railway, Render, etc.).
- **Pre-populated Database**: `lyricmap.db` (SQLite) holds **477 places** with **1,258 mentions** across **49 artists**, each with the lyric line it appears in, the song's Genius link and its release date.
- **Map API**: one marker per place, filterable by artist, year and place type.
- **Admin Auth & Reporting**: JWT authentication in an httpOnly cookie, rate-limited user reports, and admin endpoints to review, rename, move, merge and reject places.

The database is built by the processing repo (LyricMapAPI: Genius lyrics → Greek NER → Claude → OpenStreetMap), which runs locally because NER needs more memory than the server has. This repo only serves the result. The copy here leaves out the full lyrics; only single lyric lines are stored and served.

---

## 🗺️ Endpoints

All map endpoints accept `?artist=` (case-insensitive), `?year_from=`, `?year_to=` (by release date) and `?type=` (`country`, `region`, `city`, `neighborhood`, `street`, `landmark`, `venue`, `natural`).

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/places` | | One marker per place: name, type, coordinates, mention count, artists, first/last year |
| GET | `/places/{id}/mentions` | | A marker's popup: the place and every lyric line that mentions it |
| GET | `/locations` | | Every mention grouped by artist (the original shape, plus place, lyric line, release date and song URL) |
| POST | `/reports` | | Report a wrong location (5/day per IP); `location_id` is a mention id |
| POST | `/token`, `/auth/logout` | | Admin login / logout (sets / clears the `access_token` cookie) |
| GET | `/auth/me` | admin | Current admin session |
| GET, DELETE | `/reports`, `/reports/{id}` | admin | List / resolve reports |
| GET | `/places/review` | admin | Places flagged for review (estimated coordinates, suspicious name) |
| GET | `/mentions/rejected` | admin | Mentions rejected as not a place |
| POST | `/places` | admin | Create a place by hand |
| PUT | `/places/{id}` | admin | Rename, retype or move a place for all its mentions, or approve it (`needs_review: false`) |
| POST | `/places/{id}/merge` | admin | Merge a duplicate place into another (`into_place_id`) |
| PUT | `/mentions/{id}` | admin | Assign a mention to another place (`place_id`) or reject it (`rejected: true`) |
| PUT | `/locations/{id}` | admin | Set coordinates for one mention only |

Interactive docs: `/docs`.

---

## 🔄 Updating the data

1. **Before rebuilding**, copy the server's `lyricmap.db` back to the processing repo, so admin edits and reports made on the server are exported and kept.
2. Rebuild there, then copy the new `lyricmap.db` here **with the lyrics stripped** (`UPDATE songs SET lyrics = NULL; UPDATE artists SET bio = NULL; VACUUM;`). This repo is public.
3. Back up the server's `lyricmap.db`, deploy, and restart the container.

Deploying a new database replaces the server's, including any admin edits and reports made there since step 1.

---

## 🐳 Docker Deployment (DigitalOcean / VPS)

### Environment Setup
Create a `.env` file on your server:
```env
SECRET_KEY=your_strong_random_secret_here
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=$$2b$$12$$...
ALLOWED_ORIGINS=https://lyricmap.gr,http://localhost:4200
COOKIE_SECURE=true
```
- `SECRET_KEY` signs the admin JWT; the app refuses to start without it.
- `ADMIN_PASSWORD_HASH` is a bcrypt hash (`python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('your-password'))"`). Docker Compose treats `$` in `.env` as a variable, so write every `$` as `$$` there. When running without Docker, use single `$`.
- `ALLOWED_ORIGINS` is a comma-separated CORS list; it defaults to lyricmap.gr and `http://localhost:4200`.
- `COOKIE_SECURE=true` sends the login cookie over HTTPS only.

### Run with Docker Compose
```bash
docker compose up --build -d
```
The container mounts `./lyricmap.db`, so admin edits and reports are written to that file.

---

## 💻 Local Development

Run using Uvicorn directly:
```bash
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
python rest-api.py
```
Open `http://localhost:8000/docs` for interactive API documentation.
