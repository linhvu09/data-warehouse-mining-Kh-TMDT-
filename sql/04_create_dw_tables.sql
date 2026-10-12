BEGIN;

CREATE SCHEMA IF NOT EXISTS dw;

CREATE TABLE IF NOT EXISTS dw.dim_date (
    date_key       INTEGER PRIMARY KEY,
    full_date      DATE UNIQUE,
    year_number    INTEGER,
    quarter_number INTEGER,
    month_number   INTEGER,
    day_number     INTEGER,
    weekday_number INTEGER,
    is_weekend     BOOLEAN
);

CREATE TABLE IF NOT EXISTS dw.dim_customer (
    customer_key             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id              VARCHAR(32) NOT NULL UNIQUE,
    customer_unique_id       VARCHAR(32) NOT NULL,
    customer_zip_code_prefix VARCHAR(5) NOT NULL,
    customer_city            TEXT NOT NULL,
    customer_state           VARCHAR(2) NOT NULL
);

CREATE TABLE IF NOT EXISTS dw.dim_seller (
    seller_key             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seller_id              VARCHAR(32) NOT NULL UNIQUE,
    seller_zip_code_prefix VARCHAR(5) NOT NULL,
    seller_city            TEXT NOT NULL,
    seller_state           VARCHAR(2) NOT NULL
);

CREATE TABLE IF NOT EXISTS dw.dim_product (
    product_key                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id                   VARCHAR(32) NOT NULL UNIQUE,
    product_category_name        TEXT NOT NULL,
    product_category_name_english TEXT NOT NULL,
    product_name_length          INTEGER,
    product_description_length   INTEGER,
    product_photos_qty           INTEGER,
    product_weight_g             INTEGER,
    product_length_cm            INTEGER,
    product_height_cm            INTEGER,
    product_width_cm             INTEGER,
    category_missing             BOOLEAN NOT NULL,
    category_translation_missing BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dw.fact_orders (
    order_id                       VARCHAR(32) PRIMARY KEY,
    customer_key                   BIGINT NOT NULL
        REFERENCES dw.dim_customer(customer_key),
    purchase_date_key              INTEGER NOT NULL
        REFERENCES dw.dim_date(date_key),
    order_status                   TEXT NOT NULL,
    order_purchase_timestamp       TIMESTAMP NOT NULL,
    order_approved_at              TIMESTAMP,
    order_delivered_carrier_date    TIMESTAMP,
    order_delivered_customer_date   TIMESTAMP,
    order_estimated_delivery_date   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS dw.fact_order_items (
    order_id            VARCHAR(32) NOT NULL
        REFERENCES dw.fact_orders(order_id),
    order_item_id       INTEGER NOT NULL,
    customer_key        BIGINT NOT NULL
        REFERENCES dw.dim_customer(customer_key),
    purchase_date_key   INTEGER NOT NULL
        REFERENCES dw.dim_date(date_key),
    product_key         BIGINT NOT NULL
        REFERENCES dw.dim_product(product_key),
    seller_key          BIGINT NOT NULL
        REFERENCES dw.dim_seller(seller_key),
    shipping_limit_date TIMESTAMP NOT NULL,
    price               NUMERIC(18, 2) NOT NULL CHECK (price >= 0),
    freight_value       NUMERIC(18, 2) NOT NULL CHECK (freight_value >= 0),

    PRIMARY KEY (order_id, order_item_id)
);

CREATE TABLE IF NOT EXISTS dw.fact_payments (
    order_id             VARCHAR(32) NOT NULL
        REFERENCES dw.fact_orders(order_id),
    payment_sequential   INTEGER NOT NULL,
    customer_key         BIGINT NOT NULL
        REFERENCES dw.dim_customer(customer_key),
    purchase_date_key    INTEGER NOT NULL
        REFERENCES dw.dim_date(date_key),
    payment_type         TEXT NOT NULL,
    payment_installments INTEGER NOT NULL CHECK (payment_installments >= 0),
    payment_value        NUMERIC(18, 2) NOT NULL CHECK (payment_value >= 0),

    PRIMARY KEY (order_id, payment_sequential)
);

CREATE TABLE IF NOT EXISTS dw.fact_reviews (
    review_id                VARCHAR(32) NOT NULL,
    order_id                 VARCHAR(32) NOT NULL
        REFERENCES dw.fact_orders(order_id),
    customer_key             BIGINT NOT NULL
        REFERENCES dw.dim_customer(customer_key),
    purchase_date_key        INTEGER NOT NULL
        REFERENCES dw.dim_date(date_key),
    review_score             INTEGER NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title     TEXT,
    review_comment_message   TEXT,
    review_creation_date     TIMESTAMP NOT NULL,
    review_answer_timestamp  TIMESTAMP NOT NULL,

    PRIMARY KEY (review_id, order_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_unique_id
    ON dw.dim_customer(customer_unique_id);

CREATE INDEX IF NOT EXISTS idx_orders_customer
    ON dw.fact_orders(customer_key);

CREATE INDEX IF NOT EXISTS idx_orders_purchase_date
    ON dw.fact_orders(purchase_date_key);

CREATE INDEX IF NOT EXISTS idx_items_product
    ON dw.fact_order_items(product_key);

CREATE INDEX IF NOT EXISTS idx_items_seller
    ON dw.fact_order_items(seller_key);

COMMIT;