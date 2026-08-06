"""SQLAlchemy models for metadata database - schema registry, conversation history, feedback, embeddings."""

from datetime import datetime
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, Float,
    LargeBinary, ForeignKey, JSON, UniqueConstraint, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from sqlalchemy.sql import func
from dotenv import load_dotenv
load_dotenv()

Base = declarative_base()

# Global session factory (initialized by init_db)
_session_factory: Optional[sessionmaker] = None


class ServiceMeta(Base):
    """Metadata for a microservice and its database."""
    __tablename__ = "service_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_name = Column(String(100), unique=True, nullable=False, index=True)
    connection_key = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    last_crawled_at = Column(DateTime, nullable=True)
    table_count = Column(Integer, default=0, nullable=False)

    # Relationships
    tables = relationship("TableMeta", back_populates="service", cascade="all, delete-orphan")


class TableMeta(Base):
    """Metadata for a database table."""
    __tablename__ = "table_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_id = Column(Integer, ForeignKey("service_meta.id", ondelete="CASCADE"), nullable=False, index=True)
    schema_name = Column(String(100), default="public", nullable=False)
    table_name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
    row_count_estimate = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    service = relationship("ServiceMeta", back_populates="tables")
    columns = relationship("ColumnMeta", back_populates="table", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("service_id", "schema_name", "table_name", name="uq_table_per_service"),
    )


class ColumnMeta(Base):
    """Metadata for a table column."""
    __tablename__ = "column_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_id = Column(Integer, ForeignKey("table_meta.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    data_type = Column(String(100), nullable=False)
    is_nullable = Column(Boolean, default=True, nullable=False)
    is_primary_key = Column(Boolean, default=False, nullable=False)
    is_foreign_key = Column(Boolean, default=False, nullable=False)
    fk_target_table = Column(String(255), nullable=True)
    fk_target_column = Column(String(255), nullable=True)
    ordinal_position = Column(Integer, nullable=False)
    column_comment = Column(Text, nullable=True)

    # Relationships
    table = relationship("TableMeta", back_populates="columns")


class JoinKey(Base):
    """Cross-service join keys discovered by crawler."""
    __tablename__ = "join_key"

    id = Column(Integer, primary_key=True, autoincrement=True)
    column_name = Column(String(255), nullable=False, index=True)
    data_type = Column(String(100), nullable=False)
    services = Column(JSON, nullable=False)  # List of service names
    discovered_at = Column(DateTime, default=func.now(), nullable=False)


class QueryLog(Base):
    """Audit log of user queries and generated SQL."""
    __tablename__ = "query_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(255), nullable=True, index=True)
    tenant_id = Column(String(255), nullable=True, index=True)
    question = Column(Text, nullable=False)
    sql_generated = Column(Text, nullable=True)
    services_used = Column(JSON, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    row_count = Column(Integer, nullable=True)
    was_successful = Column(Boolean, default=False, nullable=False)
    error_message = Column(Text, nullable=True)
    user_feedback = Column(String(50), nullable=True)  # "positive", "partial", "negative"
    feedback_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False, index=True)


class Correction(Base):
    """User corrections and learned patterns for improved accuracy."""
    __tablename__ = "correction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trigger_phrase = Column(Text, nullable=False, index=True)
    correct_interpretation = Column(Text, nullable=False)
    trigger_embedding = Column(LargeBinary, nullable=True)
    times_used = Column(Integer, default=0, nullable=False)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


def init_db(connection_string: str) -> None:
    """Initialize the metadata database and create all tables."""
    global _session_factory

    pg_connect_args = {}
    if "postgresql" in connection_string or "postgres" in connection_string:
        pg_connect_args["prepare_threshold"] = 0  # Disable psycopg3 prepared statement cache

    engine = create_engine(
        connection_string,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
        connect_args=pg_connect_args
    )

    # Create all tables
    Base.metadata.create_all(engine)

    # Create session factory
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Session:
    """Context manager for database sessions."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
        session.close()

import hashlib
import secrets
import os

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    models = relationship("SavedModel", back_populates="user", cascade="all, delete-orphan")
    chat_threads = relationship("ChatThread", back_populates="user", cascade="all, delete-orphan")

class UserSession(Base):
    __tablename__ = "user_sessions"
    token = Column(String(100), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    user = relationship("User", back_populates="sessions")

class SavedModel(Base):
    __tablename__ = "saved_models"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(150), nullable=False)
    dialect = Column(String(50), nullable=False)
    db_name = Column(String(100), nullable=False)
    connection_string = Column(String(1024), nullable=False)
    db_key = Column(String(255), nullable=False)
    qdrant_collection = Column(String(255), nullable=False)
    common_filter_keys = Column(JSON, nullable=True)  # List of mandatory filter column names
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    user = relationship("User", back_populates="models")

class ChatThread(Base):
    __tablename__ = "chat_threads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    db_session_id = Column(String(255), nullable=False)
    title = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    user = relationship("User", back_populates="chat_threads")
    messages = relationship("ChatThreadMessage", back_populates="thread", cascade="all, delete-orphan")

class ChatThreadMessage(Base):
    __tablename__ = "chat_thread_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(Integer, ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    sql_used = Column(Text, nullable=True)
    table_data = Column(JSON, nullable=True)
    columns = Column(JSON, nullable=True)
    stats = Column(JSON, nullable=True)
    visualization_hint = Column(String(50), nullable=True)
    chart_x_axis = Column(String(100), nullable=True)
    chart_y_axis = Column(String(100), nullable=True)
    explanation = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    thread = relationship("ChatThread", back_populates="messages")

class LLMConfig(Base):
    """Dynamic configurations and parameters for LLM providers."""
    __tablename__ = "llm_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(50), unique=True, nullable=False, index=True) # gemini, openrouter, cerebras, groq
    model = Column(String(100), nullable=False)
    api_url = Column(String(255), nullable=True)
    max_tokens = Column(Integer, default=512, nullable=False)
    temperature = Column(Float, default=0.3, nullable=False)
    timeout_seconds = Column(Integer, default=60, nullable=False)
    sequence_order = Column(Integer, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    api_key = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

def hash_password(password: str, salt: Optional[str] = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return f"{salt}${key.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, _ = hashed.split('$')
        return hash_password(password, salt) == hashed
    except ValueError:
        return False

APP_DATABASE_URL = os.getenv("APP_DATABASE_URL")
if not APP_DATABASE_URL:
    raise RuntimeError("APP_DATABASE_URL environment variable is not set.")

def init_app_db():
    engine_args = {}
    db_url = APP_DATABASE_URL

    # Ensure PostgreSQL URLs have the explicit psycopg dialect for SQLAlchemy 2.0
    if db_url.startswith("postgres://") and "psycopg" not in db_url:
        db_url = db_url.replace("postgres://", "postgresql+psycopg://")
    elif db_url.startswith("postgresql://") and "psycopg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://")

    if "sqlite" in db_url:
        engine_args = {"connect_args": {"check_same_thread": False}}
    elif "postgresql" in db_url or "postgres" in db_url:
        engine_args = {"connect_args": {"prepare_threshold": 0}}  # Disable psycopg3 prepared statement cache

    engine = create_engine(db_url, **engine_args)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)

AppDBSession = init_app_db()
