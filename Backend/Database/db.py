import logging
import os
from pathlib import Path
import threading
import time

import psycopg2
from psycopg2 import pool
from psycopg2.extensions import TRANSACTION_STATUS_IDLE

logger = logging.getLogger(__name__)

_POOL = None
_POOL_LOCK = threading.Lock()
_POOL_SEMAPHORE = None
_LOCAL_MODE_LOGGED = False


def _load_dotenv_file():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv_file()


def _is_local_env():
    return os.getenv("ENV", "local").strip().lower() == "local"


def _get_database_url():
    return ""


def _get_pool_limits():
    min_conn = int(os.getenv("DB_POOL_MIN", "1"))
    max_conn = int(os.getenv("DB_POOL_MAX", "3"))
    min_conn = max(1, min_conn)
    max_conn = max(min_conn, max_conn)
    return min_conn, max_conn


def _get_connect_kwargs():
    sslmode = os.getenv("DB_SSLMODE", "disable").strip() or "disable"
    connect_timeout = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
    statement_timeout = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "15000"))
    lock_timeout = int(os.getenv("DB_LOCK_TIMEOUT_MS", "3000"))
    idle_timeout = int(os.getenv("DB_IDLE_TX_TIMEOUT_MS", "15000"))
    options = (
        f"-c statement_timeout={statement_timeout} "
        f"-c lock_timeout={lock_timeout} "
        f"-c idle_in_transaction_session_timeout={idle_timeout}"
    )

    return {
        "dbname": os.getenv("DB_NAME", "ids_db"),
        "user": os.getenv("DB_USER", "aya"),
        "password": os.getenv("DB_PASSWORD", "aya"),
        "host": os.getenv("DB_HOST", "192.168.1.2"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "sslmode": sslmode,
        "connect_timeout": connect_timeout,
        "options": options,
    }


def log_database_mode():
    global _LOCAL_MODE_LOGGED

    if _is_local_env() and not _LOCAL_MODE_LOGGED:
        logger.info("MODE LOCAL ACTIVÉ - DB_HOST = %s", os.getenv("DB_HOST", "192.168.1.2"))
        _LOCAL_MODE_LOGGED = True


def _get_pool():
    global _POOL, _POOL_SEMAPHORE, _LOCAL_MODE_LOGGED

    if _POOL is not None:
        return _POOL

    with _POOL_LOCK:
        if _POOL is not None:
            return _POOL

        min_conn, max_conn = _get_pool_limits()
        _POOL_SEMAPHORE = threading.BoundedSemaphore(max_conn)
        if _is_local_env() and not _LOCAL_MODE_LOGGED:
            logger.info("MODE LOCAL ACTIVÉ - DB_HOST = %s", os.getenv("DB_HOST", "192.168.1.2"))
            _LOCAL_MODE_LOGGED = True
        _POOL = pool.ThreadedConnectionPool(min_conn, max_conn, **_get_connect_kwargs())
        logger.info("PostgreSQL pool initialise: min=%s max=%s", min_conn, max_conn)
        return _POOL


class SlowCursorProxy:
    def __init__(self, cursor, on_close=None):
        self._cursor = cursor
        self._on_close = on_close
        self._closed = False
        self._slow_ms = int(os.getenv("DB_SLOW_QUERY_MS", "750"))

    def execute(self, query, vars=None):
        started_at = time.perf_counter()
        try:
            return self._cursor.execute(query, vars)
        finally:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            if elapsed_ms >= self._slow_ms:
                compact_query = " ".join(str(query).split())
                logger.warning("SQL lente %.0f ms: %s", elapsed_ms, compact_query[:300])

    def executemany(self, query, vars_list):
        started_at = time.perf_counter()
        try:
            return self._cursor.executemany(query, vars_list)
        finally:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            if elapsed_ms >= self._slow_ms:
                compact_query = " ".join(str(query).split())
                logger.warning("SQL lente %.0f ms: %s", elapsed_ms, compact_query[:300])

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._cursor.__exit__(exc_type, exc, tb)
        finally:
            self._mark_closed()

    def __iter__(self):
        return iter(self._cursor)

    def close(self):
        if self._closed:
            return

        try:
            self._cursor.close()
        finally:
            self._mark_closed()

    def _mark_closed(self):
        if self._closed:
            return

        self._closed = True
        if self._on_close is not None:
            self._on_close(self)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class PooledConnectionProxy:
    def __init__(self, pg_pool, raw_conn):
        self._pool = pg_pool
        self._conn = raw_conn
        self._returned = False
        self._open_cursors = []

    @property
    def closed(self):
        return True if self._returned else self._conn.closed

    def cursor(self, *args, **kwargs):
        cursor_proxy = SlowCursorProxy(
            self._conn.cursor(*args, **kwargs),
            on_close=self._unregister_cursor,
        )
        self._open_cursors.append(cursor_proxy)
        return cursor_proxy

    def _unregister_cursor(self, cursor_proxy):
        try:
            self._open_cursors.remove(cursor_proxy)
        except ValueError:
            pass

    def close(self):
        global _POOL_SEMAPHORE

        if self._returned:
            return

        close_conn = bool(self._conn.closed)
        try:
            for cursor_proxy in self._open_cursors[:]:
                cursor_proxy.close()

            if not close_conn and self._conn.get_transaction_status() != TRANSACTION_STATUS_IDLE:
                self._conn.rollback()
        except Exception:
            logger.exception("Rollback automatique impossible avant retour au pool")
            close_conn = True

        self._pool.putconn(self._conn, close=close_conn)
        self._returned = True

        if _POOL_SEMAPHORE is not None:
            _POOL_SEMAPHORE.release()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db_connection():
    log_database_mode()
    pg_pool = _get_pool()
    timeout = float(os.getenv("DB_POOL_TIMEOUT", "3"))

    started_at = time.perf_counter()
    if not _POOL_SEMAPHORE.acquire(timeout=timeout):
        raise psycopg2.OperationalError(
            f"Pool PostgreSQL sature: aucune connexion disponible apres {timeout:.0f}s"
        )

    wait_ms = (time.perf_counter() - started_at) * 1000
    if wait_ms >= 500:
        logger.warning("Attente connexion DB %.0f ms", wait_ms)

    try:
        raw_conn = pg_pool.getconn()
        return PooledConnectionProxy(pg_pool, raw_conn)
    except Exception:
        _POOL_SEMAPHORE.release()
        raise
