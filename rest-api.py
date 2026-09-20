from dotenv import load_dotenv
import os
import json
import datetime
from typing import List, Optional, Annotated
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from database import get_db, init_db, Artist, Song, LocationMention, SessionLocal, Report
from passlib.context import CryptContext
from jose import JWTError, jwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load environment variables
load_dotenv()

# JWT Configuration - Fail immediately if SECRET_KEY is missing
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("CRITICAL ERROR: SECRET_KEY environment variable is not set.")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Admin Credentials - Fail if admin credentials aren't explicitly configured
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(access_token: Optional[str] = Cookie(None)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
    )
    if not access_token:
        raise credentials_exception
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    if username != ADMIN_USERNAME:
        raise credentials_exception
    return username

# Initialize Database
init_db()

class UnicodeJSONResponse(JSONResponse):
    def render(self, content: any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")

app = FastAPI(title="LyricMap Serving API", default_response_class=UnicodeJSONResponse)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Response Models
class LocationResponse(BaseModel):
    id: int
    location: str
    song: str
    lat: Optional[float]
    lng: Optional[float]
    is_manual: bool

    class Config:
        from_attributes = True

class LocationUpdateRequest(BaseModel):
    lat: float
    lng: float

class ArtistLocationsResponse(BaseModel):
    artist: str
    mentions: List[LocationResponse]

class LoginResponse(BaseModel):
    message: str

class ReportCreateRequest(BaseModel):
    location_id: int
    report_type: str
    suggestion: Optional[str] = None

class ReportResponse(BaseModel):
    id: int
    location_id: int
    location_name: str
    song: str
    artist: str
    report_type: str
    suggestion: Optional[str]
    created_at: datetime.date

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://lyricmap.gr,https://lyricmap.gr,http://localhost:4200,http://localhost:3000,http://localhost:5173").split(",")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "LyricMap Serving API",
        "endpoints": ["/locations", "/reports", "/docs"]
    }

@app.get("/locations", response_model=List[ArtistLocationsResponse])
def get_locations(db: Session = Depends(get_db)):
    """Expose artist location mentions to the frontend map."""
    artists = db.query(Artist).all()
    response = []
    
    for artist in artists:
        mentions = []
        for song in artist.songs:
            for mention in song.locations:
                mentions.append(LocationResponse(
                    id=mention.id,
                    location=mention.location_name,
                    song=song.title,
                    lat=mention.lat,
                    lng=mention.lng,
                    is_manual=mention.is_manual
                ))
        
        if mentions:
            response.append(ArtistLocationsResponse(
                artist=artist.name,
                mentions=mentions
            ))
            
    return response

@app.post("/token", response_model=LoginResponse)
async def login(response: Response, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """Admin login endpoint."""
    if form_data.username != ADMIN_USERNAME or not verify_password(form_data.password, ADMIN_PASSWORD_HASH):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    access_token = create_access_token(data={"sub": form_data.username})
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=COOKIE_SECURE
    )
    return {"message": "Login successful"}

@app.post("/auth/logout")
async def logout(response: Response):
    """Admin logout endpoint."""
    response.delete_cookie(key="access_token", samesite="lax")
    return {"message": "Logged out successfully"}

@app.get("/auth/me")
async def get_me(current_user: Annotated[str, Depends(get_current_user)]):
    """Check current admin session status."""
    return {"username": current_user}

@app.put("/locations/{mention_id}")
def update_location(
    mention_id: int, 
    request: LocationUpdateRequest, 
    db: Annotated[Session, Depends(get_db)], 
    current_user: Annotated[str, Depends(get_current_user)]
):
    """Update coordinates for a location mention."""
    mention = db.query(LocationMention).filter(LocationMention.id == mention_id).first()
    if not mention:
        raise HTTPException(status_code=404, detail="Location mention not found")
    
    mention.lat = request.lat
    mention.lng = request.lng
    mention.is_manual = True
    db.commit()
    return {"message": "Location updated successfully", "id": mention_id, "is_manual": True}

@app.post("/reports")
@limiter.limit("5/day")
def create_report(request: Request, report_req: ReportCreateRequest, db: Session = Depends(get_db)):
    """User submitted location error report."""
    mention = db.query(LocationMention).filter(LocationMention.id == report_req.location_id).first()
    if not mention:
        raise HTTPException(status_code=404, detail="Location mention not found")
        
    new_report = Report(
        location_id=report_req.location_id,
        report_type=report_req.report_type,
        suggestion=report_req.suggestion
    )
    db.add(new_report)
    db.commit()
    db.refresh(new_report)
    return {"message": "Report submitted successfully", "id": new_report.id}

@app.get("/reports", response_model=List[ReportResponse])
def get_reports(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Admin list submitted location reports."""
    reports = db.query(Report).all()
    res = []
    for r in reports:
        loc = r.location
        if loc and loc.song and loc.song.artist:
            res.append({
                "id": r.id,
                "location_id": r.location_id,
                "location_name": loc.location_name,
                "song": loc.song.title,
                "artist": loc.song.artist.name,
                "report_type": r.report_type,
                "suggestion": r.suggestion,
                "created_at": r.created_at
            })
    return res

@app.delete("/reports/{report_id}")
def resolve_report(report_id: int, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Admin resolve/delete a report."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    db.delete(report)
    db.commit()
    return {"message": "Report resolved"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
