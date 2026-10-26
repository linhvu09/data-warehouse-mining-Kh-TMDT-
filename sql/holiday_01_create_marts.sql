BEGIN;

-- Kiểm tra dữ liệu trước khi tạo Mart.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM mart.order_summary
    ) THEN
        RAISE EXCEPTION 'order_summary đang rỗng';
    END IF;

    IF EXISTS (
        SELECT order_id
        FROM mart.order_summary
        GROUP BY order_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'order_summary có order_id trùng';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mart.order_summary
        WHERE purchase_date IS NULL
           OR order_purchase_timestamp IS NULL
           OR purchase_date <>
               order_purchase_timestamp::date
    ) THEN
        RAISE EXCEPTION
            'Ngày đặt hàng thiếu hoặc không khớp timestamp';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mart.order_summary AS orders
        LEFT JOIN external_data.br_calendar AS calendar
            ON orders.purchase_date = calendar.calendar_date
        WHERE calendar.calendar_date IS NULL
    ) THEN
        RAISE EXCEPTION
            'Có ngày đặt hàng chưa được lịch API bao phủ';
    END IF;
END
$$;

-- Một dòng mỗi ngày trong khoảng quan sát của Olist.
CREATE OR REPLACE VIEW mart.holiday_daily_sales AS
WITH observation_bounds AS (
    SELECT
        min(purchase_date) AS start_date,
        max(purchase_date) AS end_date
    FROM mart.order_summary
),
daily_orders AS (
    SELECT
        purchase_date,

        count(*) AS all_order_count,

        count(*) FILTER (
            WHERE order_status = 'delivered'
        ) AS delivered_order_count,

        coalesce(
            sum(item_sales_value), 0
        ) AS all_item_sales_value,

        coalesce(
            sum(item_sales_value) FILTER (
                WHERE order_status = 'delivered'
            ), 0
        ) AS delivered_item_sales_value,

        coalesce(
            sum(payment_value) FILTER (
                WHERE order_status = 'delivered'
            ), 0
        ) AS delivered_payment_value

    FROM mart.order_summary
    GROUP BY purchase_date
)
SELECT
    calendar.calendar_date AS purchase_date,
    calendar.year_number,
    calendar.month_number,
    calendar.day_of_week,
    calendar.is_weekend,
    calendar.is_national_public_holiday,
    calendar.national_public_holiday_names,

    CASE
        WHEN calendar.is_national_public_holiday
            THEN 'Ngày lễ được chọn'
        ELSE 'Ngày không thuộc nhóm lễ đã chọn'
    END AS day_type,

    coalesce(
        orders.all_order_count, 0
    ) AS all_order_count,

    coalesce(
        orders.delivered_order_count, 0
    ) AS delivered_order_count,

    coalesce(
        orders.all_item_sales_value, 0
    ) AS all_item_sales_value,

    coalesce(
        orders.delivered_item_sales_value, 0
    ) AS delivered_item_sales_value,

    coalesce(
        orders.delivered_payment_value, 0
    ) AS delivered_payment_value,

    calendar.source_silver_run_id

FROM external_data.br_calendar AS calendar
CROSS JOIN observation_bounds AS bounds
LEFT JOIN daily_orders AS orders
    ON calendar.calendar_date = orders.purchase_date

WHERE calendar.calendar_date
    BETWEEN bounds.start_date AND bounds.end_date;


-- So sánh theo năm, nhóm ngày và cuối tuần.
CREATE OR REPLACE VIEW mart.holiday_comparison AS
SELECT
    year_number,
    day_type,
    is_national_public_holiday,
    is_weekend,

    count(*) AS observed_day_count,

    sum(all_order_count) AS all_order_count,
    sum(delivered_order_count) AS delivered_order_count,

    sum(
        delivered_item_sales_value
    ) AS delivered_item_sales_value,

    round(
        sum(all_order_count)::numeric / count(*),
        2
    ) AS average_orders_per_day,

    round(
        sum(delivered_item_sales_value) / count(*),
        2
    ) AS average_delivered_item_sales_per_day,

    round(
        sum(delivered_item_sales_value)
        / nullif(sum(delivered_order_count), 0),
        2
    ) AS average_delivered_order_item_value

FROM mart.holiday_daily_sales

GROUP BY
    year_number,
    day_type,
    is_national_public_holiday,
    is_weekend;

COMMIT;