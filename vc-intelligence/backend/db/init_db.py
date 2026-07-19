"""
Creates every table defined in models.py against DATABASE_URL.

Run once, right after models.py is written:
    python init_db.py

With SQLite (the default here), this creates a local file vc.db
in the same folder -- nothing to sign up for, nothing to configure.
If DATABASE_URL is set in .env (e.g. to point at Supabase/Neon
instead), it creates the tables there instead, with no code changes.
"""

from pathlib import Path
import sys

# Make the backend root importable when this script is run from the
# backend folder or from the project root.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.database import Base, engine

# Import every model so SQLAlchemy's metadata knows about all of
# them before create_all() runs. Importing the module is enough --
# you don't need to reference the classes directly below.
from models.models import (
    Founder,
    FounderScoreHistory,
    Opportunity,
    EvidenceItem,
    Claim,
    Contradiction,
    ScoreHistory,
    ThesisConfig,
)


def create_all_tables():
    Base.metadata.create_all(bind=engine)
    print("All tables created (or already existed).")


def drop_and_recreate():
    """
    DESTRUCTIVE. Wipes every table and rebuilds from current models.
    Only use this during Phase 0/1 setup, before any real demo data
    is loaded -- never call this once founders are seeded.
    """
    confirm = input(
        "This will DROP ALL TABLES and recreate them. Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        return
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("All tables dropped and recreated.")


if __name__ == "__main__":
    create_all_tables()

    # ------------------------------------------------------------
    # If you need to add a column mid-build without wiping data:
    # create_all() will NOT alter existing tables, only create new
    # ones. For SQLite, the simplest fix is usually to just delete
    # vc.db and rerun this script, since you likely don't have real
    # demo data loaded yet in early phases. Once real founders are
    # seeded, add columns manually instead:
    #
    #   ALTER TABLE opportunity ADD COLUMN new_field TEXT;
    #
    # (run via `sqlite3 vc.db` in your terminal), then add the
    # matching Column(...) to models.py so they stay in sync.
    # ------------------------------------------------------------