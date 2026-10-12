import json
from io import BytesIO

import pandas as pd
from pandas.api.types import is_datetime64_any_dtype
from psycopg import sql

from config import (
    BUCKET_NAME,
    get_minio_client,
    get_postgres_connection,
)


SOURCE_TABLES = [
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
]


def read_object(client, object_name):
    response = client.get_object(BUCKET_NAME, object_name)

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def read_silver(client):
    manifest = json.loads(
        read_object(client, "silver/latest.json").decode("utf-8")
    )

    print(f"Su dung Silver: {manifest['run_id']}", flush=True)

    tables = {}

    for table_name in SOURCE_TABLES:
        info = manifest["tables"][table_name]

        dataframe = pd.read_parquet(
            BytesIO(read_object(client, info["object"]))
        )

        if len(dataframe) != info["silver_rows"]:
            raise RuntimeError(
                f"So dong Silver khong khop: {table_name}"
            )

        tables[table_name] = dataframe

        print(
            f"[OK] Doc {table_name}: {len(dataframe):,} dong",
            flush=True,
        )

    return tables


def python_value(value):
    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    # Chuyen scalar NumPy sang scalar Python.
    if hasattr(value, "item"):
        return value.item()

    return value


def copy_dataframe(connection, table_name, dataframe):
    columns = list(dataframe.columns)

    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier("dw", table_name),
        sql.SQL(", ").join(
            sql.Identifier(column)
            for column in columns
        ),
    )

    with connection.cursor() as cursor:
        with cursor.copy(statement) as copy:
            for row in dataframe.itertuples(index=False, name=None):
                copy.write_row(
                    tuple(python_value(value) for value in row)
                )

        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM {}").format(
                sql.Identifier("dw", table_name)
            )
        )

        actual_rows = cursor.fetchone()[0]

    if actual_rows != len(dataframe):
        raise RuntimeError(
            f"dw.{table_name}: so dong khong khop."
        )

    print(
        f"[OK] Nap dw.{table_name}: {actual_rows:,} dong",
        flush=True,
    )


def read_key_map(connection, table_name, natural_key, surrogate_key):
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT {}, {} FROM {}").format(
                sql.Identifier(natural_key),
                sql.Identifier(surrogate_key),
                sql.Identifier("dw", table_name),
            )
        )

        return dict(cursor.fetchall())


def map_required(values, mapping, label):
    mapped = values.map(mapping)

    if mapped.isna().any():
        examples = values[mapped.isna()].head(5).tolist()

        raise RuntimeError(
            f"Khong anh xa duoc {label}. Vi du: {examples}"
        )

    return mapped.astype("Int64")


def date_keys(values):
    return (
        values.dt.strftime("%Y%m%d")
        .fillna("0")
        .astype("int64")
    )


def build_date_dimension(tables):
    minimums = []
    maximums = []

    for table_name in ["orders", "order_items", "order_reviews"]:
        dataframe = tables[table_name]

        for column in dataframe.columns:
            values = dataframe[column]

            if not is_datetime64_any_dtype(values.dtype):
                continue

            if values.notna().any():
                minimums.append(values.min())
                maximums.append(values.max())

    if not minimums:
        raise RuntimeError("Khong tim thay cot ngay gio trong Silver.")

    days = pd.Series(
        pd.date_range(
            start=min(minimums).normalize(),
            end=max(maximums).normalize(),
            freq="D",
        )
    )

    result = pd.DataFrame({
        "date_key": date_keys(days),
        "full_date": days.dt.date,
        "year_number": days.dt.year,
        "quarter_number": days.dt.quarter,
        "month_number": days.dt.month,
        "day_number": days.dt.day,
        # Thu Hai=1 ... Chu Nhat=7.
        "weekday_number": days.dt.dayofweek + 1,
        "is_weekend": days.dt.dayofweek >= 5,
    })

    unknown = pd.DataFrame([{
        "date_key": 0,
        "full_date": None,
        "year_number": None,
        "quarter_number": None,
        "month_number": None,
        "day_number": None,
        "weekday_number": None,
        "is_weekend": None,
    }])

    # Dung nullable integer de khong bien so nguyen thanh float.
    result = pd.concat([unknown, result], ignore_index=True)

    for column in [
        "date_key",
        "year_number",
        "quarter_number",
        "month_number",
        "day_number",
        "weekday_number",
    ]:
        result[column] = result[column].astype("Int64")

    result["is_weekend"] = result["is_weekend"].astype("boolean")

    return result


def load_dw(connection, tables):
    with connection.cursor() as cursor:
        # Thay toan bo bo du lieu DW trong cung transaction.
        # Khong tac dong den staging.
        cursor.execute(
            """
            TRUNCATE TABLE
                dw.fact_reviews,
                dw.fact_payments,
                dw.fact_order_items,
                dw.fact_orders,
                dw.dim_product,
                dw.dim_seller,
                dw.dim_customer,
                dw.dim_date
            RESTART IDENTITY;
            """
        )

    # 1. Nap Dimension.
    copy_dataframe(
        connection,
        "dim_date",
        build_date_dimension(tables),
    )

    customer_columns = [
        "customer_id",
        "customer_unique_id",
        "customer_zip_code_prefix",
        "customer_city",
        "customer_state",
    ]

    copy_dataframe(
        connection,
        "dim_customer",
        tables["customers"][customer_columns],
    )

    seller_columns = [
        "seller_id",
        "seller_zip_code_prefix",
        "seller_city",
        "seller_state",
    ]

    copy_dataframe(
        connection,
        "dim_seller",
        tables["sellers"][seller_columns],
    )

    products = tables["products"].rename(columns={
        "product_name_lenght": "product_name_length",
        "product_description_lenght": "product_description_length",
    })

    product_columns = [
        "product_id",
        "product_category_name",
        "product_category_name_english",
        "product_name_length",
        "product_description_length",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
        "category_missing",
        "category_translation_missing",
    ]

    copy_dataframe(
        connection,
        "dim_product",
        products[product_columns],
    )

    # 2. Lay anh xa natural ID -> surrogate key.
    customer_map = read_key_map(
        connection, "dim_customer", "customer_id", "customer_key"
    )

    seller_map = read_key_map(
        connection, "dim_seller", "seller_id", "seller_key"
    )

    product_map = read_key_map(
        connection, "dim_product", "product_id", "product_key"
    )

    # 3. Nap Fact Orders truoc cac Fact lien quan.
    orders = tables["orders"].copy()

    orders["customer_key"] = map_required(
        orders["customer_id"],
        customer_map,
        "customer_id",
    )

    orders["purchase_date_key"] = date_keys(
        orders["order_purchase_timestamp"]
    )

    order_columns = [
        "order_id",
        "customer_key",
        "purchase_date_key",
        "order_status",
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ]

    copy_dataframe(
        connection,
        "fact_orders",
        orders[order_columns],
    )

    order_customer_map = dict(
        zip(orders["order_id"], orders["customer_key"])
    )

    order_date_map = dict(
        zip(orders["order_id"], orders["purchase_date_key"])
    )

    # 4. Gan khach hang va ngay mua cho cac Fact con.
    for source_name, target_name in [
        ("order_items", "fact_order_items"),
        ("order_payments", "fact_payments"),
        ("order_reviews", "fact_reviews"),
    ]:
        dataframe = tables[source_name].copy()

        dataframe["customer_key"] = map_required(
            dataframe["order_id"],
            order_customer_map,
            f"{source_name}.order_id -> customer",
        )

        dataframe["purchase_date_key"] = map_required(
            dataframe["order_id"],
            order_date_map,
            f"{source_name}.order_id -> purchase_date",
        )

        if source_name == "order_items":
            dataframe["product_key"] = map_required(
                dataframe["product_id"],
                product_map,
                "product_id",
            )

            dataframe["seller_key"] = map_required(
                dataframe["seller_id"],
                seller_map,
                "seller_id",
            )

            columns = [
                "order_id",
                "order_item_id",
                "customer_key",
                "purchase_date_key",
                "product_key",
                "seller_key",
                "shipping_limit_date",
                "price",
                "freight_value",
            ]

        elif source_name == "order_payments":
            columns = [
                "order_id",
                "payment_sequential",
                "customer_key",
                "purchase_date_key",
                "payment_type",
                "payment_installments",
                "payment_value",
            ]

        else:
            columns = [
                "review_id",
                "order_id",
                "customer_key",
                "purchase_date_key",
                "review_score",
                "review_comment_title",
                "review_comment_message",
                "review_creation_date",
                "review_answer_timestamp",
            ]

        copy_dataframe(
            connection,
            target_name,
            dataframe[columns],
        )


def main():
    client = get_minio_client()

    # Doc va kiem tra Silver truoc khi thay doi database.
    tables = read_silver(client)

    with get_postgres_connection() as connection:
        load_dw(connection, tables)

    print("\nHOAN TAT: Da commit 4 Dimension va 4 Fact.")


if __name__ == "__main__":
    main()