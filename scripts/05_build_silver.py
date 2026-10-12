import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
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
TABLES = [
    "category_translation",
    "customers",
    "orders",
    "order_items",
    "products",
    "sellers",
    "order_payments",
    "order_reviews",
    "geolocation",
]


KEYS = {
    "category_translation": ["product_category_name"],
    "customers": ["customer_id"],
    "orders": ["order_id"],
    "order_items": ["order_id", "order_item_id"],
    "products": ["product_id"],
    "sellers": ["seller_id"],
    "order_payments": ["order_id", "payment_sequential"],
    "order_reviews": ["review_id", "order_id"],
}


INTEGER_COLUMNS = {
    "order_items": ["order_item_id"],
    "products": [
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
    ],
    "order_payments": [
        "payment_sequential",
        "payment_installments",
    ],
    "order_reviews": ["review_score"],
}


MONEY_COLUMNS = {
    "order_items": ["price", "freight_value"],
    "order_payments": ["payment_value"],
}


DATE_COLUMNS = {
    "orders": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "order_items": ["shipping_limit_date"],
    "order_reviews": [
        "review_creation_date",
        "review_answer_timestamp",
    ],
}


ZIP_COLUMNS = {
    "customers": "customer_zip_code_prefix",
    "sellers": "seller_zip_code_prefix",
    "geolocation": "geolocation_zip_code_prefix",
}


def read_staging(connection, table_name):
    frames = []

    with connection.cursor(name=f"read_{table_name}") as cursor:
        cursor.execute(
            sql.SQL("SELECT * FROM {}").format(
                sql.Identifier("staging", table_name)
            )
        )

        columns = [column.name for column in cursor.description]

        while True:
            rows = cursor.fetchmany(50_000)

            if not rows:
                break

            frames.append(
                pd.DataFrame(
                    rows,
                    columns=columns,
                    dtype="string",
                )
            )

    if not frames:
        raise RuntimeError(
            f"staging.{table_name} khong co du lieu. "
            "Hay nap staging truoc."
        )

    return pd.concat(frames, ignore_index=True)


def normalize_text(dataframe):
    free_text_columns = {
        "review_comment_title",
        "review_comment_message",
    }

    for column in dataframe.columns:
        values = dataframe[column].astype("string")

        # Giu nguyen noi dung binh luan.
        if column not in free_text_columns:
            values = values.str.strip()

        # Gia tri rong/chi co khoang trang -> missing.
        dataframe[column] = values.mask(
            values.str.strip().eq(""),
            pd.NA,
        )

    return dataframe


def convert_integer(dataframe, column):
    original = dataframe[column]
    numeric = pd.to_numeric(original, errors="coerce")

    invalid = original.notna() & numeric.isna()

    if invalid.any():
        raise ValueError(
            f"{column}: co gia tri khong chuyen duoc thanh so."
        )

    non_integer = numeric.notna() & numeric.mod(1).ne(0)

    if non_integer.any():
        raise ValueError(
            f"{column}: co gia tri khong phai so nguyen."
        )

    if numeric.lt(0).any():
        raise ValueError(f"{column}: co gia tri am.")

    dataframe[column] = numeric.astype("Int64")


def parse_money(value):
    if pd.isna(value):
        return None

    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"Gia tri tien khong hop le: {value}") from error

    if not result.is_finite() or result < 0:
        raise ValueError(f"Gia tri tien khong hop le: {value}")

    # Olist dung don vi tien voi toi da 2 chu so thap phan.
    if result != result.quantize(Decimal("0.01")):
        raise ValueError(f"Gia tri tien co hon 2 so thap phan: {value}")

    return result.quantize(Decimal("0.01"))


def convert_date(dataframe, column):
    original = dataframe[column]

    converted = pd.to_datetime(
        original,
        format="ISO8601",
        errors="coerce",
    )

    invalid = original.notna() & converted.isna()

    if invalid.any():
        raise ValueError(
            f"{column}: co ngay gio khong hop le."
        )

    dataframe[column] = converted


def check_key(dataframe, table_name):
    columns = KEYS.get(table_name)

    if not columns:
        return

    missing = dataframe[columns].isna().any(axis=1)

    if missing.any():
        raise ValueError(
            f"{table_name}: {int(missing.sum())} dong thieu khoa."
        )

    duplicates = dataframe.duplicated(
        subset=columns,
        keep=False,
    )

    if duplicates.any():
        examples = (
            dataframe.loc[duplicates, columns]
            .head(5)
            .to_dict("records")
        )

        raise ValueError(
            f"{table_name}: trung khoa nhung khac noi dung. "
            f"Vi du: {examples}"
        )


def clean_table(dataframe, table_name, translation):
    dataframe = normalize_text(dataframe)

    if table_name in ZIP_COLUMNS:
        column = ZIP_COLUMNS[table_name]
        valid = dataframe[column].str.fullmatch(r"\d{1,5}", na=False)

        if not valid.all():
            raise ValueError(
                f"{table_name}: ma buu dien thieu hoac sai dinh dang."
            )

        dataframe[column] = dataframe[column].str.zfill(5)

    state_columns = [
        column for column in dataframe.columns
        if column.endswith("_state")
    ]

    for column in state_columns:
        dataframe[column] = dataframe[column].str.upper()

    for column in INTEGER_COLUMNS.get(table_name, []):
        convert_integer(dataframe, column)

    for column in MONEY_COLUMNS.get(table_name, []):
        dataframe[column] = dataframe[column].map(parse_money)

    for column in DATE_COLUMNS.get(table_name, []):
        convert_date(dataframe, column)

    if table_name == "geolocation":
        for column in ["geolocation_lat", "geolocation_lng"]:
            original = dataframe[column]
            numeric = pd.to_numeric(original, errors="coerce")

            if (original.notna() & numeric.isna()).any():
                raise ValueError(f"{column}: toa do khong doc duoc.")

            dataframe[column] = numeric.astype("Float64")

        dataframe["coordinate_valid"] = (
            dataframe["geolocation_lat"].between(-90, 90)
            & dataframe["geolocation_lng"].between(-180, 180)
        ).fillna(False)

    if table_name == "order_reviews":
        invalid = ~dataframe["review_score"].isin([1, 2, 3, 4, 5])

        if invalid.any():
            raise ValueError("review_score thieu hoac ngoai khoang 1-5.")

    # Loai dong trung sau khi chuan hoa va chuyen kieu.
    before = len(dataframe)
    dataframe = dataframe.drop_duplicates().reset_index(drop=True)
    removed = before - len(dataframe)

    check_key(dataframe, table_name)

    if table_name == "products":
        dataframe["category_missing"] = (
            dataframe["product_category_name"].isna()
        )

        dataframe["product_category_name"] = (
            dataframe["product_category_name"].fillna("unknown")
        )

        dataframe = dataframe.merge(
            translation,
            on="product_category_name",
            how="left",
            validate="many_to_one",
        )

        dataframe["category_translation_missing"] = (
            dataframe["product_category_name_english"].isna()
            & ~dataframe["category_missing"]
        )

        dataframe["product_category_name_english"] = (
            dataframe["product_category_name_english"]
            .fillna(dataframe["product_category_name"])
        )

    return dataframe, removed


def main():
    client = get_minio_client()

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid4().hex[:8]
    )

    local_root = PROJECT_ROOT / "data" / "processed" / "silver"
    output_dir = local_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "bucket": BUCKET_NAME,
        "tables": {},
    }

    translation = None

    print(f"BAT DAU SILVER: {run_id}", flush=True)

    with get_postgres_connection() as connection:
        # Doc ca 9 bang tu cung mot snapshot database.
        with connection.cursor() as cursor:
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL "
                "REPEATABLE READ READ ONLY"
            )

        for table_name in TABLES:
            print(f"\nDang xu ly: {table_name}", flush=True)

            dataframe = read_staging(connection, table_name)
            source_rows = len(dataframe)

            dataframe, removed = clean_table(
                dataframe,
                table_name,
                translation,
            )

            if table_name == "category_translation":
                translation = dataframe.copy()

            file_path = output_dir / f"{table_name}.parquet"

            dataframe.to_parquet(
                file_path,
                engine="pyarrow",
                compression="snappy",
                index=False,
            )

            # Kiem tra file Parquet da ghi.
            parquet_rows = pq.ParquetFile(file_path).metadata.num_rows

            if parquet_rows != len(dataframe):
                raise RuntimeError(
                    f"{table_name}: so dong Parquet khong khop."
                )

            object_name = f"silver/runs/{run_id}/{table_name}.parquet"

            client.fput_object(
                bucket_name=BUCKET_NAME,
                object_name=object_name,
                file_path=str(file_path),
                content_type="application/octet-stream",
            )

            uploaded = client.stat_object(BUCKET_NAME, object_name)

            if uploaded.size != file_path.stat().st_size:
                raise RuntimeError(
                    f"{table_name}: kich thuoc upload khong khop."
                )

            manifest["tables"][table_name] = {
                "object": object_name,
                "source_rows": source_rows,
                "silver_rows": len(dataframe),
                "duplicates_removed": removed,
            }

            print(
                f"[OK] {table_name}: "
                f"staging={source_rows:,}, "
                f"silver={len(dataframe):,}, "
                f"loai trung={removed:,}",
                flush=True,
            )

            del dataframe

    # Chi cap nhat con tro sau khi ca 9 bang thanh cong.
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    client.fput_object(
        bucket_name=BUCKET_NAME,
        object_name="silver/latest.json",
        file_path=str(manifest_path),
        content_type="application/json",
    )

    (local_root / "latest.json").write_text(
        manifest_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    print("\nHOAN TAT: Da tao Silver cho 9 bang.")
    print(f"Du lieu local: {output_dir}")
    print("MinIO: silver/latest.json")


if __name__ == "__main__":
    main()