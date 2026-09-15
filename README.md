# LyricMap Serving API (Production)

Lightweight, high-performance FastAPI service built to deliver Greek rapper location data to the LyricMap web frontend, handle user error reports, and manage administrative location edits.

## 🚀 Features
- **Ultra-Lightweight**: Requires **< 50 MB RAM** at runtime.
- **Instant Docker Build**: Takes under 15 seconds to build and run on any 512 MB cloud container (DigitalOcean, Railway, Render, etc.).
- **Pre-populated Database**: Includes pre-extracted `lyricmap.db` SQLite database with **52 artists**, **983 songs**, and **1,056 location mentions**.
- **Admin Auth & Reporting**: JWT Authentication, cookie-based security, and rate-limited reporting.

---

## 🛠️ How to push to a new GitHub Repository

Navigate to this project folder in your terminal and execute:

```powershell
# 1. Move into the project directory
cd lyricmap-serving-api

# 2. Initialize Git
git init

# 3. Add all files
git add .

# 4. Create initial commit
git commit -m "Initial commit: LyricMap Lightweight Serving API"

# 5. Rename branch to main
git branch -M main

# 6. Link your new GitHub Repository (replace URL with your new GitHub repo link)
git remote add origin https://github.com/YOUR_USERNAME/LyricMap-Serving-API.git

# 7. Push to GitHub
git push -u origin main
```

---

## 🐳 Docker Deployment (DigitalOcean / VPS)

### Environment Setup
Create a `.env` file on your server (refer to `.env.example`):
```env
SECRET_KEY=your_strong_random_secret_here
ALLOWED_ORIGINS=https://lyricmap.gr,http://localhost:4200
COOKIE_SECURE=true
```

### Run with Docker Compose
```bash
docker compose up --build -d
```

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
