from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import aiosqlite
import asyncio

Base = declarative_base()

class EmailValidation(Base):
    __tablename__ = "email_validations"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    format_valid = Column(Boolean, nullable=False)
    mx_valid = Column(Boolean, nullable=False)
    deliverable = Column(Boolean, nullable=False)
    message = Column(Text, nullable=False)
    validated_at = Column(DateTime, default=datetime.utcnow)
    batch_id = Column(String, nullable=True, index=True)  # For batch validations

# Database setup
DATABASE_URL = "sqlite:///./email_validator.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def create_tables():
    """Create database tables"""
    Base.metadata.create_all(bind=engine)

def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
