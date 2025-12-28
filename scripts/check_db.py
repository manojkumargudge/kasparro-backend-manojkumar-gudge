import os
import traceback
from sqlalchemy import create_engine

DATABASE_URL = os.getenv("DATABASE_URL")
print("DATABASE_URL:", repr(DATABASE_URL))

engine = create_engine(DATABASE_URL)
print("engine.url.host=", engine.url.host)

try:
    conn = engine.connect()
    print("connected to database")
    conn.close()
except Exception as e:
    print("ERROR:", repr(e))
    traceback.print_exc()
