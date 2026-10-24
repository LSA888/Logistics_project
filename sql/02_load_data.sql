-- ============================================================
-- 数据导入脚本：CSV -> MySQL（LOAD DATA LOCAL INFILE）
-- 执行方式: mysql -uroot -p --local-infile=1 < 02_load_data.sql
-- 前置: SET GLOBAL local_infile = ON;
-- ============================================================
USE logistics_analysis;
SET GLOBAL local_infile = ON;

LOAD DATA LOCAL INFILE '../data/clean/dim_warehouse.csv'
INTO TABLE dim_warehouse
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 ROWS
(warehouse_id, warehouse_name, city);

LOAD DATA LOCAL INFILE '../data/clean/fact_waybills.csv'
INTO TABLE fact_waybills
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 ROWS
(waybill_no, @created_at, warehouse_id, dest_province, dest_region, @weight_kg, status, @exception_type, @signed_at)
SET created_at = STR_TO_DATE(@created_at, '%Y-%m-%d %H:%i:%s'),
    weight_kg  = NULLIF(@weight_kg, ''),
    exception_type = NULLIF(@exception_type, ''),
    signed_at  = NULLIF(@signed_at, '');

LOAD DATA LOCAL INFILE '../data/clean/fact_tracking_events.csv'
INTO TABLE fact_tracking_events
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"' LINES TERMINATED BY '\n' IGNORE 1 ROWS
(event_id, waybill_no, event_type, @event_time)
SET event_time = STR_TO_DATE(@event_time, '%Y-%m-%d %H:%i:%s');

SELECT 'dim_warehouse' AS tbl, COUNT(*) AS rows_cnt FROM dim_warehouse
UNION ALL SELECT 'fact_waybills', COUNT(*) FROM fact_waybills
UNION ALL SELECT 'fact_tracking_events', COUNT(*) FROM fact_tracking_events;
