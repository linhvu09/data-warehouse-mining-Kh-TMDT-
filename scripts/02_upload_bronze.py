from config import BUCKET_NAME, RAW_DIR, get_minio_client


EXPECTED_FILES = [
    "olist_customers_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_geolocation_dataset.csv",
    "product_category_name_translation.csv",
]


def main():
    # Kiem tra du 9 file truoc khi upload.
    problems = []

    for filename in EXPECTED_FILES:
        path = RAW_DIR / filename

        if not path.is_file():
            problems.append(f"Thieu file: {path}")
        elif path.stat().st_size == 0:
            problems.append(f"File rong: {path}")

    if problems:
        raise RuntimeError("\n".join(problems))

    client = get_minio_client()

    if not client.bucket_exists(BUCKET_NAME):
        client.make_bucket(BUCKET_NAME)

    for index, filename in enumerate(EXPECTED_FILES, start=1):
        path = RAW_DIR / filename
        object_name = f"bronze/{filename}"

        print(
            f"[{index}/{len(EXPECTED_FILES)}] "
            f"Dang upload: {filename}",
            flush=True,
        )

        client.fput_object(
            bucket_name=BUCKET_NAME,
            object_name=object_name,
            file_path=str(path),
            content_type="text/csv",
        )

        uploaded = client.stat_object(BUCKET_NAME, object_name)

        if uploaded.size != path.stat().st_size:
            raise RuntimeError(
                f"Kich thuoc khong khop: {filename}"
            )

        print(
            f"    [OK] {BUCKET_NAME}/{object_name} "
            f"({uploaded.size:,} bytes)"
        )

    print("\nHOAN TAT: Da upload va kiem tra kich thuoc 9 CSV.")


if __name__ == "__main__":
    main()