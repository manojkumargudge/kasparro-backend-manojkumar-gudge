import sys
import os
# ensure project root is on sys.path for tests run inside container
sys.path.insert(0, os.getcwd())
# Use a file-backed SQLite DB with a unique name per process to avoid cross-run collisions
os.environ['TESTING'] = '1'
pid = str(os.getpid())
default_db = f"sqlite+pysqlite:////tmp/test_db_{pid}.sqlite3"
os.environ['DATABASE_URL'] = os.environ.get('DATABASE_URL', default_db)
