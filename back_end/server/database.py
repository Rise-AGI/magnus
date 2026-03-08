# back_end/server/database.py
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pywheels.file_tools import guarantee_file_exist
from ._magnus_config import *


magnus_database_path = f"{magnus_config['server']['root']}/database"
guarantee_file_exist(magnus_database_path, is_directory=True)
sqlalchemy_database_url = f"sqlite:///{magnus_database_path}/magnus.db"


db_config = magnus_config["server"]["database"]
engine = create_engine(
    url = sqlalchemy_database_url,
    connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    },
    pool_size = db_config["pool_size"],
    max_overflow = db_config["max_overflow"],
    pool_timeout = db_config["pool_timeout"],
    pool_recycle = db_config["pool_recycle"],
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
SessionLocal = sessionmaker(
    autocommit = False, 
    autoflush = False, 
    bind = engine,
)


Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()