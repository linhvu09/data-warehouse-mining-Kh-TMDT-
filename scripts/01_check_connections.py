from config import (
    BUCKET_NAME,
    get_minio_client,
    get_postgres_connection,
)


def check_postgres():
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    current_database(),
                    current_user,
                    current_setting('server_version');
                """
            )

            database, username, version = cursor.fetchone()

            print("[OK] Ket noi PostgreSQL")
            print(f"     Database: {database}")
            print(f"     User: {username}")
            print(f"     Version: {version}")

            cursor.execute(
                """
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name IN ('staging', 'dw', 'mart')
                ORDER BY schema_name;
                """
            )

            schemas = [row[0] for row in cursor.fetchall()]

            if set(schemas) != {"staging", "dw", "mart"}:
                raise RuntimeError(
                    f"Chua du 3 schema. Dang co: {schemas}"
                )

            print(f"[OK] Schema: {', '.join(schemas)}")


def check_minio():
    client = get_minio_client()

    if not client.bucket_exists(BUCKET_NAME):
        client.make_bucket(BUCKET_NAME)
        print(f"[OK] Da tao bucket: {BUCKET_NAME}")
    else:
        print(f"[OK] Bucket da ton tai: {BUCKET_NAME}")

    print("[OK] Ket noi MinIO")


def main():
    check_postgres()
    check_minio()
    print("\nHOAN TAT: PostgreSQL va MinIO san sang.")


if __name__ == "__main__":
    main()