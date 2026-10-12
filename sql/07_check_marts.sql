-- Order summary phai co cung so dong voi Fact Orders.
SELECT
    (SELECT COUNT(*) FROM dw.fact_orders) AS fact_order_count,
    (SELECT COUNT(*) FROM mart.order_summary) AS summary_count;


-- Khong duoc trung order_id.
SELECT COUNT(*) AS duplicate_order_groups
FROM (
    SELECT order_id
    FROM mart.order_summary
    GROUP BY order_id
    HAVING COUNT(*) > 1
) AS duplicates;


-- Tong tien summary phai khop Fact, khong bi nhan dong.
SELECT
    'item_sales_value' AS metric,
    (SELECT SUM(price)
     FROM dw.fact_order_items) AS fact_total,
    (SELECT SUM(item_sales_value)
     FROM mart.order_summary) AS mart_total

UNION ALL
SELECT
    'freight_value',
    (SELECT SUM(freight_value)
     FROM dw.fact_order_items),
    (SELECT SUM(freight_value)
     FROM mart.order_summary)

UNION ALL
SELECT
    'payment_value',
    (SELECT SUM(payment_value)
     FROM dw.fact_payments),
    (SELECT SUM(payment_value)
     FROM mart.order_summary);


-- RFM: so khach hang va khoang gia tri.
SELECT
    COUNT(*) AS customer_count,
    MIN(recency) AS min_recency,
    MAX(recency) AS max_recency,
    MIN(frequency) AS min_frequency,
    MAX(frequency) AS max_frequency,
    MIN(monetary) AS min_monetary,
    MAX(monetary) AS max_monetary
FROM mart.customer_rfm;


-- Phai bang 0.
SELECT COUNT(*) AS invalid_rfm_rows
FROM mart.customer_rfm
WHERE recency IS NULL OR recency < 0
   OR frequency IS NULL OR frequency < 1
   OR monetary IS NULL OR monetary < 0;


-- Phan bo nhan Decision Tree.
SELECT
    low_review,
    COUNT(*) AS row_count,
    ROUND(
        100.0 * COUNT(*) / SUM(COUNT(*)) OVER (),
        2
    ) AS percentage
FROM mart.review_training
GROUP BY low_review
ORDER BY low_review;


-- Xem thu RFM.
SELECT *
FROM mart.customer_rfm
ORDER BY monetary DESC
LIMIT 10;


-- Xem thu dashboard theo thang.
SELECT *
FROM mart.monthly_sales
ORDER BY purchase_month_start;