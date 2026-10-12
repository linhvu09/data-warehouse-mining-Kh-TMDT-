BEGIN;

CREATE TABLE IF NOT EXISTS mart.customer_segments (
    customer_unique_id VARCHAR(32) PRIMARY KEY,
    dataset_run_id     TEXT NOT NULL,
    reference_date     DATE NOT NULL,
    recency            INTEGER NOT NULL,
    frequency          INTEGER NOT NULL,
    monetary           NUMERIC(18, 2) NOT NULL,
    cluster_id         INTEGER NOT NULL,
    cluster_name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart.decision_tree_test_predictions (
    order_id             VARCHAR(32) PRIMARY KEY,
    dataset_run_id       TEXT NOT NULL,
    customer_unique_id   VARCHAR(32) NOT NULL,
    order_approved_at    TIMESTAMP NOT NULL,
    low_review           INTEGER NOT NULL CHECK (low_review IN (0, 1)),
    model_score          DOUBLE PRECISION NOT NULL
        CHECK (model_score BETWEEN 0 AND 1),
    predicted_low_review INTEGER NOT NULL
        CHECK (predicted_low_review IN (0, 1))
);

COMMIT;


CREATE OR REPLACE VIEW mart.customer_segment_profile AS
SELECT
    cluster_id,
    cluster_name,
    COUNT(*) AS customer_count,
    AVG(recency) AS average_recency,
    AVG(frequency) AS average_frequency,
    AVG(monetary) AS average_monetary,
    SUM(monetary) AS total_monetary
FROM mart.customer_segments
GROUP BY cluster_id, cluster_name;


CREATE OR REPLACE VIEW mart.decision_tree_test_metrics AS
WITH counts AS (
    SELECT
        COUNT(*) AS test_count,

        COUNT(*) FILTER (
            WHERE low_review = 1 AND predicted_low_review = 1
        ) AS tp,

        COUNT(*) FILTER (
            WHERE low_review = 0 AND predicted_low_review = 1
        ) AS fp,

        COUNT(*) FILTER (
            WHERE low_review = 1 AND predicted_low_review = 0
        ) AS fn,

        COUNT(*) FILTER (
            WHERE low_review = 0 AND predicted_low_review = 0
        ) AS tn

    FROM mart.decision_tree_test_predictions
)
SELECT
    *,
    (tp + tn)::NUMERIC / NULLIF(test_count, 0) AS accuracy,
    tp::NUMERIC / NULLIF(tp + fp, 0) AS precision_class_1,
    tp::NUMERIC / NULLIF(tp + fn, 0) AS recall_class_1,
    2.0 * tp / NULLIF(2 * tp + fp + fn, 0) AS f1_class_1
FROM counts;