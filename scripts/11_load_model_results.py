import argparse
import json

import pandas as pd
from psycopg import sql

from config import PROJECT_ROOT, get_postgres_connection


def python_value(value):
    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    if hasattr(value, "item"):
        return value.item()

    return value


def copy_dataframe(connection, table_name, dataframe):
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier("mart", table_name),
        sql.SQL(", ").join(
            sql.Identifier(column)
            for column in dataframe.columns
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
                sql.Identifier("mart", table_name)
            )
        )

        actual = cursor.fetchone()[0]

    if actual != len(dataframe):
        raise RuntimeError(f"So dong khong khop: {table_name}")

    print(f"[OK] mart.{table_name}: {actual:,} dong")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-run", default=None)
    args = parser.parse_args()

    manifest_path = (
        PROJECT_ROOT
        / "data"
        / "processed"
        / "mining"
        / "latest.json"
    )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    dataset_run = manifest["run_id"]
    decision_run = args.decision_run or dataset_run

    segments_path = (
        PROJECT_ROOT
        / "models"
        / "kmeans"
        / dataset_run
        / "customer_segments.parquet"
    )

    predictions_path = (
        PROJECT_ROOT
        / "models"
        / "decision_tree"
        / decision_run
        / "test_predictions.parquet"
    )

    for path in [segments_path, predictions_path]:
        if not path.is_file():
            raise FileNotFoundError(f"Khong tim thay: {path}")

    segments = pd.read_parquet(segments_path)
    predictions = pd.read_parquet(predictions_path)

    if segments.empty or predictions.empty:
        raise ValueError("File ket qua khong duoc rong.")

    if segments["customer_unique_id"].duplicated().any():
        raise ValueError("Ket qua K-Means bi trung khach hang.")

    if predictions["order_id"].duplicated().any():
        raise ValueError("Ket qua Decision Tree bi trung don.")

    if not predictions["model_score"].between(0, 1).all():
        raise ValueError("model_score khong hop le.")

    segments["dataset_run_id"] = dataset_run
    predictions["dataset_run_id"] = decision_run

    segments["reference_date"] = (
        pd.to_datetime(segments["reference_date"]).dt.date
    )

    segment_columns = [
        "customer_unique_id",
        "dataset_run_id",
        "reference_date",
        "recency",
        "frequency",
        "monetary",
        "cluster_id",
        "cluster_name",
    ]

    prediction_columns = [
        "order_id",
        "dataset_run_id",
        "customer_unique_id",
        "order_approved_at",
        "low_review",
        "model_score",
        "predicted_low_review",
    ]

    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                TRUNCATE TABLE
                    mart.customer_segments,
                    mart.decision_tree_test_predictions;
                """
            )

        copy_dataframe(
            connection,
            "customer_segments",
            segments[segment_columns],
        )

        copy_dataframe(
            connection,
            "decision_tree_test_predictions",
            predictions[prediction_columns],
        )

    print("\nHOAN TAT: Da nap ket qua hai mo hinh vao PostgreSQL.")


if __name__ == "__main__":
    main()