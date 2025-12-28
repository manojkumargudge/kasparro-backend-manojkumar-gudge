import os, traceback, importlib
os.environ['TESTING']='1'
os.environ['DATABASE_URL']=os.environ.get('DATABASE_URL','sqlite+pysqlite:////tmp/test_db.sqlite3')

m = importlib.import_module('tests.test_api')
try:
    m.setup_module(None)
    m.test_get_data()
    print('TEST OK')
except Exception:
    traceback.print_exc()
