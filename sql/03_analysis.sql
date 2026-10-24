-- ============================================================
-- 物流时效分析 SQL（分层：口径层 -> 指标层 -> 归因层）
-- 库: logistics_analysis
-- 分析主线：全程时效 -> 分环节拆解 -> 慢因定位
-- ============================================================
USE logistics_analysis;

-- ============================================================
-- 0. 口径层：运单环节宽表视图
--    把轨迹事件行转成"一单一行、各环节时长一列"的分析宽表
--    所有下游分析统一从这里取数，口径只维护这一处
-- ============================================================
CREATE OR REPLACE VIEW v_waybill_stages AS
SELECT
    w.waybill_no,
    w.warehouse_id,
    wh.warehouse_name,
    w.dest_province,
    w.dest_region,
    w.weight_kg,
    w.status,
    w.created_at,
    w.signed_at,
    TIMESTAMPDIFF(HOUR, e.t_created,  e.t_audited) AS audit_h,      -- 审单时长
    TIMESTAMPDIFF(HOUR, e.t_audited,  e.t_outbound) AS outbound_h,  -- 审单->出库
    TIMESTAMPDIFF(HOUR, e.t_outbound, e.t_picked)  AS pickup_h,     -- 出库->揽收
    TIMESTAMPDIFF(HOUR, e.t_picked,   e.t_depart)  AS enter_hub_h,  -- 揽收->进始发仓
    TIMESTAMPDIFF(HOUR, e.t_depart,   e.t_arrive)  AS trunk_h,      -- 干线时长
    TIMESTAMPDIFF(HOUR, e.t_arrive,   e.t_ofd)     AS sort_h,       -- 到达仓->分拣出库
    TIMESTAMPDIFF(HOUR, e.t_ofd,      w.signed_at) AS delivery_h,   -- 派送时长
    TIMESTAMPDIFF(HOUR, e.t_created,  w.signed_at) AS total_h,      -- 全程时效
    (w.signed_at IS NOT NULL AND TIMESTAMPDIFF(HOUR, e.t_created, w.signed_at) <= 72)
                                                   AS is_on_time,   -- 72h 内送达
    DAYOFWEEK(e.t_ofd) IN (1, 7)                   AS is_weekend,  -- 派送日是否周末(派送运力口径)
    DATE_FORMAT(w.created_at, '%Y-%m')             AS order_month
FROM fact_waybills w
JOIN dim_warehouse wh USING (warehouse_id)
JOIN (
    SELECT
        waybill_no,
        MAX(CASE WHEN event_type = 'CREATED'          THEN event_time END) AS t_created,
        MAX(CASE WHEN event_type = 'AUDITED'          THEN event_time END) AS t_audited,
        MAX(CASE WHEN event_type = 'OUTBOUND'         THEN event_time END) AS t_outbound,
        MAX(CASE WHEN event_type = 'PICKED_UP'        THEN event_time END) AS t_picked,
        MAX(CASE WHEN event_type = 'DEPARTED_HUB'     THEN event_time END) AS t_depart,
        MAX(CASE WHEN event_type = 'ARRIVED_HUB'      THEN event_time END) AS t_arrive,
        MAX(CASE WHEN event_type = 'OUT_FOR_DELIVERY' THEN event_time END) AS t_ofd
    FROM fact_tracking_events
    GROUP BY waybill_no
) e USING (waybill_no)
WHERE w.status = 'SIGNED'      -- 时效口径：只分析完整履约的已签收运单
  AND w.signed_at IS NOT NULL;

-- ============================================================
-- 1. 指标层 A：整体大盘
-- ============================================================
-- 1.1 总体履约概况
SELECT
    COUNT(*)                              AS signed_orders,
    ROUND(AVG(total_h), 1)                AS avg_total_h,
    ROUND(AVG(total_h > 72) * 100, 2)     AS overtime_72h_pct,
    (SELECT ROUND(SUM(status = 'SIGNED') / COUNT(*) * 100, 2)
     FROM fact_waybills)                  AS signed_rate_pct
FROM v_waybill_stages;

-- 1.2 每日单量与平均时效趋势（看整体有没有异常日期）
SELECT
    DATE(created_at)          AS dt,
    COUNT(*)                  AS orders,
    ROUND(AVG(total_h), 1)    AS avg_total_h
FROM v_waybill_stages
GROUP BY DATE(created_at)
ORDER BY dt;

-- ============================================================
-- 2. 指标层 B：分环节拆解（全程时长由谁构成）
-- ============================================================
-- 2.1 全网各环节平均时长
SELECT
    ROUND(AVG(pickup_h),    1) AS avg_pickup_h,
    ROUND(AVG(enter_hub_h), 1) AS avg_enter_hub_h,
    ROUND(AVG(trunk_h),     1) AS avg_trunk_h,
    ROUND(AVG(sort_h),      1) AS avg_sort_h,
    ROUND(AVG(delivery_h),  1) AS avg_delivery_h
FROM v_waybill_stages;

-- 2.2 分仓 × 分环节时长（第一视角：哪个仓整体慢）
SELECT
    warehouse_name,
    COUNT(*)                    AS orders,
    ROUND(AVG(pickup_h),   1)   AS pickup_h,
    ROUND(AVG(trunk_h),    1)   AS trunk_h,
    ROUND(AVG(delivery_h), 1)   AS delivery_h,
    ROUND(AVG(total_h),    1)   AS total_h
FROM v_waybill_stages
GROUP BY warehouse_name
ORDER BY total_h DESC;

-- ============================================================
-- 3. 归因层：三个具体问题的定位
-- ============================================================
-- 3.1 【P1 揽收】分仓揽收时长分布（均值 + 80分位）
--     结论预期：华南仓揽收显著慢于其他仓
SELECT
    warehouse_name,
    COUNT(*) AS orders,
    ROUND(AVG(pickup_h), 1) AS avg_pickup_h,
    ROUND(MAX(CASE WHEN rn = CEIL(cnt * 0.8) THEN pickup_h END), 1) AS p80_pickup_h
FROM (
    SELECT warehouse_name, pickup_h,
           ROW_NUMBER() OVER (PARTITION BY warehouse_name ORDER BY pickup_h) AS rn,
           COUNT(*)     OVER (PARTITION BY warehouse_name)                   AS cnt
    FROM v_waybill_stages
) t
GROUP BY warehouse_name
ORDER BY avg_pickup_h DESC;

-- 3.2 【P2 干线】主要线路的干线时长，按月看恶化趋势
--     结论预期：华东仓->华中秋季起干线陡增
SELECT
    warehouse_name,
    dest_region,
    order_month,
    COUNT(*)                 AS orders,
    ROUND(AVG(trunk_h), 1)   AS avg_trunk_h
FROM v_waybill_stages
WHERE dest_region IN ('湖北区', '河南区', '广东区', '江苏区', '四川区')  -- 量大线路
GROUP BY warehouse_name, dest_region, order_month
HAVING orders >= 400
ORDER BY warehouse_name, dest_region, order_month;

-- 3.3 【P3 派送】周末 vs 工作日派送时长
--     结论预期：周末派送明显慢（人手不足设定）
SELECT
    is_weekend,
    COUNT(*)                    AS orders,
    ROUND(AVG(delivery_h), 1)   AS avg_delivery_h,
    ROUND(AVG(delivery_h > 12) * 100, 1) AS slow_delivery_pct
FROM v_waybill_stages
GROUP BY is_weekend;

-- 3.4 慢线路 TOP：全程超时率(>72h)排名，用于给出优化优先级
SELECT
    warehouse_name,
    dest_region,
    COUNT(*)                                AS orders,
    ROUND(AVG(total_h), 1)                  AS avg_total_h,
    ROUND(AVG(total_h > 72) * 100, 1)       AS overtime_pct
FROM v_waybill_stages
GROUP BY warehouse_name, dest_region
HAVING orders >= 300
ORDER BY overtime_pct DESC
LIMIT 10;

-- ============================================================
-- 4. 履约总览层：漏斗、健康度、瓶颈定位
-- ============================================================
-- 4.1 履约健康度 KPI（含完美订单率、次日达达成率）
--     口径注记：完美订单 = 已签收且 72h 内送达且无异常；
--              首次妥投成功率在数据中无二次投递事件，等于签收率
SELECT
    (SELECT COUNT(*) FROM fact_waybills)                                    AS all_orders,
    (SELECT COUNT(*) FROM fact_waybills WHERE status = 'EXCEPTION')         AS exception_orders,
    COUNT(*)                                                                AS signed_orders,
    ROUND(AVG(is_on_time) * 100, 2)                                         AS nextday72_pct,
    ROUND(SUM(is_on_time) / (SELECT COUNT(*) FROM fact_waybills) * 100, 2)  AS perfect_order_pct,
    ROUND((SELECT COUNT(*) FROM fact_waybills WHERE status = 'SIGNED')
          / (SELECT COUNT(*) FROM fact_waybills) * 100, 2)                  AS first_delivery_pct
FROM v_waybill_stages;

-- 4.2 履约漏斗：各环节平均耗时与累计占比（定位链条中哪一段最重）
SELECT
    '审单'   AS stage, ROUND(AVG(audit_h), 1)     AS avg_h FROM v_waybill_stages
UNION ALL SELECT '出库', ROUND(AVG(outbound_h), 1) FROM v_waybill_stages
UNION ALL SELECT '揽收', ROUND(AVG(pickup_h), 1)   FROM v_waybill_stages
UNION ALL SELECT '进始发仓', ROUND(AVG(enter_hub_h), 1) FROM v_waybill_stages
UNION ALL SELECT '干线', ROUND(AVG(trunk_h), 1)    FROM v_waybill_stages
UNION ALL SELECT '分拣', ROUND(AVG(sort_h), 1)     FROM v_waybill_stages
UNION ALL SELECT '派送', ROUND(AVG(delivery_h), 1) FROM v_waybill_stages
ORDER BY avg_h DESC;

-- 4.3 分仓漏斗瓶颈定位（哪个仓卡在哪个环节）
SELECT
    warehouse_name,
    ROUND(AVG(audit_h), 1)    AS audit_h,
    ROUND(AVG(outbound_h), 1) AS outbound_h,
    ROUND(AVG(pickup_h), 1)   AS pickup_h,
    ROUND(AVG(trunk_h), 1)    AS trunk_h,
    ROUND(AVG(delivery_h), 1) AS delivery_h
FROM v_waybill_stages
GROUP BY warehouse_name;

-- ============================================================
-- 5. 异常归因：拒收/破损/丢失 按区域与线路拆解
-- ============================================================
-- 5.1 异常类型分布与趋势
SELECT
    exception_type,
    COUNT(*) AS orders,
    ROUND(COUNT(*) / (SELECT COUNT(*) FROM fact_waybills
                      WHERE status = 'EXCEPTION') * 100, 1) AS pct
FROM fact_waybills
WHERE status = 'EXCEPTION'
GROUP BY exception_type
ORDER BY orders DESC;

-- 5.2 异常按发货仓 x 目的区域 拆解（异常率 = 异常单/总单）
SELECT
    wh.warehouse_name,
    w.dest_region,
    COUNT(*)                                          AS total_orders,
    SUM(w.status = 'EXCEPTION')                       AS exception_orders,
    ROUND(SUM(w.status = 'EXCEPTION') / COUNT(*) * 100, 2) AS exception_pct
FROM fact_waybills w
JOIN dim_warehouse wh USING (warehouse_id)
GROUP BY wh.warehouse_name, w.dest_region
HAVING total_orders >= 300
ORDER BY exception_pct DESC
LIMIT 10;
