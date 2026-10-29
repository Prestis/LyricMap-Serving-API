from sqlalchemy import Column, Integer, BigInteger, String, Text, Float, Boolean, ForeignKey, Date, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./lyricmap.db"
# For PostgreSQL later: 
# SQLALCHEMY_DATABASE_URL = "postgresql://user:password@postgresserver/db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    bio = Column(Text, nullable=True)  # Genius artist description, used as context (hometown) for Claude

    songs = relationship("Song", back_populates="artist")

class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True, index=True)
    artist_id = Column(Integer, ForeignKey("artists.id"))
    title = Column(String)
    genius_id = Column(Integer, unique=True, index=True, nullable=True)
    url = Column(String, nullable=True)
    release_date = Column(Date, nullable=True)
    release_date_precision = Column(String, nullable=True)  # "day", "month" or "year"
    album = Column(String, nullable=True)
    lyrics = Column(Text, nullable=True)  # cleaned lyrics, internal only, never served
    is_processed = Column(Boolean, default=False)

    artist = relationship("Artist", back_populates="songs")
    locations = relationship("LocationMention", back_populates="song")

class Place(Base):
    """One real-world location. Every mention of it, under any spelling, points here."""
    __tablename__ = "places"
    __table_args__ = (UniqueConstraint("osm_type", "osm_id", name="uq_places_osm"),)

    id = Column(Integer, primary_key=True, index=True)
    display_name = Column(String)  # what the frontend shows: "Αθήνα", "Λόφος Στρέφη"
    display_name_en = Column(String, nullable=True)
    name_key = Column(String, index=True)  # normalize_place_key(display_name), to catch duplicates
    place_type = Column(String, nullable=True)  # country, region, city, neighborhood, street, landmark, venue, natural
    country_code = Column(String, nullable=True)  # ISO 3166-1 alpha-2, lowercase
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    osm_type = Column(String, nullable=True)  # node, way, relation
    osm_id = Column(BigInteger, nullable=True)
    geo_source = Column(String, nullable=True)  # "osm", "llm" or "manual"
    is_manual = Column(Boolean, default=False)  # admin edits are never overwritten by reprocessing
    needs_review = Column(Boolean, default=False)  # LLM-only coordinates, suspicious name or coordinates

    aliases = relationship("PlaceAlias", back_populates="place")
    mentions = relationship("LocationMention", back_populates="place")

class PlaceAlias(Base):
    """Every place-name spelling seen, by matching key. Points to a place, or records why it isn't one."""
    __tablename__ = "place_aliases"

    id = Column(Integer, primary_key=True, index=True)
    alias_key = Column(String, unique=True, index=True)  # normalize_place_key(surface text)
    place_id = Column(Integer, ForeignKey("places.id"), nullable=True)
    rejected_reason = Column(String, nullable=True)  # fictional, person, brand, celestial, mythological, not_a_place, too_vague
    source = Column(String)  # "llm", "admin" or "seed"

    place = relationship("Place", back_populates="aliases")

class LocationMention(Base):
    __tablename__ = "location_mentions"
    # One mention per place per song (NULL place_ids don't collide)
    __table_args__ = (UniqueConstraint("song_id", "place_id", name="uq_location_mentions_song_place"),)

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"))
    place_id = Column(Integer, ForeignKey("places.id"), nullable=True)
    status = Column(String, nullable=True)  # "ok", "rejected" or "pending"; NULL for rows from before places existed
    location_name = Column(String)
    surface_text = Column(String, nullable=True)  # as written in the lyric, original case and accents
    lyric_line = Column(String, nullable=True)
    line_no = Column(Integer, nullable=True)  # 0-based line index in Song.lyrics
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    is_manual = Column(Boolean, default=False)

    song = relationship("Song", back_populates="locations")
    place = relationship("Place", back_populates="mentions")

class ApiUsage(Base):
    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, unique=True, index=True, default=datetime.date.today)
    count = Column(Integer, default=0)

class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    location_id = Column(Integer, ForeignKey("location_mentions.id"))
    report_type = Column(String)
    suggestion = Column(String, nullable=True)
    created_at = Column(Date, default=datetime.date.today)

    location = relationship("LocationMention")

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
