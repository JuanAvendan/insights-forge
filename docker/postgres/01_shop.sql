-- Sample shop database for local testing (loaded from data/sample/sales.csv).

CREATE TABLE sales (
    order_id       text,
    order_date     date,
    region         text,
    channel        text,
    product        text,
    category       text,
    unit_price     numeric(10, 2),
    quantity       integer,
    discount       numeric(4, 2),
    total          numeric(12, 2),
    customer_email text
);

COPY sales FROM '/seed/sales.csv' WITH (FORMAT csv, HEADER true);

CREATE VIEW monthly_sales AS
SELECT date_trunc('month', order_date)::date AS month,
       region,
       count(*)   AS orders,
       sum(total) AS revenue
FROM sales
GROUP BY 1, 2;

-- Least privilege: the account used by insights-forge can only read.
CREATE ROLE analyst LOGIN PASSWORD 'analyst';
GRANT CONNECT ON DATABASE shop TO analyst;
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;
