import json
from io import BytesIO

import joblib
import matplotlib

# Ghi hinh ra file, khong can cua so GUI.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    PrecisionRecallDisplay,
    accuracy_score,
    average_precision_score,
    classification_report,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.tree import DecisionTreeClassifier, plot_tree

from config import BUCKET_NAME, PROJECT_ROOT, get_minio_client


NUMERIC_FEATURES = [
    "purchase_month",
    "purchase_weekday",
    "purchase_hour",
    "item_count",
    "distinct_product_count",
    "seller_count",
    "category_count",
    "item_sales_value",
    "freight_value",
    "minimum_item_price",
    "maximum_item_price",
    "average_product_weight_g",
    "payment_count",
    "payment_value",
    "max_payment_installments",
]

CATEGORICAL_FEATURES = ["customer_state"]

FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TARGET = "low_review"
RANDOM_STATE = 42


def read_object(client, object_name):
    response = client.get_object(BUCKET_NAME, object_name)

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def load_data():
    client = get_minio_client()

    manifest = json.loads(
        read_object(
            client,
            "gold/mining/latest.json",
        ).decode("utf-8")
    )

    info = manifest["datasets"]["review_training_timed"]

    dataframe = pd.read_parquet(
        BytesIO(read_object(client, info["object"]))
    )

    if len(dataframe) != info["rows"]:
        raise RuntimeError("So dong khong khop manifest.")

    if dataframe["order_id"].duplicated().any():
        raise ValueError("Du lieu huan luyen bi trung order_id.")

    for column in NUMERIC_FEATURES:
        dataframe[column] = pd.to_numeric(
            dataframe[column],
            errors="raise",
        ).astype("float64")

    for column in CATEGORICAL_FEATURES:
        dataframe[column] = (
            dataframe[column]
            .astype("string")
            .fillna("unknown")
            .astype(str)
        )

    for column in ["order_approved_at", "label_available_at"]:
        dataframe[column] = pd.to_datetime(
            dataframe[column],
            errors="raise",
        )

        if dataframe[column].isna().any():
            raise ValueError(f"Thieu cot thoi gian {column}.")

    dataframe[TARGET] = dataframe[TARGET].astype("int64")

    if set(dataframe[TARGET].unique()) != {0, 1}:
        raise ValueError("Du lieu phai co du hai nhan 0 va 1.")

    dataframe = dataframe.sort_values(
        ["order_approved_at", "order_id"]
    ).reset_index(drop=True)

    return dataframe, manifest


def split_by_time(dataframe):
    count = len(dataframe)

    # Cat theo ngay de khong tach cung mot ngay sang hai tap.
    validation_start = (
        dataframe["order_approved_at"]
        .iloc[int(count * 0.60)]
        .normalize()
    )

    test_start = (
        dataframe["order_approved_at"]
        .iloc[int(count * 0.80)]
        .normalize()
    )

    if validation_start >= test_start:
        raise ValueError("Khong du khoang thoi gian de chia tap.")

    train_period = dataframe[
        dataframe["order_approved_at"] < validation_start
    ]

    train = train_period[
        train_period["label_available_at"] < validation_start
    ].copy()

    validation_period = dataframe[
        (dataframe["order_approved_at"] >= validation_start)
        & (dataframe["order_approved_at"] < test_start)
    ]

    validation = validation_period[
        validation_period["label_available_at"] < test_start
    ].copy()

    test = dataframe[
        dataframe["order_approved_at"] >= test_start
    ].copy()

    print(f"\nValidation bat dau: {validation_start}")
    print(f"Test bat dau: {test_start}")

    print(
        "Train loai vi nhan chua co:",
        len(train_period) - len(train),
    )

    print(
        "Validation loai vi nhan chua co:",
        len(validation_period) - len(validation),
    )

    for name, subset in [
        ("train", train),
        ("validation", validation),
        ("test", test),
    ]:
        if subset.empty or set(subset[TARGET].unique()) != {0, 1}:
            raise ValueError(f"Tap {name} rong hoac thieu mot nhan.")

        print(
            f"{name}: {len(subset):,} dong; "
            f"ty le nhan 1={subset[TARGET].mean():.2%}; "
            f"tu {subset['order_approved_at'].min()} "
            f"den {subset['order_approved_at'].max()}"
        )

    return train, validation, test, validation_start, test_start


def make_pipeline(max_depth, min_samples_leaf, class_weight):
    preprocessing = ColumnTransformer(
        transformers=[
            (
                "numeric",
                SimpleImputer(
                    strategy="median",
                    add_indicator=True,
                ),
                NUMERIC_FEATURES,
            ),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                CATEGORICAL_FEATURES,
            ),
        ]
    )

    return Pipeline([
        ("preprocessing", preprocessing),
        (
            "tree",
            DecisionTreeClassifier(
                max_depth=max_depth,
                min_samples_leaf=min_samples_leaf,
                class_weight=class_weight,
                random_state=RANDOM_STATE,
            ),
        ),
    ])


def choose_model(train, validation):
    best_model = None
    best_score = -1.0
    best_parameters = None

    for depth in [3, 5, 8]:
        for leaf_size in [200, 500]:
            for weight in [None, "balanced"]:
                model = make_pipeline(
                    depth,
                    leaf_size,
                    weight,
                )

                model.fit(train[FEATURES], train[TARGET])

                scores = model.predict_proba(
                    validation[FEATURES]
                )[:, 1]

                average_precision = average_precision_score(
                    validation[TARGET],
                    scores,
                )

                print(
                    f"depth={depth}, leaf={leaf_size}, "
                    f"weight={weight}, "
                    f"validation_AP={average_precision:.4f}",
                    flush=True,
                )

                if average_precision > best_score:
                    best_score = average_precision
                    best_model = model

                    best_parameters = {
                        "max_depth": depth,
                        "min_samples_leaf": leaf_size,
                        "class_weight": weight,
                    }

    return best_model, best_parameters, best_score


def choose_threshold(model, validation):
    scores = model.predict_proba(validation[FEATURES])[:, 1]

    precision, recall, thresholds = precision_recall_curve(
        validation[TARGET],
        scores,
    )

    # Phan tu cuoi cua precision/recall khong co threshold.
    precision = precision[:-1]
    recall = recall[:-1]

    denominator = precision + recall

    f1_values = np.divide(
        2 * precision * recall,
        denominator,
        out=np.zeros_like(denominator),
        where=denominator > 0,
    )

    best_index = int(np.argmax(f1_values))

    return float(thresholds[best_index]), float(f1_values[best_index])


def save_charts(model, y_test, scores, predictions, output_dir):
    fig, ax = plt.subplots(figsize=(6, 5))

    ConfusionMatrixDisplay.from_predictions(
        y_test,
        predictions,
        labels=[0, 1],
        display_labels=["Score 3-5", "Score 1-2"],
        cmap="Blues",
        ax=ax,
    )

    ax.set_title("Decision Tree - Test")
    fig.tight_layout()
    fig.savefig(output_dir / "confusion_matrix.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 5))

    PrecisionRecallDisplay.from_predictions(
        y_test,
        scores,
        name="Decision Tree",
        ax=ax,
    )

    ax.axhline(
        float(y_test.mean()),
        color="gray",
        linestyle="--",
        label="Test positive rate",
    )

    ax.legend()
    fig.tight_layout()
    fig.savefig(output_dir / "precision_recall.png", dpi=180)
    plt.close(fig)

    feature_names = (
        model.named_steps["preprocessing"]
        .get_feature_names_out()
    )

    importance = pd.Series(
        model.named_steps["tree"].feature_importances_,
        index=feature_names,
    ).sort_values(ascending=False)

    fig, ax = plt.subplots(figsize=(10, 6))
    importance.head(15).sort_values().plot.barh(ax=ax)
    ax.set_title("Tree feature importance")
    fig.tight_layout()
    fig.savefig(output_dir / "feature_importance.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(26, 12))

    plot_tree(
        model.named_steps["tree"],
        max_depth=3,
        feature_names=list(feature_names),
        class_names=["Score 3-5", "Score 1-2"],
        filled=True,
        rounded=True,
        fontsize=7,
        ax=ax,
    )

    ax.set_title("Decision Tree - First 3 Levels")
    fig.tight_layout()
    fig.savefig(output_dir / "tree_preview.png", dpi=180)
    plt.close(fig)


def main():
    dataframe, manifest = load_data()

    print(f"Bo du lieu: {manifest['run_id']}")
    print(f"Tong so dong: {len(dataframe):,}")

    train, validation, test, validation_start, test_start = (
        split_by_time(dataframe)
    )

    print("\nCHON CAU HINH TREN VALIDATION:")

    model, parameters, validation_ap = choose_model(
        train,
        validation,
    )

    threshold, validation_f1 = choose_threshold(
        model,
        validation,
    )

    print("\nCau hinh duoc chon:", parameters)
    print(f"Validation AP: {validation_ap:.4f}")
    print(f"Nguong duoc chon: {threshold:.4f}")
    print(f"Validation F1 tai nguong: {validation_f1:.4f}")

    # Chi danh gia test sau khi da chon model va threshold.
    scores = model.predict_proba(test[FEATURES])[:, 1]
    predictions = (scores >= threshold).astype("int64")
    y_test = test[TARGET]

    baseline = DummyClassifier(strategy="most_frequent")
    baseline.fit(
        np.zeros((len(train), 1)),
        train[TARGET],
    )

    baseline_predictions = baseline.predict(
        np.zeros((len(test), 1))
    )

    print("\nBASELINE - LUON DOAN LOP PHO BIEN:")
    print(
        f"Accuracy: "
        f"{accuracy_score(y_test, baseline_predictions):.4f}"
    )
    print(
        f"F1 lop 1: "
        f"{f1_score(y_test, baseline_predictions, zero_division=0):.4f}"
    )

    metrics = {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision_class_1": float(
            precision_score(y_test, predictions, zero_division=0)
        ),
        "recall_class_1": float(
            recall_score(y_test, predictions, zero_division=0)
        ),
        "f1_class_1": float(
            f1_score(y_test, predictions, zero_division=0)
        ),
        "average_precision": float(
            average_precision_score(y_test, scores)
        ),
        "test_positive_rate": float(y_test.mean()),
    }

    print("\nDECISION TREE - TEST:")
    print(json.dumps(metrics, indent=2))

    print(
        classification_report(
            y_test,
            predictions,
            labels=[0, 1],
            target_names=["Score 3-5", "Score 1-2"],
            zero_division=0,
        )
    )

    output_dir = (
        PROJECT_ROOT
        / "models"
        / "decision_tree"
        / manifest["run_id"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    bundle = {
        "pipeline": model,
        "threshold": threshold,
        "features": FEATURES,
        "target": TARGET,
        "dataset_run_id": manifest["run_id"],
        "parameters": parameters,
        "validation_start": str(validation_start),
        "test_start": str(test_start),
        "test_metrics": metrics,
    }

    joblib.dump(
        bundle,
        output_dir / "decision_tree.joblib",
    )

    output = test[[
        "order_id",
        "customer_unique_id",
        "order_approved_at",
        TARGET,
    ]].copy()

    output["model_score"] = scores
    output["predicted_low_review"] = predictions

    output.to_parquet(
        output_dir / "test_predictions.parquet",
        index=False,
    )

    save_charts(
        model,
        y_test,
        scores,
        predictions,
        output_dir,
    )

    print("\nHOAN TAT: Da huan luyen va danh gia Decision Tree.")
    print(f"Ket qua: {output_dir}")


if __name__ == "__main__":
    main()