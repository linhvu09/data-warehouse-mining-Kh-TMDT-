import json

import pandas as pd
import pyarrow.parquet as pq

from config import PROJECT_ROOT


def main():
    silver_root = PROJECT_ROOT / "data" / "processed" / "silver"
    latest_path = silver_root / "latest.json"

    manifest = json.loads(
        latest_path.read_text(encoding="utf-8")
    )

    run_dir = silver_root / manifest["run_id"]

    print(f"KIEM TRA SILVER: {manifest['run_id']}")

    for table_name, info in manifest["tables"].items():
        path = run_dir / f"{table_name}.parquet"
        actual_rows = pq.ParquetFile(path).metadata.num_rows

        if actual_rows != info["silver_rows"]:
            raise RuntimeError(f"So dong khong khop: {table_name}")

        print(f"[OK] {table_name}: {actual_rows:,} dong")

    products = pd.read_parquet(run_dir / "products.parquet")

    print("\nPRODUCTS:")
    print(
        "San pham thieu danh muc:",
        int(products["category_missing"].sum()),
    )
    print(
        "San pham thieu ban dich:",
        int(products["category_translation_missing"].sum()),
    )

    print("\nVi du danh muc thieu:")
    print(
        products.loc[
            products["category_missing"],
            [
                "product_category_name",
                "product_category_name_english",
            ],
        ].head().to_string(index=False)
    )

    orders = pd.read_parquet(run_dir / "orders.parquet")

    print("\nKIEU DU LIEU ORDERS:")
    print(orders.dtypes.to_string())

    print(
        "\nSo dong thieu ngay giao:",
        int(orders["order_delivered_customer_date"].isna().sum()),
    )

    items = pd.read_parquet(run_dir / "order_items.parquet")

    print("\nKIEU GIA TRI TIEN:")
    print(type(items["price"].iloc[0]).__name__)

    print("\nHOAN TAT: Kiem tra Silver thanh cong.")


if __name__ == "__main__":
    main()