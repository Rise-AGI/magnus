# back_end/server/database.py
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from ._magnus_config import *


magnus_database_path = magnus_config["server"]["database"]["path"]
sqlalchemy_database_url = f"sqlite:///{magnus_database_path}/magnus.db"


engine = create_engine(
    sqlalchemy_database_url, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()