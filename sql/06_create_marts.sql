BEGIN;

CREATE SCHEMA IF NOT EXISTS mart;


-- 1. MOT DONG MOI DON HANG

CREATE OR REPLACE VIEW mart.order_summary AS
WITH item_totals AS (
    SELECT
        items.order_id,
        COUNT(*) AS item_count,
        COUNT(DISTINCT items.product_key) AS distinct_product_count,
        COUNT(DISTINCT items.seller_key) AS seller_count,
        COUNT(DISTINCT products.product_category_name) AS category_count,
        SUM(items.price) AS item_sales_value,
        SUM(items.freight_value) AS freight_value,
        MIN(items.price) AS minimum_item_price,
        MAX(items.price) AS maximum_item_price,
        AVG(products.product_weight_g) AS average_product_weight_g
    FROM dw.fact_order_items AS items
    JOIN dw.dim_product AS products
        ON items.product_key = products.product_key
    GROUP BY items.order_id
),
payment_totals AS (
    SELECT
        order_id,
        COUNT(*) AS payment_count,
        SUM(payment_value) AS payment_value,
        MAX(payment_installments) AS max_payment_installments
    FROM dw.fact_payments
    GROUP BY order_id
),
ranked_reviews AS (
    SELECT
        order_id,
        review_id,
        review_score,
        review_creation_date,
        review_answer_timestamp,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY
                review_answer_timestamp DESC,
                review_creation_date DESC,
                review_id ASC
        ) AS review_rank
    FROM dw.fact_reviews
)
SELECT
    orders.order_id,
    customers.customer_unique_id,
    customers.customer_city,
    customers.customer_state,

    orders.order_status,
    orders.order_purchase_timestamp,
    orders.order_approved_at,
    orders.order_delivered_carrier_date,
    orders.order_delivered_customer_date,
    orders.order_estimated_delivery_date,

    dates.full_date AS purchase_date,
    dates.year_number AS purchase_year,
    dates.month_number AS purchase_month,
    dates.weekday_number AS purchase_weekday,

    EXTRACT(HOUR FROM orders.order_purchase_timestamp)::INTEGER
        AS purchase_hour,

    COALESCE(items.item_count, 0) AS item_count,
    COALESCE(items.distinct_product_count, 0)
        AS distinct_product_count,
    COALESCE(items.seller_count, 0) AS seller_count,
    COALESCE(items.category_count, 0) AS category_count,

    items.item_sales_value,
    items.freight_value,
    items.minimum_item_price,
    items.maximum_item_price,
    items.average_product_weight_g,

    COALESCE(payments.payment_count, 0) AS payment_count,
    payments.payment_value,
    payments.max_payment_installments,

    reviews.review_score,
    reviews.review_creation_date,
    reviews.review_answer_timestamp,

    CASE
        WHEN orders.order_delivered_customer_date IS NULL THEN NULL
        ELSE
            orders.order_delivered_customer_date::DATE
            > orders.order_estimated_delivery_date::DATE
    END AS is_late_delivery,

    CASE
        WHEN orders.order_delivered_customer_date IS NULL THEN NULL
        ELSE
            orders.order_delivered_customer_date::DATE
            - orders.order_purchase_timestamp::DATE
    END AS delivery_days

FROM dw.fact_orders AS orders
JOIN dw.dim_customer AS customers
    ON orders.customer_key = customers.customer_key
JOIN dw.dim_date AS dates
    ON orders.purchase_date_key = dates.date_key
LEFT JOIN item_totals AS items
    ON orders.order_id = items.order_id
LEFT JOIN payment_totals AS payments
    ON orders.order_id = payments.order_id
LEFT JOIN ranked_reviews AS reviews
    ON orders.order_id = reviews.order_id
   AND reviews.review_rank = 1;


-- 2. BAN HANG THEO THANG MUA

CREATE OR REPLACE VIEW mart.monthly_sales AS
SELECT
    DATE_TRUNC('month', order_purchase_timestamp)::DATE
        AS purchase_month_start,

    COUNT(*) AS delivered_order_count,
    COUNT(DISTINCT customer_unique_id) AS unique_customer_count,
    SUM(item_count) AS sold_item_count,
    SUM(item_sales_value) AS item_sales_value,
    SUM(freight_value) AS freight_value,
    SUM(payment_value) AS payment_value,
    AVG(item_sales_value) AS average_order_item_value,

    COUNT(*) FILTER (
        WHERE is_late_delivery = TRUE
    ) AS late_order_count,

    COUNT(*) FILTER (
        WHERE is_late_delivery IS NOT NULL
    ) AS delivery_observed_count,

    AVG(review_score) AS average_review_score

FROM mart.order_summary
WHERE order_status = 'delivered'
GROUP BY DATE_TRUNC('month', order_purchase_timestamp)::DATE;


-- 3. BAN HANG THEO DANH MUC

CREATE OR REPLACE VIEW mart.category_sales AS
SELECT
    products.product_category_name,
    products.product_category_name_english,
    COUNT(*) AS sold_item_count,
    COUNT(DISTINCT items.order_id) AS order_count,
    COUNT(DISTINCT customers.customer_unique_id)
        AS unique_customer_count,
    SUM(items.price) AS item_sales_value,
    SUM(items.freight_value) AS freight_value

FROM dw.fact_order_items AS items
JOIN dw.fact_orders AS orders
    ON items.order_id = orders.order_id
JOIN dw.dim_product AS products
    ON items.product_key = products.product_key
JOIN dw.dim_customer AS customers
    ON items.customer_key = customers.customer_key

WHERE orders.order_status = 'delivered'

GROUP BY
    products.product_category_name,
    products.product_category_name_english;


-- 4. RFM: MOT DONG MOI NGUOI KHACH HANG

CREATE OR REPLACE VIEW mart.customer_rfm AS
WITH eligible_orders AS (
    SELECT *
    FROM mart.order_summary
    WHERE order_status = 'delivered'
      AND item_count > 0
      AND item_sales_value IS NOT NULL
),
reference AS (
    SELECT
        MAX(purchase_date) + 1 AS reference_date
    FROM eligible_orders
)
SELECT
    orders.customer_unique_id,
    reference.reference_date,

    MAX(orders.purchase_date) AS last_purchase_date,

    reference.reference_date - MAX(orders.purchase_date)
        AS recency,

    COUNT(*) AS frequency,

    SUM(orders.item_sales_value) AS monetary

FROM eligible_orders AS orders
CROSS JOIN reference

GROUP BY
    orders.customer_unique_id,
    reference.reference_date;


-- 5. DU LIEU DECISION TREE

CREATE OR REPLACE VIEW mart.review_training AS
SELECT
    -- Cot dinh danh va thoi gian de chia tap, khong dung lam feature.
    order_id,
    customer_unique_id,
    order_purchase_timestamp,
    order_approved_at,

    -- Cac feature du kien.
    customer_state,
    purchase_month,
    purchase_weekday,
    purchase_hour,

    item_count,
    distinct_product_count,
    seller_count,
    category_count,

    item_sales_value,
    freight_value,
    minimum_item_price,
    maximum_item_price,
    average_product_weight_g,

    payment_count,
    payment_value,
    max_payment_installments,

    -- Cot nhan va diem goc de doi chieu.
    review_score,

    CASE
        WHEN review_score <= 2 THEN 1
        ELSE 0
    END AS low_review

FROM mart.order_summary

WHERE order_status = 'delivered'
  AND review_score IS NOT NULL
  AND order_approved_at IS NOT NULL
  AND review_answer_timestamp >= order_approved_at
  AND item_count > 0
  AND payment_count > 0;


COMMIT;


SELECT table_name
FROM information_schema.views
WHERE table_schema = 'mart'
ORDER BY table_name;