import hashlib
import json
import re
from io import BytesIO

import pandas as pd
from psycopg.types.json import Jsonb

from config import (
    get_minio_client,
    get_postgres_connection,
    required,
)


SILVER_ROOT = "silver/external/nager_date"


def read_object(client, bucket, object_name):
    response = client.get_object(
        bucket_name=bucket,
        object_name=object_name,
    )

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def main():
    client = get_minio_client()
    bucket = required("MINIO_BUCKET")

    # 1. Xác định bộ Silver hoàn chỉnh.
    latest = json.loads(
        read_object(
            client, bucket, f"{SILVER_ROOT}/latest.json"
        )
    )

    if latest.get("status") != "completed":
        raise ValueError("Bộ Silver chưa hoàn chỉnh.")

    silver_run_id = latest["silver_run_id"]

    if not isinstance(silver_run_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", silver_run_id
    ):
        raise ValueError("silver_run_id không hợp lệ.")

    prefix = f"{SILVER_ROOT}/runs/{silver_run_id}"

    manifest = json.loads(
        read_object(client, bucket, f"{prefix}/manifest.json")
    )

    if (
        manifest.get("status") != "completed"
        or manifest.get("silver_run_id") != silver_run_id
    ):
        raise ValueError("Manifest Silver không khớp.")

    bronze_run_id = manifest["source_bronze_run_id"]

    file_entries = {
        entry["filename"]: entry
        for entry in manifest["files"]
    }

    frames = {}

    # 2. Kiểm tra nội dung rồi đọc hai Parquet.
    for filename in (
        "holiday_events.parquet",
        "calendar_br.parquet",
    ):
        body = read_object(
            client, bucket, f"{prefix}/{filename}"
        )

        actual_hash = hashlib.sha256(body).hexdigest()

        if actual_hash != file_entries[filename]["sha256"]:
            raise ValueError(
                f"Nội dung Silver không khớp: {filename}"
            )

        frames[filename] = pd.read_parquet(BytesIO(body))

    events = frames["holiday_events.parquet"]
    calendar = frames["calendar_br.parquet"]

    calendar["calendar_date"] = pd.to_datetime(
        calendar["calendar_date"], errors="raise"
    ).dt.date

    events["holiday_date"] = pd.to_datetime(
        events["holiday_date"], errors="raise"
    ).dt.date

    expected_dates = set(
        pd.date_range(
            "2016-01-01", "2018-12-31", freq="D"
        ).date
    )

    if (
        len(calendar) != len(expected_dates)
        or calendar["calendar_date"].duplicated().any()
        or set(calendar["calendar_date"]) != expected_dates
    ):
        raise ValueError(
            "Lịch phải đủ 1.096 ngày và không trùng ngày."
        )

    if events.empty:
        raise ValueError("Bảng sự kiện ngày lễ đang rỗng.")

    if not set(events["holiday_date"]).issubset(expected_dates):
        raise ValueError("Sự kiện có ngày ngoài phạm vi lịch.")

    # Chuẩn bị kiểu dữ liệu phù hợp cho PostgreSQL.
    calendar_rows = [
        (
            row.calendar_date,
            int(row.year),
            int(row.month),
            int(row.day_of_week),
            bool(row.is_weekend),
            int(row.national_public_event_count),
            bool(row.is_national_public_holiday),
            row.national_public_holiday_names,
            silver_run_id,
            bronze_run_id,
        )
        for row in calendar.itertuples(index=False)
    ]

    event_rows = [
        (
            row.holiday_date,
            row.country_code,
            row.holiday_name,
            row.holiday_local_name,
            bool(row.is_global),
            Jsonb(json.loads(row.types_json)),
            Jsonb(json.loads(row.counties_json)),
            bool(row.is_national_public_holiday),
            silver_run_id,
            bronze_run_id,
        )
        for row in events.itertuples(index=False)
    ]

    # Toàn bộ thao tác DB nằm trong cùng một giao dịch.
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE SCHEMA IF NOT EXISTS external_data
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS
                    external_data.br_calendar (
                    calendar_date DATE PRIMARY KEY,
                    year_number INTEGER NOT NULL,
                    month_number INTEGER NOT NULL,
                    day_of_week INTEGER NOT NULL,
                    is_weekend BOOLEAN NOT NULL,
                    national_public_event_count INTEGER NOT NULL,
                    is_national_public_holiday BOOLEAN NOT NULL,
                    national_public_holiday_names TEXT NOT NULL,
                    source_silver_run_id TEXT NOT NULL,
                    source_bronze_run_id TEXT NOT NULL,
                    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK (month_number BETWEEN 1 AND 12),
                    CHECK (day_of_week BETWEEN 0 AND 6),
                    CHECK (national_public_event_count >= 0)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS
                    external_data.br_holiday_events (
                    event_id BIGINT GENERATED ALWAYS AS IDENTITY
                        PRIMARY KEY,
                    holiday_date DATE NOT NULL REFERENCES
                        external_data.br_calendar(calendar_date),
                    country_code TEXT NOT NULL,
                    holiday_name TEXT NOT NULL,
                    holiday_local_name TEXT NOT NULL,
                    is_global BOOLEAN NOT NULL,
                    types JSONB NOT NULL,
                    counties JSONB NOT NULL,
                    is_national_public_holiday BOOLEAN NOT NULL,
                    source_silver_run_id TEXT NOT NULL,
                    source_bronze_run_id TEXT NOT NULL,
                    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK (country_code = 'BR')
                )
            """)

            # Chỉ thay nội dung hai bảng mới của nguồn ngày lễ.
            cursor.execute(
                "DELETE FROM external_data.br_holiday_events"
            )
            cursor.execute(
                "DELETE FROM external_data.br_calendar"
            )

            cursor.executemany("""
                INSERT INTO external_data.br_calendar (
                    calendar_date,
                    year_number,
                    month_number,
                    day_of_week,
                    is_weekend,
                    national_public_event_count,
                    is_national_public_holiday,
                    national_public_holiday_names,
                    source_silver_run_id,
                    source_bronze_run_id
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, calendar_rows)

            cursor.executemany("""
                INSERT INTO external_data.br_holiday_events (
                    holiday_date,
                    country_code,
                    holiday_name,
                    holiday_local_name,
                    is_global,
                    types,
                    counties,
                    is_national_public_holiday,
                    source_silver_run_id,
                    source_bronze_run_id
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, event_rows)

            cursor.execute("""
                SELECT count(*)
                FROM external_data.br_calendar
            """)
            calendar_count = cursor.fetchone()[0]

            cursor.execute("""
                SELECT count(*)
                FROM external_data.br_holiday_events
            """)
            event_count = cursor.fetchone()[0]

            if (
                calendar_count != len(calendar)
                or event_count != len(events)
            ):
                raise ValueError("Số dòng sau nạp không khớp.")

    print("NẠP NGÀY LỄ VÀO POSTGRESQL THÀNH CÔNG")
    print(f"Số ngày trong lịch: {calendar_count}")
    print(f"Số bản ghi sự kiện: {event_count}")
    print(f"Silver run: {silver_run_id}")
    print(f"Bronze run: {bronze_run_id}")


if __name__ == "__main__":
    main()