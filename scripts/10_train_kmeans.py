import argparse
import json
from io import BytesIO

import joblib
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from config import BUCKET_NAME, PROJECT_ROOT, get_minio_client


FEATURES = ["recency", "frequency", "monetary"]
RANDOM_STATE = 42


def read_object(client, object_name):
    response = client.get_object(BUCKET_NAME, object_name)

    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def load_rfm():
    client = get_minio_client()

    manifest = json.loads(
        read_object(
            client,
            "gold/mining/latest.json",
        ).decode("utf-8")
    )

    info = manifest["datasets"]["customer_rfm"]

    dataframe = pd.read_parquet(
        BytesIO(read_object(client, info["object"]))
    )

    if len(dataframe) != info["rows"]:
        raise ValueError("So dong RFM khong khop manifest.")

    if dataframe["customer_unique_id"].duplicated().any():
        raise ValueError("RFM bi trung khach hang.")

    numeric = dataframe[FEATURES].apply(
        pd.to_numeric,
        errors="raise",
    ).astype("float64")

    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("RFM co gia tri thieu hoac vo han.")

    if numeric["recency"].lt(0).any():
        raise ValueError("Recency am.")

    if numeric["frequency"].lt(1).any():
        raise ValueError("Frequency nho hon 1.")

    if numeric["monetary"].lt(0).any():
        raise ValueError("Monetary am.")

    if dataframe["reference_date"].nunique() != 1:
        raise ValueError("RFM phai dung cung mot ngay tham chieu.")

    return dataframe, numeric, manifest


def save_charts(result, evaluation, scaled, output_dir):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    axes[0].plot(
        evaluation["k"],
        evaluation["inertia"],
        marker="o",
    )
    axes[0].set_title("Elbow")
    axes[0].set_xlabel("Number of clusters")
    axes[0].set_ylabel("Inertia")

    axes[1].plot(
        evaluation["k"],
        evaluation["silhouette"],
        marker="o",
    )
    axes[1].set_title("Sampled Silhouette")
    axes[1].set_xlabel("Number of clusters")
    axes[1].set_ylabel("Silhouette score")

    fig.tight_layout()
    fig.savefig(output_dir / "choose_k.png", dpi=180)
    plt.close(fig)

    counts = result["cluster_id"].value_counts().sort_index()

    fig, ax = plt.subplots(figsize=(8, 5))
    counts.plot.bar(ax=ax)
    ax.set_title("Customers per cluster")
    ax.set_xlabel("Cluster")
    ax.set_ylabel("Customer count")
    fig.tight_layout()
    fig.savefig(output_dir / "cluster_sizes.png", dpi=180)
    plt.close(fig)

    sample = result.sample(
        n=min(5_000, len(result)),
        random_state=RANDOM_STATE,
    )

    fig, ax = plt.subplots(figsize=(9, 6))

    scatter = ax.scatter(
        sample["recency"],
        pd.to_numeric(sample["monetary"]).astype(float),
        c=sample["cluster_id"],
        cmap="tab10",
        s=10,
        alpha=0.5,
    )

    ax.set_yscale("log")
    ax.set_xlabel("Recency")
    ax.set_ylabel("Monetary - log scale")
    ax.set_title("Customer clusters")
    fig.colorbar(scatter, ax=ax, label="Cluster")
    fig.tight_layout()
    fig.savefig(output_dir / "rfm_clusters.png", dpi=180)
    plt.close(fig)

    scaled_profile = pd.DataFrame(scaled, columns=FEATURES)
    scaled_profile["cluster_id"] = result["cluster_id"].to_numpy()

    scaled_profile = (
        scaled_profile.groupby("cluster_id")[FEATURES].mean()
    )

    fig, ax = plt.subplots(figsize=(9, 5))
    scaled_profile.plot.bar(ax=ax)
    ax.set_title("Mean standardized log-RFM by cluster")
    ax.set_xlabel("Cluster")
    ax.set_ylabel("Standardized value")
    fig.tight_layout()
    fig.savefig(output_dir / "cluster_profiles.png", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--k",
        type=int,
        choices=range(2, 9),
        default=None,
    )
    args = parser.parse_args()

    rfm, numeric, manifest = load_rfm()

    print(f"Bo du lieu: {manifest['run_id']}")
    print(f"So khach hang: {len(rfm):,}")
    print(
        "Ty le khach chi mua 1 lan:",
        f"{numeric['frequency'].eq(1).mean():.2%}",
    )

    preprocessing = Pipeline([
        ("log", FunctionTransformer(np.log1p)),
        ("scale", StandardScaler()),
    ])

    scaled = preprocessing.fit_transform(numeric)

    # Dung cung mot mau de so sanh cac gia tri k.
    rng = np.random.default_rng(RANDOM_STATE)

    sample_indices = rng.choice(
        len(rfm),
        size=min(2_000, len(rfm)),
        replace=False,
    )

    evaluations = []
    fitted_models = {}

    for k in range(2, 9):
        model = KMeans(
            n_clusters=k,
            n_init=10,
            random_state=RANDOM_STATE,
        )

        labels = model.fit_predict(scaled)
        sampled_labels = labels[sample_indices]

        unique_labels = len(np.unique(sampled_labels))

        if 2 <= unique_labels < len(sample_indices):
            score = silhouette_score(
                scaled[sample_indices],
                sampled_labels,
            )
        else:
            score = np.nan

        smallest_cluster = int(
            pd.Series(labels).value_counts().min()
        )

        evaluations.append({
            "k": k,
            "inertia": float(model.inertia_),
            "silhouette": float(score),
            "smallest_cluster": smallest_cluster,
        })

        fitted_models[k] = model

        print(
            f"k={k}: inertia={model.inertia_:.2f}, "
            f"silhouette={score:.4f}, "
            f"smallest_cluster={smallest_cluster:,}",
            flush=True,
        )

    evaluation = pd.DataFrame(evaluations)
    valid_scores = evaluation.dropna(subset=["silhouette"])

    if valid_scores.empty:
        raise RuntimeError("Khong tinh duoc Silhouette.")

    if args.k is None:
        best_row = valid_scores.loc[
            valid_scores["silhouette"].idxmax()
        ]
        selected_k = int(best_row["k"])
    else:
        selected_k = args.k

    selected_model = fitted_models[selected_k]
    labels = selected_model.labels_

    print(f"\nSo cum duoc chon: {selected_k}")

    # Kiem tra muc do on dinh khi doi random seed.
    stability = []

    for seed in [7, 21]:
        alternative = KMeans(
            n_clusters=selected_k,
            n_init=10,
            random_state=seed,
        )

        alternative_labels = alternative.fit_predict(scaled)

        ari = adjusted_rand_score(labels, alternative_labels)
        stability.append(float(ari))

        print(f"Stability ARI voi seed={seed}: {ari:.4f}")

    result = rfm.copy()
    result["cluster_id"] = labels.astype("int64")
    result["cluster_name"] = (
        "Cluster " + result["cluster_id"].astype(str)
    )

    numeric_profile = numeric.copy()
    numeric_profile["cluster_id"] = labels

    profile = numeric_profile.groupby("cluster_id").agg(
        customer_count=("recency", "size"),
        recency_mean=("recency", "mean"),
        recency_median=("recency", "median"),
        frequency_mean=("frequency", "mean"),
        frequency_median=("frequency", "median"),
        monetary_mean=("monetary", "mean"),
        monetary_median=("monetary", "median"),
    )

    profile["customer_percentage"] = (
        100 * profile["customer_count"] / len(result)
    )

    print("\nDAC DIEM CAC CUM:")
    print(profile.round(2).to_string())

    output_dir = (
        PROJECT_ROOT
        / "models"
        / "kmeans"
        / manifest["run_id"]
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline = Pipeline([
        ("preprocessing", preprocessing),
        ("kmeans", selected_model),
    ])

    joblib.dump(
        {
            "pipeline": pipeline,
            "features": FEATURES,
            "selected_k": selected_k,
            "dataset_run_id": manifest["run_id"],
            "stability_ari": stability,
        },
        output_dir / "kmeans.joblib",
    )

    result.to_parquet(
        output_dir / "customer_segments.parquet",
        index=False,
    )

    profile.reset_index().to_parquet(
        output_dir / "cluster_profile.parquet",
        index=False,
    )

    evaluation.to_parquet(
        output_dir / "k_evaluation.parquet",
        index=False,
    )

    save_charts(result, evaluation, scaled, output_dir)

    print("\nHOAN TAT: Da phan nhom khach hang bang K-Means.")
    print(f"Ket qua: {output_dir}")


if __name__ == "__main__":
    main()