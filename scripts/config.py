from pathlib import Path

import psycopg
from dotenv import dotenv_values
from minio import Minio


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

ENV = dotenv_values(PROJECT_ROOT / ".env")


def required(name: str) -> str:
    value = ENV.get(name)

    if value is None or not value.strip():
        raise ValueError(f"Thieu hoac rong bien {name} trong file .env")

    return value


BUCKET_NAME = required("MINIO_BUCKET")


def get_postgres_connection():
    return psycopg.connect(
        host=required("POSTGRES_HOST"),
        port=int(required("POSTGRES_PORT")),
        dbname=required("POSTGRES_DB"),
        user=required("POSTGRES_USER"),
        password=required("POSTGRES_PASSWORD"),
        connect_timeout=10,
    )


def get_minio_client():
    return Minio(
        endpoint=f"127.0.0.1:{required('MINIO_API_PORT')}",
        access_key=required("MINIO_ROOT_USER"),
        secret_key=required("MINIO_ROOT_PASSWORD"),
        secure=False,
    )