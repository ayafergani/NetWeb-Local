import os

import psycopg2


def get_db_connection():
    sslmode = os.getenv("DB_SSLMODE", "disable").strip() or "disable"
    db_name = os.getenv("DB_NAME", "ids_db")
    db_user = os.getenv("DB_USER", "aya")
    db_password = os.getenv("DB_PASSWORD", "aya")
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = int(os.getenv("DB_PORT", "5432"))

    return psycopg2.connect(
        dbname=db_name,
        user=db_user,
        password=db_password,
        host=db_host,
        port=db_port,
        sslmode=sslmode,
    )
