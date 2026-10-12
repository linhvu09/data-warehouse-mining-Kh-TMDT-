CREATE OR REPLACE VIEW mart.review_training_timed AS
SELECT
    training.*,
    summary.review_answer_timestamp AS label_available_at
FROM mart.review_training AS training
JOIN mart.order_summary AS summary
    ON training.order_id = summary.order_id;


SELECT
    COUNT(*) AS training_rows,
    COUNT(*) FILTER (
        WHERE label_available_at IS NULL
    ) AS missing_label_time
FROM mart.review_training_timed;