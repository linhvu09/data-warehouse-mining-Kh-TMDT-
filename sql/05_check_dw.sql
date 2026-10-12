-- So dong cua tung bang.
SELECT 'dim_date' AS table_name, COUNT(*) AS row_count
FROM dw.dim_date

UNION ALL
SELECT 'dim_customer', COUNT(*)
FROM dw.dim_customer

UNION ALL
SELECT 'dim_seller', COUNT(*)
FROM dw.dim_seller

UNION ALL
SELECT 'dim_product', COUNT(*)
FROM dw.dim_product

UNION ALL
SELECT 'fact_orders', COUNT(*)
FROM dw.fact_orders

UNION ALL
SELECT 'fact_order_items', COUNT(*)
FROM dw.fact_order_items

UNION ALL
SELECT 'fact_payments', COUNT(*)
FROM dw.fact_payments

UNION ALL
SELECT 'fact_reviews', COUNT(*)
FROM dw.fact_reviews

ORDER BY table_name;


-- Doi chieu tien voi staging, kiem tra rieng tung Fact.
SELECT
    'item_price' AS metric,
    (SELECT SUM(price::NUMERIC)
     FROM staging.order_items) AS staging_total,
    (SELECT SUM(price)
     FROM dw.fact_order_items) AS dw_total

UNION ALL
SELECT
    'freight_value',
    (SELECT SUM(freight_value::NUMERIC)
     FROM staging.order_items),
    (SELECT SUM(freight_value)
     FROM dw.fact_order_items)

UNION ALL
SELECT
    'payment_value',
    (SELECT SUM(payment_value::NUMERIC)
     FROM staging.order_payments),
    (SELECT SUM(payment_value)
     FROM dw.fact_payments);


-- Kiem tra ngay mua duoc anh xa dung.
SELECT COUNT(*) AS mismatched_purchase_dates
FROM dw.fact_orders AS orders
JOIN dw.dim_date AS dates
    ON orders.purchase_date_key = dates.date_key
WHERE dates.full_date IS DISTINCT FROM
      orders.order_purchase_timestamp::DATE;


-- So nguoi khach hang phan biet.
SELECT
    COUNT(*) AS customer_records,
    COUNT(DISTINCT customer_unique_id) AS unique_customers
FROM dw.dim_customer;


-- Xem thu cac dong mat hang voi thong tin san pham.
SELECT
    items.order_id,
    items.order_item_id,
    products.product_category_name_english,
    items.price,
    items.freight_value
FROM dw.fact_order_items AS items
JOIN dw.dim_product AS products
    ON items.product_key = products.product_key
ORDER BY items.order_id, items.order_item_id
LIMIT 10;