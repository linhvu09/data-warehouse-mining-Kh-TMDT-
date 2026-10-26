BEGIN;

-- Mỗi dòng là thông tin tổng hợp của một cụm.
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


-- Chuyển bốn loại kết quả dự đoán thành bốn dòng.
CREATE OR REPLACE VIEW mart.decision_tree_confusion_summary AS
SELECT
    'TP'::TEXT AS result_type,
    'Correct low-review prediction'::TEXT AS description,
    tp AS order_count
FROM mart.decision_tree_test_metrics

UNION ALL

SELECT
    'FP',
    'False alarm',
    fp
FROM mart.decision_tree_test_metrics

UNION ALL

SELECT
    'FN',
    'Missed low review',
    fn
FROM mart.decision_tree_test_metrics

UNION ALL

SELECT
    'TN',
    'Correct non-low prediction',
    tn
FROM mart.decision_tree_test_metrics;

COMMIT;