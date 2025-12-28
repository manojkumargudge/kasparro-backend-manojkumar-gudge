import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# During tests we force an in-memory SQLite DB to avoid depending on Postgres
if os.getenv("TESTING") == "1":
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
else:
    DATABASE_URL = os.getenv("DATABASE_URL")

# If DATABASE_URL is None, create_engine will raise a helpful error
engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
