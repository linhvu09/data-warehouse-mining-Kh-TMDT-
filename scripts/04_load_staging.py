from time import perf_counter

import pandas as pd
from psycopg import sql

from config import (
    BUCKET_NAME,
    get_minio_client,
    get_postgres_connection,
)


CHUNK_SIZE = 50_000

TABLE_FILES = {
    "customers": "olist_customers_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def get_table_columns(cursor, table_name):
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY ordinal_position;
        """,
        ("staging", table_name),
    )

    columns = [row[0] for row in cursor.fetchall()]

    if not columns:
        raise RuntimeError(
            f"Khong tim thay bang staging.{table_name}. "
            "Hay chay file SQL tao bang truoc."
        )

    return columns


def load_table(client, connection, table_name, filename):
    started = perf_counter()
    object_name = f"bronze/{filename}"
    total_rows = 0

    print(
        f"\nDang nap: {BUCKET_NAME}/{object_name}"
        f" -> staging.{table_name}",
        flush=True,
    )

    with connection.cursor() as cursor:
        table_columns = get_table_columns(cursor, table_name)

        response = client.get_object(
            bucket_name=BUCKET_NAME,
            object_name=object_name,
        )

        try:
            with pd.read_csv(
                response,
                dtype="string",
                keep_default_na=False,
                encoding="utf-8",
                chunksize=CHUNK_SIZE,
            ) as reader:

                cursor.execute(
                    sql.SQL("TRUNCATE TABLE {}").format(
                        sql.Identifier("staging", table_name)
                    )
                )

                copy_statement = sql.SQL(
                    "COPY {} ({}) FROM STDIN"
                ).format(
                    sql.Identifier("staging", table_name),
                    sql.SQL(", ").join(
                        sql.Identifier(column)
                        for column in table_columns
                    ),
                )

                with cursor.copy(copy_statement) as copy:
                    for chunk_number, chunk in enumerate(
                        reader,
                        start=1,
                    ):
                        csv_columns = list(chunk.columns)

                        if csv_columns != table_columns:
                            raise RuntimeError(
                                f"Cot CSV khong khop staging.{table_name}.\n"
                                f"CSV: {csv_columns}\n"
                                f"PostgreSQL: {table_columns}"
                            )

                        for row in chunk.itertuples(
                            index=False,
                            name=None,
                        ):
                            record = tuple(
                                None if value == "" else value
                                for value in row
                            )

                            copy.write_row(record)

                        total_rows += len(chunk)

                        print(
                            f"  Chunk {chunk_number}: "
                            f"da gui {total_rows:,} dong",
                            flush=True,
                        )

        finally:
            response.close()
            response.release_conn()

        if total_rows == 0:
            raise RuntimeError(
                f"File {filename} khong co dong du lieu."
            )

        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM {}").format(
                sql.Identifier("staging", table_name)
            )
        )

        database_rows = cursor.fetchone()[0]

        if database_rows != total_rows:
            raise RuntimeError(
                f"So dong khong khop staging.{table_name}: "
                f"CSV={total_rows:,}, PostgreSQL={database_rows:,}"
            )

    elapsed = perf_counter() - started

    print(
        f"  [OK] staging.{table_name}: "
        f"{database_rows:,} dong, {elapsed:.1f} giay",
        flush=True,
    )

    return {
        "table": f"staging.{table_name}",
        "csv_rows": total_rows,
        "database_rows": database_rows,
    }


def main():
    started = perf_counter()
    client = get_minio_client()
    results = []

    print("BAT DAU NAP 9 BANG STAGING", flush=True)

    for filename in TABLE_FILES.values():
        object_name = f"bronze/{filename}"
        info = client.stat_object(BUCKET_NAME, object_name)

        if info.size == 0:
            raise RuntimeError(f"Object rong: {object_name}")

    print("[OK] Du 9 file nguon tren MinIO", flush=True)

    with get_postgres_connection() as connection:
        for table_name, filename in TABLE_FILES.items():
            result = load_table(
                client,
                connection,
                table_name,
                filename,
            )

            results.append(result)

    print("\nKET QUA SAU KHI COMMIT:")
    print(pd.DataFrame(results).to_string(index=False))

    elapsed = perf_counter() - started

    print(
        f"\nHOAN TAT: Da nap 9 bang staging "
        f"trong {elapsed:.1f} giay."
    )


if __name__ == "__main__":
    main()