import pandas as pd

from config import BUCKET_NAME, get_minio_client


TABLES = {
    "customers": (
        "olist_customers_dataset.csv",
        ["customer_id"],
    ),
    "orders": (
        "olist_orders_dataset.csv",
        ["order_id"],
    ),
    "order_items": (
        "olist_order_items_dataset.csv",
        ["order_id", "order_item_id"],
    ),
    "products": (
        "olist_products_dataset.csv",
        ["product_id"],
    ),
    "sellers": (
        "olist_sellers_dataset.csv",
        ["seller_id"],
    ),
    "order_payments": (
        "olist_order_payments_dataset.csv",
        ["order_id", "payment_sequential"],
    ),
    "order_reviews": (
        "olist_order_reviews_dataset.csv",
        ["review_id", "order_id"],
    ),
    "geolocation": (
        "olist_geolocation_dataset.csv",
        [],
    ),
    "category_translation": (
        "product_category_name_translation.csv",
        ["product_category_name"],
    ),
}


RELATIONS = [
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("order_items", "seller_id", "sellers", "seller_id"),
    ("order_payments", "order_id", "orders", "order_id"),
    ("order_reviews", "order_id", "orders", "order_id"),
    (
        "products",
        "product_category_name",
        "category_translation",
        "product_category_name",
    ),
]


NUMERIC_COLUMNS = {
    "order_items": [
        "order_item_id",
        "price",
        "freight_value",
    ],
    "order_payments": [
        "payment_sequential",
        "payment_installments",
        "payment_value",
    ],
    "order_reviews": ["review_score"],
    "products": [
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
    ],
    "geolocation": [
        "geolocation_lat",
        "geolocation_lng",
    ],
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


def read_bronze(client, filename):
    object_name = f"bronze/{filename}"
    response = client.get_object(BUCKET_NAME, object_name)

    try:
        return pd.read_csv(
            response,
            dtype="string",
            keep_default_na=False,
            encoding="utf-8",
        )
    finally:
        response.close()
        response.release_conn()


def inspect_table(name, dataframe, key_columns):
    print("\n" + "=" * 72)
    print(f"BANG: {name}")
    print(f"So dong: {len(dataframe):,}")
    print(f"So cot: {len(dataframe.columns)}")

    missing_counts = {
        column: int(dataframe[column].str.strip().eq("").sum())
        for column in dataframe.columns
    }

    column_summary = pd.DataFrame({
        "cot": list(dataframe.columns),
        "so_gia_tri_thieu": [
            missing_counts[column]
            for column in dataframe.columns
        ],
        "ty_le_thieu_pct": [
            round(
                missing_counts[column] / len(dataframe) * 100,
                2,
            ) if len(dataframe) else 0
            for column in dataframe.columns
        ],
    })

    print("\nCac cot va du lieu thieu:")
    print(column_summary.to_string(index=False))

    duplicate_rows = int(dataframe.duplicated().sum())
    print(f"\nSo dong trung hoan toan sau lan dau: {duplicate_rows:,}")

    if key_columns:
        key_missing = (
            dataframe[key_columns]
            .apply(lambda column: column.str.strip().eq(""))
            .any(axis=1)
        )

        duplicate_keys = dataframe.duplicated(
            subset=key_columns,
            keep=False,
        )

        print(f"Khoa dang khao sat: {', '.join(key_columns)}")
        print(f"So dong thieu thanh phan khoa: {int(key_missing.sum()):,}")
        print(
            "So dong thuoc nhom trung khoa: "
            f"{int(duplicate_keys.sum()):,}"
        )

        if duplicate_keys.any():
            print("Vi du khoa trung:")
            print(
                dataframe.loc[duplicate_keys, key_columns]
                .head(5)
                .to_string(index=False)
            )

    print("\nKiem tra cac cot so:")

    for column in NUMERIC_COLUMNS.get(name, []):
        values = dataframe[column].str.strip()
        parsed = pd.to_numeric(values, errors="coerce")
        invalid = values.ne("") & parsed.isna()

        print(
            f"  {column}: "
            f"khong chuyen duoc thanh so={int(invalid.sum()):,}, "
            f"min={parsed.min()}, max={parsed.max()}"
        )

    print("\nKiem tra cac cot ngay gio:")

    for column in DATE_COLUMNS.get(name, []):
        values = dataframe[column].str.strip()

        parsed = pd.to_datetime(
            values,
            format="ISO8601",
            errors="coerce",
        )

        invalid = values.ne("") & parsed.isna()

        print(
            f"  {column}: "
            f"sai dinh dang={int(invalid.sum()):,}, "
            f"min={parsed.min()}, max={parsed.max()}"
        )

    if name == "orders":
        print("\nPhan bo trang thai don hang:")
        print(dataframe["order_status"].value_counts().to_string())

    if name == "order_reviews":
        scores = pd.to_numeric(
            dataframe["review_score"],
            errors="coerce",
        )

        invalid_scores = (
            scores.isna()
            | ~scores.isin([1, 2, 3, 4, 5])
        )

        print("\nPhan bo diem danh gia:")
        print(dataframe["review_score"].value_counts().to_string())
        print(
            "So diem danh gia thieu hoac ngoai 1-5: "
            f"{int(invalid_scores.sum()):,}"
        )


def inspect_relations(dataframes):
    print("\n" + "=" * 72)
    print("KIEM TRA QUAN HE GIUA CAC BANG")

    for child, child_column, parent, parent_column in RELATIONS:
        child_values = dataframes[child][child_column]
        parent_values = dataframes[parent][parent_column]

        missing = child_values.str.strip().eq("")
        unmatched = ~missing & ~child_values.isin(parent_values)

        print(
            f"\n{child}.{child_column}"
            f" -> {parent}.{parent_column}"
        )

        print(f"  So dong thieu tham chieu: {int(missing.sum()):,}")
        print(f"  So dong khong tim thay bang cha: {int(unmatched.sum()):,}")

        if unmatched.any():
            examples = (
                child_values[unmatched]
                .drop_duplicates()
                .head(5)
                .tolist()
            )

            print(f"  Vi du gia tri khong khop: {examples}")


def main():
    client = get_minio_client()
    dataframes = {}

    for name, (filename, key_columns) in TABLES.items():
        print(f"\nDang doc bronze/{filename} ...", flush=True)

        dataframe = read_bronze(client, filename)
        inspect_table(name, dataframe, key_columns)

        if name != "geolocation":
            dataframes[name] = dataframe

        del dataframe

    inspect_relations(dataframes)

    print("\nHOAN TAT: Da khao sat 9 CSV tu MinIO.")


if __name__ == "__main__":
    main()