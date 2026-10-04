"""Query corpora shared by the SQL gate and scoping tests."""

# Queries a good analyst (or model) would write. None of these may be rejected.
VALID = [
    "SELECT COUNT(*) AS n FROM order_items",
    "SELECT p.brand, ROUND(SUM(oi.sale_price), 2) AS revenue FROM order_items oi "
    "JOIN products p ON p.id = oi.product_id GROUP BY p.brand ORDER BY revenue DESC",
    "SELECT FORMAT_TIMESTAMP('%Y-%m', created_at) AS month, SUM(sale_price) AS revenue "
    "FROM order_items WHERE status NOT IN ('Cancelled', 'Returned') GROUP BY month ORDER BY month",
    "SELECT u.state, COUNT(DISTINCT u.id) AS customers, AVG(oi.sale_price) AS avg_price "
    "FROM users u JOIN order_items oi ON oi.user_id = u.id GROUP BY u.state ORDER BY avg_price",
    "WITH spend AS (SELECT user_id, SUM(sale_price) AS total FROM order_items GROUP BY user_id) "
    "SELECT user_id, total, RANK() OVER (ORDER BY total DESC) AS rnk FROM spend "
    "ORDER BY total DESC LIMIT 10",
    "WITH a AS (SELECT user_id FROM orders), b AS (SELECT user_id FROM a) SELECT COUNT(*) FROM b",
    "SELECT * FROM users LIMIT 3",
    "SELECT u FROM users u LIMIT 1",
    "SELECT * EXCEPT (id) FROM users LIMIT 1",
    "SELECT * FROM (orders o JOIN users u ON o.user_id = u.id) LIMIT 2",
    "SELECT * FROM `bigquery-public-data.thelook_ecommerce.products` LIMIT 2",
    "SELECT * FROM `bigquery-public-data`.thelook_ecommerce.orders LIMIT 2",
    "SELECT * FROM thelook_ecommerce.order_items LIMIT 2",
    "SELECT department, SAFE_DIVIDE(SUM(retail_price - cost), SUM(retail_price)) AS margin "
    "FROM products GROUP BY department",
    "SELECT EXTRACT(YEAR FROM created_at) AS y, COUNT(*) AS n FROM orders GROUP BY y ORDER BY y",
    "SELECT brand FROM products UNION DISTINCT SELECT category FROM products",
    "SELECT o.order_id, (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id = o.order_id) "
    "AS n FROM orders o LIMIT 3",
    "SELECT DATE_TRUNC(DATE(created_at), MONTH) AS m, COUNT(*) AS c FROM orders "
    "GROUP BY m ORDER BY m DESC LIMIT 3",
    "SELECT n FROM UNNEST(GENERATE_ARRAY(1, 3)) AS n",
    "SELECT category, COUNTIF(status = 'Returned') / COUNT(*) AS return_rate FROM order_items oi "
    "JOIN products p ON p.id = oi.product_id GROUP BY category HAVING COUNT(*) > 5",
    "SELECT CASE WHEN u.age < 30 THEN 'under 30' ELSE '30+' END AS age_band, COUNT(*) AS n "
    "FROM users u GROUP BY age_band",
    "-- monthly orders\nSELECT COUNT(*) FROM orders /* all statuses */",
    "SELECT * FROM products PIVOT(SUM(cost) FOR department IN ('Men', 'Women'))",
    "(SELECT id FROM products) UNION ALL (SELECT id FROM products)",
]

# (query, expected rejection code). Every one of these must be rejected.
REJECTED = [
    # writes and scripting
    ("DELETE FROM users WHERE TRUE", "not_select"),
    ("INSERT INTO users (id) VALUES (1)", "not_select"),
    ("UPDATE users SET age = 1 WHERE TRUE", "not_select"),
    ("CREATE TABLE x AS SELECT * FROM users", "not_select"),
    ("DROP TABLE users", "not_select"),
    ("TRUNCATE TABLE users", "not_select"),
    ("MERGE users t USING users s ON t.id = s.id WHEN MATCHED THEN DELETE", "not_select"),
    ("CALL some_proc()", "not_select"),
    ("EXPORT DATA OPTIONS(uri='gs://b/x') AS SELECT * FROM users", "not_select"),
    ("EXECUTE IMMEDIATE 'SELECT 1'", "not_select"),
    ("WITH x AS (DELETE FROM users WHERE TRUE RETURNING *) SELECT * FROM x", "not_select"),
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("SELECT 1; DROP TABLE users", "multiple_statements"),
    ("BEGIN SELECT 1; END", "multiple_statements"),
    ("DECLARE x INT64; SELECT 1", "multiple_statements"),
    ("CREATE TEMP FUNCTION f() AS (1); SELECT f()", "multiple_statements"),
    ("", "empty"),
    ("-- nothing here", "empty"),
    ("SELEC 1", "syntax"),
    # tables outside the allow-list, however they are reached
    ("SELECT * FROM events", "unknown_table"),
    ("SELECT * FROM inventory_items", "unknown_table"),
    ("SELECT * FROM distribution_centers", "unknown_table"),
    ("SELECT * FROM `bigquery-public-data`.thelook_ecommerce.events", "unknown_table"),
    ("SELECT * FROM `bigquery-public-data.other_dataset.users`", "unknown_table"),
    ("SELECT * FROM `other-project.thelook_ecommerce.users`", "unknown_table"),
    ("SELECT * FROM other_dataset.users", "unknown_table"),
    ("SELECT * FROM `bigquery-public-data.thelook_ecommerce.*`", "unknown_table"),
    ("SELECT * FROM INFORMATION_SCHEMA.TABLES", "unknown_table"),
    ("SELECT * FROM thelook_ecommerce.INFORMATION_SCHEMA.COLUMNS", "unknown_table"),
    (
        "SELECT * FROM `bigquery-public-data.thelook_ecommerce.INFORMATION_SCHEMA.TABLES`",
        "unknown_table",
    ),
    ("SELECT * FROM `region-us`.INFORMATION_SCHEMA.JOBS", "unknown_table"),
    ("SELECT (SELECT COUNT(*) FROM events) AS n FROM users", "unknown_table"),
    ("SELECT * FROM users WHERE id IN (SELECT user_id FROM events)", "unknown_table"),
    # a WITH name used to disguise a real table
    (
        "SELECT * FROM events WHERE 1 IN (WITH events AS (SELECT 1 AS x) SELECT x FROM events)",
        "unknown_table",
    ),
    (
        "WITH a AS (SELECT * FROM events), events AS (SELECT 1 AS x) SELECT * FROM a",
        "unknown_table",
    ),
    ("WITH c AS (SELECT 1 AS x) SELECT * FROM c AS a, events AS a", "unknown_table"),
    (
        "SELECT * FROM (SELECT * FROM (WITH z AS (SELECT 1) SELECT * FROM z)) JOIN z ON TRUE",
        "unknown_table",
    ),
    ("WITH t AS (SELECT 1 AS x) SELECT * FROM other_dataset.t", "unknown_table"),
    ("(WITH z AS (SELECT 1 AS x) SELECT x FROM z) UNION ALL SELECT x FROM z", "unknown_table"),
    ("SELECT * FROM orders o JOIN o.events e ON TRUE", "unknown_table"),
    ("SELECT * FROM orders o JOIN o.users u ON TRUE", "unknown_table"),
    # a WITH name used to replace a table the scoping relies on
    (
        "WITH products AS (SELECT n AS id, 'Brightwave' AS brand "
        "FROM UNNEST(GENERATE_ARRAY(1, 9)) n) SELECT COUNT(*) FROM order_items",
        "with_shadows_table",
    ),
    ("WITH users AS (SELECT 1 AS id) SELECT * FROM users", "with_shadows_table"),
    (
        "SELECT * FROM orders WHERE 1 IN (WITH order_items AS (SELECT 1 AS x) SELECT x "
        "FROM order_items)",
        "with_shadows_table",
    ),
    # table functions and functions that reach outside the gate
    ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')", "forbidden_function"),
    ("SELECT * FROM ML.PREDICT(MODEL `p.d.m`, TABLE users)", "table_function"),
    ("SELECT * FROM ML.PREDICT(MODEL `p.d.m`, (SELECT * FROM users))", "table_function"),
    ("SELECT * FROM VECTOR_SEARCH(TABLE users, 'c', (SELECT 1))", "table_function"),
    ("SELECT * FROM my_dataset.my_tvf(1)", "table_function"),
    ("SELECT SESSION_USER()", "forbidden_function"),
    ("SELECT CURRENT_USER()", "forbidden_function"),
    ("SELECT AI.GENERATE('hi')", "forbidden_function"),
    ("SELECT ML.DISTANCE([1.0], [2.0])", "forbidden_function"),
    ("SELECT bqutil.fn.int(1)", "forbidden_function"),
    ("SELECT my_dataset.my_udf(id) FROM users", "forbidden_function"),
    ("SELECT `my-project.my_dataset.my_udf`(id) FROM users", "forbidden_function"),
    ("SELECT KEYS.NEW_KEYSET('AEAD_AES_GCM_256')", "forbidden_function"),
    # table clauses that cannot be scoped
    (
        "SELECT * FROM users FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), "
        "INTERVAL 1 HOUR)",
        "unsupported_table_clause",
    ),
    ("SELECT * FROM users TABLESAMPLE SYSTEM (10 PERCENT)", "unsupported_table_clause"),
    # personal data, however it is reached
    ("SELECT email FROM users", "pii_column"),
    ("SELECT EMAIL FROM users", "pii_column"),
    ("SELECT `email` FROM users", "pii_column"),
    ("SELECT u.email AS contact FROM users u", "pii_column"),
    ("SELECT users.first_name FROM users", "pii_column"),
    ("SELECT CONCAT(u.first_name, ' ', u.last_name) AS n FROM users AS u", "pii_column"),
    ("SELECT x.email FROM (SELECT * FROM users) x", "pii_column"),
    ("WITH c AS (SELECT * FROM users) SELECT street_address FROM c", "pii_column"),
    ("SELECT id FROM users WHERE email LIKE 'a%'", "pii_column"),
    ("SELECT id FROM users ORDER BY last_name", "pii_column"),
    ("SELECT postal_code, COUNT(*) FROM users GROUP BY postal_code", "pii_column"),
    ("SELECT latitude, longitude FROM users", "pii_column"),
    ("SELECT MAX(email) FROM users", "pii_column"),
    (
        "SELECT o.order_id FROM orders o JOIN users u ON u.email = CAST(o.user_id AS STRING)",
        "pii_column",
    ),
]
