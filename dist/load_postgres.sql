-- Run from the bundle root:  psql -d <db> -f load_postgres.sql
\set ON_ERROR_STOP on
BEGIN;
\i schema.sql
\copy categories FROM 'seed/categories.csv' WITH (FORMAT csv, HEADER true)
\copy products FROM 'seed/products.csv' WITH (FORMAT csv, HEADER true)
\copy product_images FROM 'seed/product_images.csv' WITH (FORMAT csv, HEADER true)
\copy customers FROM 'seed/customers.csv' WITH (FORMAT csv, HEADER true)
\copy orders FROM 'seed/orders.csv' WITH (FORMAT csv, HEADER true)
\copy order_items FROM 'seed/order_items.csv' WITH (FORMAT csv, HEADER true)
SELECT setval(pg_get_serial_sequence('customers', 'customer_id'), (SELECT max(customer_id) FROM customers));
SELECT setval(pg_get_serial_sequence('orders', 'order_id'), (SELECT max(order_id) FROM orders));
COMMIT;
ANALYZE;
