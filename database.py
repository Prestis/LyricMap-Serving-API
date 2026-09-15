from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy import create_engine
import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./lyricmap.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)

    songs = relationship("Song", back_populates="artist")

class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True, index=True)
    artist_id = Column(Integer, ForeignKey("artists.id"))
    title = Column(String)
    is_processed = Column(Boolean, default=False)

    artist = relationship("Artist", back_populates="songs")
    locations = relationship("LocationMention", back_populates="song")

class LocationMention(Base):
    __tablename__ = "location_mentions"

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"))
    location_name = Column(String)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    is_manual = Column(Boolean, default=False)

    song = relationship("Song", back_populates="locations")

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
