import json
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq
from psycopg import sql

from config import (
    BUCKET_NAME,
    PROJECT_ROOT,
    get_minio_client,
    get_postgres_connection,
)


EXPORTS = {
    "customer_rfm": [
        "reference_date",
        "last_purchase_date",
    ],
    "review_training_timed": [
        "order_purchase_timestamp",
        "order_approved_at",
        "label_available_at",
    ],
}

def read_view(connection, view_name):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT * FROM {}").format(
                sql.Identifier("mart", view_name)
            )
        )

        columns = [column.name for column in cursor.description]
        rows = cursor.fetchall()

    if not rows:
        raise RuntimeError(f"mart.{view_name} khong co du lieu.")

    return pd.DataFrame(rows, columns=columns)


def validate(dataframe, view_name):
    if view_name == "customer_rfm":
        if dataframe["customer_unique_id"].duplicated().any():
            raise ValueError("RFM bi trung customer_unique_id.")

        for column in ["recency", "frequency", "monetary"]:
            if dataframe[column].isna().any():
                raise ValueError(f"RFM thieu {column}.")

        if dataframe["recency"].lt(0).any():
            raise ValueError("RFM co recency am.")

        if dataframe["frequency"].lt(1).any():
            raise ValueError("RFM co frequency nho hon 1.")

        if dataframe["monetary"].lt(0).any():
            raise ValueError("RFM co monetary am.")

    else:
        if dataframe["order_id"].duplicated().any():
            raise ValueError("Training bi trung order_id.")

        labels = set(dataframe["low_review"].dropna().unique())

        if labels != {0, 1}:
            raise ValueError(
                f"Training can du hai nhan 0 va 1. Dang co: {labels}"
            )


def main():
    client = get_minio_client()

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid4().hex[:8]
    )

    local_root = PROJECT_ROOT / "data" / "processed" / "mining"
    output_dir = local_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "datasets": {},
    }

    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL "
                "REPEATABLE READ READ ONLY"
            )

        for view_name, date_columns in EXPORTS.items():
            print(f"\nDang xuat mart.{view_name} ...", flush=True)

            dataframe = read_view(connection, view_name)

            for column in date_columns:
                dataframe[column] = pd.to_datetime(
                    dataframe[column],
                    errors="raise",
                )

            validate(dataframe, view_name)

            file_path = output_dir / f"{view_name}.parquet"

            dataframe.to_parquet(
                file_path,
                engine="pyarrow",
                compression="snappy",
                index=False,
            )

            actual_rows = pq.ParquetFile(file_path).metadata.num_rows

            if actual_rows != len(dataframe):
                raise RuntimeError("So dong Parquet khong khop.")

            object_name = (
                f"gold/mining/runs/{run_id}/{view_name}.parquet"
            )

            client.fput_object(
                bucket_name=BUCKET_NAME,
                object_name=object_name,
                file_path=str(file_path),
                content_type="application/octet-stream",
            )

            uploaded = client.stat_object(BUCKET_NAME, object_name)

            if uploaded.size != file_path.stat().st_size:
                raise RuntimeError("Kich thuoc upload khong khop.")

            manifest["datasets"][view_name] = {
                "object": object_name,
                "rows": len(dataframe),
            }

            print(
                f"[OK] {view_name}: {len(dataframe):,} dong",
                flush=True,
            )

            if view_name == "review_training_timed":
                print(
                    dataframe["low_review"]
                    .value_counts()
                    .sort_index()
                    .to_string()
                )

    manifest_path = output_dir / "manifest.json"

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    client.fput_object(
        bucket_name=BUCKET_NAME,
        object_name="gold/mining/latest.json",
        file_path=str(manifest_path),
        content_type="application/json",
    )

    (local_root / "latest.json").write_text(
        manifest_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    print("\nHOAN TAT: Da xuat 2 bo du lieu khai pha.")
    print(f"Du lieu local: {output_dir}")


if __name__ == "__main__":
    main()