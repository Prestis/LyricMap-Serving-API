from dotenv import load_dotenv
import os
import re
import json
import datetime
import unicodedata
from typing import List, Optional, Annotated, Literal
from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Cookie, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.orm import Session, contains_eager
from database import get_db, init_db, Artist, Song, LocationMention, Place, PlaceAlias, Report
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

# Admin Credentials - login fails unless both are configured
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

PlaceType = Literal["country", "region", "city", "neighborhood", "street", "landmark", "venue", "natural"]

# Response Models
class LocationResponse(BaseModel):
    id: int
    location: str  # the place's display name
    song: str
    lat: Optional[float]
    lng: Optional[float]
    is_manual: bool
    place_id: Optional[int] = None
    place_name: Optional[str] = None
    place_type: Optional[str] = None
    lyric: Optional[str] = None  # the one lyric line the place appears in
    surface_text: Optional[str] = None  # the place as written in that line, for highlighting
    release_date: Optional[datetime.date] = None
    release_date_precision: Optional[str] = None  # "day", "month" or "year"
    song_url: Optional[str] = None

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
    place_id: Optional[int] = None
    lyric: Optional[str] = None

class PlaceResponse(BaseModel):
    id: int
    name: str  # display name, e.g. "Αθήνα"
    name_en: Optional[str]
    type: Optional[str]
    country_code: Optional[str]
    lat: float
    lng: float
    mention_count: int
    artists: List[str]
    first_year: Optional[int]
    last_year: Optional[int]

class PlaceMentionResponse(BaseModel):
    id: int  # mention id, also used for POST /reports
    artist: str
    song: str
    song_url: Optional[str]
    release_date: Optional[datetime.date]
    release_date_precision: Optional[str]
    lyric: Optional[str]
    surface_text: Optional[str]

class PlaceDetailResponse(BaseModel):
    place: PlaceResponse
    mentions: List[PlaceMentionResponse]

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://lyricmap.gr,https://lyricmap.gr,http://localhost:4200").split(",")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def normalize_place_key(text):
    """Matching key for a place name: no accents, casefolded, ς→σ, punctuation removed.
    Only used to compare names, never shown to users."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("ς", "σ")
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())

def valid_coordinates(lat, lng):
    """Reject missing, out-of-range and (0, 0) coordinates."""
    if lat is None or lng is None:
        return False
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return False
    return not (lat == 0 and lng == 0)

def display_name_looks_wrong(name):
    """Flag names an admin should check: all-lowercase, or a Greek word of 4+ letters
    without an accent (e.g. "Αθηνα" from an all-caps source)."""
    if name == name.lower():
        return True
    for word in name.split():
        greek_letters = [c for c in word if "GREEK" in unicodedata.name(c, "")]
        if len(greek_letters) >= 4 and not word.isupper():
            if not any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", word)):
                return True
    return False

def mention_coordinates(mention):
    """A mention's own coordinates if an admin set them (PUT /locations/{id}),
    otherwise its place's."""
    if mention.is_manual and mention.lat is not None:
        return mention.lat, mention.lng
    if mention.place:
        return mention.place.lat, mention.place.lng
    return mention.lat, mention.lng

def mention_filters(
    artist: Optional[str] = None,
    year_from: Annotated[Optional[int], Query(ge=1900, le=2100)] = None,
    year_to: Annotated[Optional[int], Query(ge=1900, le=2100)] = None,
    place_type: Annotated[Optional[PlaceType], Query(alias="type")] = None,
):
    """Filters shared by the map endpoints: ?artist=, ?year_from=, ?year_to=, ?type=.
    Year filters use the song's release date, so songs without one are left out."""
    return {"artist": artist, "year_from": year_from, "year_to": year_to, "place_type": place_type}

def mapped_mentions(db: Session, filters):
    """Mentions that belong on the map (status ok, place with coordinates), with their
    song, artist and place loaded in the same query."""
    query = (
        db.query(LocationMention)
        .join(LocationMention.song)
        .join(Song.artist)
        .join(LocationMention.place)
        .options(
            contains_eager(LocationMention.song).contains_eager(Song.artist),
            contains_eager(LocationMention.place),
        )
        .filter(LocationMention.status == "ok", Place.lat.isnot(None))
    )
    if filters["artist"]:
        query = query.filter(func.lower(Artist.name) == filters["artist"].lower())
    if filters["year_from"] is not None:
        query = query.filter(Song.release_date >= datetime.date(filters["year_from"], 1, 1))
    if filters["year_to"] is not None:
        query = query.filter(Song.release_date <= datetime.date(filters["year_to"], 12, 31))
    if filters["place_type"]:
        query = query.filter(Place.place_type == filters["place_type"])
    return query.order_by(Artist.id, Song.id, LocationMention.line_no)

def summarize_places(mentions):
    """One PlaceResponse per place from a list of mapped mentions, most mentioned first."""
    summaries = {}
    for mention in mentions:
        entry = summaries.setdefault(mention.place_id, {"place": mention.place, "count": 0, "artists": [], "years": []})
        entry["count"] += 1
        artist_name = mention.song.artist.name
        if artist_name not in entry["artists"]:
            entry["artists"].append(artist_name)
        if mention.song.release_date:
            entry["years"].append(mention.song.release_date.year)
    response = [
        PlaceResponse(
            id=entry["place"].id,
            name=entry["place"].display_name,
            name_en=entry["place"].display_name_en,
            type=entry["place"].place_type,
            country_code=entry["place"].country_code,
            lat=entry["place"].lat,
            lng=entry["place"].lng,
            mention_count=entry["count"],
            artists=entry["artists"],
            first_year=min(entry["years"]) if entry["years"] else None,
            last_year=max(entry["years"]) if entry["years"] else None,
        )
        for entry in summaries.values()
    ]
    return sorted(response, key=lambda place: -place.mention_count)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "LyricMap Serving API",
        "endpoints": ["/places", "/places/{place_id}/mentions", "/locations", "/reports", "/docs"]
    }

@app.get("/locations", response_model=List[ArtistLocationsResponse])
def get_locations(filters: Annotated[dict, Depends(mention_filters)], db: Session = Depends(get_db)):
    """Every mention on the map, grouped by artist. Rejected and unresolved mentions are left out."""
    grouped = {}
    for mention in mapped_mentions(db, filters):
        song, place = mention.song, mention.place
        lat, lng = mention_coordinates(mention)
        grouped.setdefault(song.artist.name, []).append(LocationResponse(
            id=mention.id,
            location=place.display_name,
            song=song.title,
            lat=lat,
            lng=lng,
            is_manual=mention.is_manual,
            place_id=place.id,
            place_name=place.display_name,
            place_type=place.place_type,
            lyric=mention.lyric_line,
            surface_text=mention.surface_text,
            release_date=song.release_date,
            release_date_precision=song.release_date_precision,
            song_url=song.url,
        ))
    return [ArtistLocationsResponse(artist=artist, mentions=mentions) for artist, mentions in grouped.items()]

@app.get("/places", response_model=List[PlaceResponse])
def get_places(filters: Annotated[dict, Depends(mention_filters)], db: Session = Depends(get_db)):
    """One map marker per place, with how often and by whom it's mentioned."""
    return summarize_places(mapped_mentions(db, filters))

@app.get("/places/{place_id}/mentions", response_model=PlaceDetailResponse)
def get_place_mentions(place_id: int, filters: Annotated[dict, Depends(mention_filters)], db: Session = Depends(get_db)):
    """A marker's popup: the place and every lyric line that mentions it."""
    mentions = mapped_mentions(db, filters).filter(LocationMention.place_id == place_id).all()
    if not mentions:
        raise HTTPException(status_code=404, detail="Place not found, or no mentions match the filters")
    return PlaceDetailResponse(
        place=summarize_places(mentions)[0],
        mentions=[
            PlaceMentionResponse(
                id=mention.id,
                artist=mention.song.artist.name,
                song=mention.song.title,
                song_url=mention.song.url,
                release_date=mention.song.release_date,
                release_date_precision=mention.song.release_date_precision,
                lyric=mention.lyric_line,
                surface_text=mention.surface_text,
            )
            for mention in mentions
        ],
    )

@app.post("/token", response_model=LoginResponse)
async def login(response: Response, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """Admin login endpoint."""
    if not ADMIN_USERNAME or not ADMIN_PASSWORD_HASH:
        raise HTTPException(status_code=500, detail="Admin login is not configured")
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
    """Set coordinates for one mention only. To move a place for every mention, use PUT /places/{id}."""
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
                "location_name": loc.place.display_name if loc.place else loc.location_name,
                "song": loc.song.title,
                "artist": loc.song.artist.name,
                "report_type": r.report_type,
                "suggestion": r.suggestion,
                "created_at": r.created_at,
                "place_id": loc.place_id,
                "lyric": loc.lyric_line,
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

# --- Admin: reviewing and correcting places and mentions ---
# Edits made here live only in the server's lyricmap.db: copy it back to the
# processing repo (LyricMapAPI) before rebuilding, or the next deploy replaces them.

class PlaceAdminResponse(BaseModel):
    id: int
    name: str
    name_en: Optional[str]
    type: Optional[str]
    country_code: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    geo_source: Optional[str]
    is_manual: bool
    needs_review: bool
    review_reasons: List[str]  # "claude_coordinates", "display_name"
    mention_count: int
    aliases: List[str]

class RejectedMentionResponse(BaseModel):
    id: int
    surface_text: Optional[str]
    lyric: Optional[str]
    reason: Optional[str]  # Claude's reject_reason, or "admin"
    song: str
    artist: str

class PlaceCreateRequest(BaseModel):
    display_name: str = Field(min_length=1)
    display_name_en: Optional[str] = None
    place_type: Optional[PlaceType] = None
    country_code: Optional[str] = Field(None, min_length=2, max_length=2)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)

class PlaceUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1)
    display_name_en: Optional[str] = None
    place_type: Optional[PlaceType] = None
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    needs_review: Optional[bool] = None  # send false to approve a place as it is

class PlaceMergeRequest(BaseModel):
    into_place_id: int

class MentionUpdateRequest(BaseModel):
    place_id: Optional[int] = None  # assign the mention to this place...
    rejected: bool = False  # ...or reject it as not a place
    # Also apply the decision to this spelling in songs processed later. Off by default:
    # one spelling can be a place in one song and an ordinary word in another (Κούβα/κουβά).
    apply_to_spelling: bool = False

def admin_display_name(name):
    """Admin-entered names are kept as typed (e.g. "ΟΑΚΑ"), apart from NFC and spaces."""
    return " ".join(unicodedata.normalize("NFC", name).split())

def set_admin_alias(db: Session, alias_key, place=None, rejected_reason=None):
    """Point a spelling at a place (or reject it) for future songs. Overrides Claude's
    rejections and earlier admin decisions, but never takes a spelling from another place
    Claude resolved."""
    if not alias_key:
        return
    alias = db.query(PlaceAlias).filter(PlaceAlias.alias_key == alias_key).first()
    if alias is None:
        db.add(PlaceAlias(
            alias_key=alias_key,
            place_id=place.id if place else None,
            rejected_reason=rejected_reason,
            source="admin",
        ))
        db.flush()
    elif alias.place_id is None or alias.source == "admin":
        alias.place_id = place.id if place else None
        alias.rejected_reason = rejected_reason
        alias.source = "admin"

def place_admin_response(db: Session, place):
    reasons = []
    if place.geo_source == "llm":
        reasons.append("claude_coordinates")
    if display_name_looks_wrong(place.display_name):
        reasons.append("display_name")
    return PlaceAdminResponse(
        id=place.id,
        name=place.display_name,
        name_en=place.display_name_en,
        type=place.place_type,
        country_code=place.country_code,
        lat=place.lat,
        lng=place.lng,
        geo_source=place.geo_source,
        is_manual=bool(place.is_manual),
        needs_review=bool(place.needs_review),
        review_reasons=reasons,
        mention_count=db.query(LocationMention).filter(
            LocationMention.place_id == place.id, LocationMention.status == "ok"
        ).count(),
        aliases=[alias.alias_key for alias in place.aliases],
    )

def get_place_or_404(db: Session, place_id):
    place = db.query(Place).filter(Place.id == place_id).first()
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    return place

def merge_place(db: Session, place, into):
    """Move `place`'s mentions and aliases to `into`, then delete it. A song that
    already mentions `into` keeps that mention (and gets the duplicate's reports)."""
    for mention in list(place.mentions):
        duplicate = db.query(LocationMention).filter(
            LocationMention.song_id == mention.song_id,
            LocationMention.place_id == into.id,
        ).first()
        if duplicate:
            db.query(Report).filter(Report.location_id == mention.id).update(
                {"location_id": duplicate.id}, synchronize_session=False
            )
            db.delete(mention)
        else:
            mention.place_id = into.id
            if not mention.is_manual:
                mention.lat, mention.lng = into.lat, into.lng
    for alias in list(place.aliases):
        alias.place_id = into.id
    db.flush()
    # Reload the (now empty) collections, or deleting the place would null out the
    # place_id of the rows we just moved
    db.expire(place, ["mentions", "aliases"])
    db.delete(place)

@app.get("/places/review", response_model=List[PlaceAdminResponse])
def get_places_to_review(db: Annotated[Session, Depends(get_db)], current_user: Annotated[str, Depends(get_current_user)]):
    """Places flagged for review (Claude's estimated coordinates, or a suspicious display
    name), most mentioned first."""
    places = db.query(Place).filter(Place.needs_review == True).all()  # noqa: E712
    return sorted((place_admin_response(db, place) for place in places), key=lambda p: -p.mention_count)

@app.get("/mentions/rejected", response_model=List[RejectedMentionResponse])
def get_rejected_mentions(db: Annotated[Session, Depends(get_db)], current_user: Annotated[str, Depends(get_current_user)]):
    """Mentions rejected as not a place, to catch real places that were rejected."""
    mentions = (
        db.query(LocationMention)
        .join(LocationMention.song)
        .join(Song.artist)
        .options(contains_eager(LocationMention.song).contains_eager(Song.artist))
        .filter(LocationMention.status == "rejected")
        .order_by(Artist.id, Song.id)
        .all()
    )
    reasons = dict(db.query(PlaceAlias.alias_key, PlaceAlias.rejected_reason).filter(PlaceAlias.place_id.is_(None)).all())
    return [
        RejectedMentionResponse(
            id=mention.id,
            surface_text=mention.surface_text,
            lyric=mention.lyric_line,
            reason="admin" if mention.is_manual else reasons.get(normalize_place_key(mention.surface_text or "")),
            song=mention.song.title,
            artist=mention.song.artist.name,
        )
        for mention in mentions
    ]

@app.post("/places", response_model=PlaceAdminResponse)
def create_place(
    request: PlaceCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[str, Depends(get_current_user)],
):
    """Create a place by hand, e.g. for a mention that was wrongly rejected."""
    display_name = admin_display_name(request.display_name)
    existing = db.query(Place).filter(Place.name_key == normalize_place_key(display_name)).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A place with this name already exists (id {existing.id}); assign mentions to it instead",
        )
    place = Place(
        display_name=display_name,
        display_name_en=request.display_name_en,
        name_key=normalize_place_key(display_name),
        place_type=request.place_type,
        country_code=request.country_code.lower() if request.country_code else None,
        lat=request.lat,
        lng=request.lng,
        geo_source="manual",
        is_manual=True,
        needs_review=False,
    )
    db.add(place)
    db.flush()
    set_admin_alias(db, place.name_key, place=place)
    db.commit()
    db.refresh(place)
    return place_admin_response(db, place)

@app.put("/places/{place_id}", response_model=PlaceAdminResponse)
def update_place(
    place_id: int,
    request: PlaceUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[str, Depends(get_current_user)],
):
    """Rename a place, change its type, or fix its coordinates for every mention at once.
    Any change marks the place manual (reprocessing never overwrites it) and reviewed."""
    place = get_place_or_404(db, place_id)
    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to update")
    if ("lat" in changes) != ("lng" in changes):
        raise HTTPException(status_code=422, detail="Send lat and lng together")

    if request.display_name is not None:
        place.display_name = admin_display_name(request.display_name)
        place.name_key = normalize_place_key(place.display_name)
        set_admin_alias(db, place.name_key, place=place)
    if "display_name_en" in changes:
        place.display_name_en = request.display_name_en
    if "place_type" in changes:
        place.place_type = request.place_type
    if "lat" in changes:
        if not valid_coordinates(request.lat, request.lng):
            raise HTTPException(status_code=422, detail="Invalid coordinates")
        place.lat, place.lng, place.geo_source = request.lat, request.lng, "manual"
        for mention in place.mentions:
            if not mention.is_manual:
                mention.lat, mention.lng = place.lat, place.lng
    if changes.keys() - {"needs_review"}:
        place.is_manual = True
    place.needs_review = request.needs_review if request.needs_review is not None else False
    db.commit()
    db.refresh(place)
    return place_admin_response(db, place)

@app.post("/places/{place_id}/merge", response_model=PlaceAdminResponse)
def merge_places(
    place_id: int,
    request: PlaceMergeRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[str, Depends(get_current_user)],
):
    """Merge a duplicate place into another: its mentions and spellings move over and it's deleted."""
    if request.into_place_id == place_id:
        raise HTTPException(status_code=400, detail="Can't merge a place into itself")
    place = get_place_or_404(db, place_id)
    into = get_place_or_404(db, request.into_place_id)
    merge_place(db, place, into)
    db.commit()
    db.refresh(into)
    return place_admin_response(db, into)

@app.put("/mentions/{mention_id}")
def update_mention(
    mention_id: int,
    request: MentionUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[str, Depends(get_current_user)],
):
    """Assign a mention to a different place, or reject it. The mention is marked manual,
    so reprocessing keeps the decision."""
    if (request.place_id is None) == (not request.rejected):
        raise HTTPException(status_code=422, detail="Send either place_id or rejected: true")
    mention = db.query(LocationMention).filter(LocationMention.id == mention_id).first()
    if not mention:
        raise HTTPException(status_code=404, detail="Location mention not found")
    alias_key = normalize_place_key(mention.surface_text or mention.location_name)

    if request.rejected:
        mention.place_id, mention.status = None, "rejected"
        if request.apply_to_spelling:
            set_admin_alias(db, alias_key, rejected_reason="not_a_place")
    else:
        place = get_place_or_404(db, request.place_id)
        other = db.query(LocationMention).filter(
            LocationMention.song_id == mention.song_id,
            LocationMention.place_id == place.id,
            LocationMention.id != mention.id,
        ).first()
        if other:
            raise HTTPException(status_code=409, detail=f"This song already mentions that place (mention {other.id})")
        mention.place_id, mention.status = place.id, "ok"
        if request.apply_to_spelling:
            set_admin_alias(db, alias_key, place=place)
    # Coordinates now come from the place (or nowhere, if rejected)
    mention.lat = mention.lng = None
    mention.is_manual = True
    db.commit()
    return {"message": "Mention updated", "id": mention.id, "place_id": mention.place_id, "status": mention.status}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
