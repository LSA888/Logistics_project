-- ============================================================
-- 物流时效分析项目 - 建库建表脚本
-- MySQL 8.0 / 库名: logistics_analysis
-- ============================================================
DROP DATABASE IF EXISTS logistics_analysis;
CREATE DATABASE logistics_analysis DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE logistics_analysis;

-- 仓库维度表
CREATE TABLE dim_warehouse (
    warehouse_id    VARCHAR(10)  NOT NULL COMMENT '仓库ID',
    warehouse_name  VARCHAR(50)  NOT NULL COMMENT '仓库名称',
    city            VARCHAR(50)  NOT NULL COMMENT '所在城市',
    PRIMARY KEY (warehouse_id)
) COMMENT '仓库维度表';

-- 运单事实表（一票运单一行）
CREATE TABLE fact_waybills (
    waybill_no      VARCHAR(20)  NOT NULL COMMENT '运单号',
    created_at      DATETIME     NOT NULL COMMENT '下单时间',
    warehouse_id    VARCHAR(10)  NOT NULL COMMENT '发货仓ID',
    dest_province   VARCHAR(30)  NOT NULL COMMENT '目的省份',
    dest_region     VARCHAR(30)  NOT NULL COMMENT '目的大区',
    weight_kg       DECIMAL(6,2) NOT NULL COMMENT '重量(kg)',
    status          VARCHAR(20)  NOT NULL COMMENT '状态: SIGNED/IN_TRANSIT/EXCEPTION',
    exception_type  VARCHAR(20)  NULL     COMMENT '异常类型: 拒收/破损/丢失(仅异常单)',
    signed_at       DATETIME     NULL     COMMENT '签收时间(未签收为NULL)',
    PRIMARY KEY (waybill_no),
    KEY idx_wh_created (warehouse_id, created_at),
    KEY idx_status (status)
) COMMENT '运单事实表';

-- 轨迹事件表（一个环节一行）
CREATE TABLE fact_tracking_events (
    event_id        INT          NOT NULL COMMENT '事件ID',
    waybill_no      VARCHAR(20)  NOT NULL COMMENT '运单号',
    event_type      VARCHAR(30)  NOT NULL COMMENT '环节: CREATED/PICKED_UP/DEPARTED_HUB/ARRIVED_HUB/OUT_FOR_DELIVERY/SIGNED',
    event_time      DATETIME     NOT NULL COMMENT '事件时间',
    PRIMARY KEY (event_id),
    KEY idx_wb (waybill_no),
    KEY idx_type_time (event_type, event_time)
) COMMENT '运单轨迹事件表';
