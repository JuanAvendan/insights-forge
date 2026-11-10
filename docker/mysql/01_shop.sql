-- Sample shop database for local testing (loaded from data/sample/sales.csv).

USE shop;

CREATE TABLE sales (
    order_id       VARCHAR(20),
    order_date     DATE,
    region         VARCHAR(20),
    channel        VARCHAR(20),
    product        VARCHAR(50),
    category       VARCHAR(50),
    unit_price     DECIMAL(10, 2),
    quantity       INT,
    discount       DECIMAL(4, 2),
    total          DECIMAL(12, 2),
    customer_email VARCHAR(100)
);

-- Empty CSV fields become NULL; the trailing TRIM handles CRLF line endings.
LOAD DATA INFILE '/seed/sales.csv'
INTO TABLE sales
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(order_id, order_date, @region, channel, product, category,
 unit_price, quantity, discount, total, @email)
SET region         = NULLIF(@region, ''),
    customer_email = NULLIF(TRIM(TRAILING '\r' FROM @email), '');

CREATE VIEW monthly_sales AS
SELECT CAST(DATE_FORMAT(order_date, '%Y-%m-01') AS DATE) AS month,
       region,
       COUNT(*)   AS orders,
       SUM(total) AS revenue
FROM sales
GROUP BY 1, 2;

-- Least privilege: the account used by insights-forge can only read.
CREATE USER 'analyst'@'%' IDENTIFIED BY 'analyst';
GRANT SELECT, SHOW VIEW ON shop.* TO 'analyst'@'%';
