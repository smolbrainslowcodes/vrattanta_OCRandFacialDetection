import os
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

_pool: ThreadedConnectionPool | None = None


def get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            dbname=os.getenv("DB_NAME", "media_ai"),
            user=os.getenv("DB_USER", "media_user"),
            password=os.getenv("DB_PASS", "media_pass"),
            options="-c search_path=media_ai,public",
        )
    return _pool


def get_conn():
    return get_pool().getconn()


def release_conn(conn):
    get_pool().putconn(conn)


@contextmanager
def db_cursor():
    """Context manager that yields a cursor and handles commit/rollback/release."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)


def execute_query(sql: str, params: tuple = (), fetch: str = "none"):
    """
    Run a SQL statement and optionally fetch results.

    fetch:
        "none"  — INSERT/UPDATE/DELETE, returns None
        "one"   — fetchone(), returns a single row or None
        "all"   — fetchall(), returns list of rows
    """
    with db_cursor() as cur:
        cur.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return None
