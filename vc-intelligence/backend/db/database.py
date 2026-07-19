from pathlib import Path
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env")

# Falls back to a local SQLite file if DATABASE_URL isn't set in .env --
# this is what makes "zero installs" possible: SQLite needs no server,
# no signup, no driver beyond sqlalchemy itself. The file vc.db gets
# created automatically the first time init_db.py runs.
DEFAULT_DB_PATH = BACKEND_ROOT / "vc.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# check_same_thread=False is SQLite-specific -- needed because
# Streamlit/FastAPI may access the DB from a different thread than
# the one that created the connection. Harmless to leave in even if
# you later switch to Postgres, but only actually required for SQLite.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)

Base = declarative_base()


def get_db():
    """
    FastAPI-style dependency. If you're calling straight from
    Streamlit instead, just use SessionLocal() directly as shown
    in crud.py -- you don't need this function there.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()