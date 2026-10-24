# -*- coding: utf-8 -*-
"""
物流时效分析看板（面试作品集）
数据源: MySQL logistics_analysis 库（口径视图 v_waybill_stages）
        DB 不可用时自动降级为 CSV 内存复算（同一口径）
运行: streamlit run app.py
"""
import io
import os
import re

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------- 基础配置 ----------------
DB = dict(host="127.0.0.1", port=3306, user="root",
          password="root", database="logistics_analysis", charset="utf8mb4")

CSV_DIR = "../data/clean"    # DB 不可用时的降级数据源（清洗后数据，与 v_waybill_stages 口径一致）
RAW_DIR = "../data/output"   # 清洗前原始数据（数据质量页展示用）

st.set_page_config(page_title="物流时效分析", page_icon="📦", layout="wide")

# ---------------- 物流风视觉主题 ----------------
st.markdown("""<style>
/* 顶部：去掉默认白条 */
[data-testid="stHeader"] { background: transparent; }

/* 侧边栏：货运深蓝渐变 */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #17407F 0%, #0C2148 100%);
}
[data-testid="stSidebar"] * { color: #E6ECF8 !important; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding: 3px 8px; border-radius: 8px; }
[data-testid="stSidebar"] [data-testid="stRadio"] label:hover {
    background: rgba(255,255,255,.08);
}

/* 侧边栏品牌头 */
.brand-row { display: flex; align-items: center; gap: 9px; margin-top: 4px; }
.brand-ico { font-size: 1.7rem; }
.brand-name { font-size: 1.45rem; font-weight: 800; letter-spacing: 1px; }
.brand-sub { color: #A9BCE0 !important; font-size: .84rem; margin: 6px 0 10px 2px; }
.tape { height: 7px; border-radius: 4px; margin: 10px 0 16px;
        background: repeating-linear-gradient(45deg, #E8802E 0 12px, #0C2148 12px 24px);
        border: 1px solid rgba(255,255,255,.28); }
.src-tag { color: #A9BCE0 !important; font-size: .85rem; margin-top: 6px; }

/* 主区标题：藏青标题 + 橙色侧条 */
h1 { color: #13356F; border-bottom: 3px solid #E8802E; padding-bottom: 8px; }
h2 { color: #13356F; border-left: 5px solid #E8802E; padding-left: 10px; }
h3 { color: #1F2A3D; }

/* KPI 指标卡：面单卡片风 */
[data-testid="stMetric"] {
    background: #FFFFFF; border: 1px solid #E7E2D6; border-left: 5px solid #E8802E;
    border-radius: 10px; padding: 12px 14px;
    box-shadow: 0 1px 4px rgba(19, 53, 111, .08);
}
[data-testid="stMetricValue"] { color: #13356F; font-weight: 800; }
[data-testid="stMetricLabel"] p { color: #6B7280; font-weight: 600; }
</style>""", unsafe_allow_html=True)

# 中文市场的涨跌配色习惯不涉及涨跌，这里用统一业务配色
C_MAIN = "#2E6FE8"      # 主色
C_WARN = "#E8802E"      # 警示（慢）
C_OK = "#3CA878"        # 正常
C_STACK = ["#8FB6F5", "#5E93EF", "#2E6FE8", "#1D4FA8", "#12336E"]


@st.cache_data(ttl=600)
def q(sql: str) -> pd.DataFrame:
    import pymysql
    conn = pymysql.connect(**DB)
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


# ---------------- 口径核心：事件行 -> 一单一行的环节宽表（与 SQL 视图同一口径） ----------------
def stages_from_tables(wb: pd.DataFrame, ev: pd.DataFrame, wh: pd.DataFrame) -> pd.DataFrame:
    """复现 v_waybill_stages：仅已签收，每个环节时长一列"""
    p = ev.pivot_table(index="waybill_no", columns="event_type",
                       values="event_time", aggfunc="first").reset_index()
    m = wb.merge(p, on="waybill_no").merge(wh, on="warehouse_id")
    m = m[(m.status == "SIGNED") & m.signed_at.notna()].copy()
    m["pickup_h"] = ((m.PICKED_UP - m.OUTBOUND).dt.total_seconds() / 3600).apply(np.floor)
    m["audit_h"] = ((m.AUDITED - m.created_at).dt.total_seconds() / 3600).apply(np.floor)
    m["outbound_h"] = ((m.OUTBOUND - m.AUDITED).dt.total_seconds() / 3600).apply(np.floor)
    m["enter_hub_h"] = ((m.DEPARTED_HUB - m.PICKED_UP).dt.total_seconds() / 3600).apply(np.floor)
    m["trunk_h"] = ((m.ARRIVED_HUB - m.DEPARTED_HUB).dt.total_seconds() / 3600).apply(np.floor)
    m["sort_h"] = ((m.OUT_FOR_DELIVERY - m.ARRIVED_HUB).dt.total_seconds() / 3600).apply(np.floor)
    m["delivery_h"] = ((m.signed_at - m.OUT_FOR_DELIVERY).dt.total_seconds() / 3600).apply(np.floor)
    m["total_h"] = ((m.signed_at - m.created_at).dt.total_seconds() / 3600).apply(np.floor)
    m["is_on_time"] = m.total_h <= 72
    m["is_weekend"] = m.OUT_FOR_DELIVERY.dt.weekday >= 5          # 周末口径：派送日
    m["order_month"] = m.created_at.dt.strftime("%Y-%m")
    return m


@st.cache_data(ttl=600, show_spinner=False)
def csv_stages() -> pd.DataFrame:
    """内置清洗后 CSV -> 环节宽表"""
    base = os.path.join(os.path.dirname(__file__), CSV_DIR)
    wb = pd.read_csv(f"{base}/fact_waybills.csv", parse_dates=["created_at", "signed_at"])
    ev = pd.read_csv(f"{base}/fact_tracking_events.csv", parse_dates=["event_time"])
    wh = pd.read_csv(f"{base}/dim_warehouse.csv")
    return stages_from_tables(wb, ev, wh)


def csv_aggregates() -> dict:
    s_all = csv_stages()
    base = os.path.join(os.path.dirname(__file__), CSV_DIR)
    wb_all = pd.read_csv(f"{base}/fact_waybills.csv", parse_dates=["created_at", "signed_at"])
    wh = pd.read_csv(f"{base}/dim_warehouse.csv")
    return aggregates_from_tables(s_all, wb_all, wh)


def aggregates_from_tables(s_all: pd.DataFrame, wb_all: pd.DataFrame, wh: pd.DataFrame) -> dict:
    """基于环节宽表 + 全量运单 + 仓库维度 计算看板全部聚合（内置数据与上传数据共用）"""
    d = {}
    n_all = len(wb_all)
    d["kpi"] = pd.DataFrame([{
        "signed_orders": len(s_all), "avg_total_h": round(s_all.total_h.mean(), 1),
        "overtime_pct": round((s_all.total_h > 72).mean() * 100, 2),
        "signed_rate": round(len(s_all) / max(n_all, 1) * 100, 2)}])
    d["daily"] = (s_all.groupby(s_all.created_at.dt.date)
                    .agg(orders=("waybill_no", "count"), avg_total_h=("total_h", "mean"))
                    .reset_index().rename(columns={"created_at": "dt"}))
    d["daily"]["avg_total_h"] = d["daily"].avg_total_h.round(1)
    d["stages"] = pd.DataFrame([{
        "audit": round(s_all.audit_h.mean(), 1),
        "outbound": round(s_all.outbound_h.mean(), 1),
        "pickup": round(s_all.pickup_h.mean(), 1), "enter_hub": round(s_all.enter_hub_h.mean(), 1),
        "trunk": round(s_all.trunk_h.mean(), 1), "sort": round(s_all.sort_h.mean(), 1),
        "delivery": round(s_all.delivery_h.mean(), 1)}])
    d["wh"] = (s_all.groupby("warehouse_name")
                 .agg(单量=("waybill_no", "count"), 审单=("audit_h", "mean"),
                      出库=("outbound_h", "mean"), 揽收=("pickup_h", "mean"),
                      干线=("trunk_h", "mean"), 派送=("delivery_h", "mean"),
                      全程=("total_h", "mean")).round(1).reset_index()
                 .rename(columns={"warehouse_name": "仓"})
                 .sort_values("全程", ascending=False))
    # 履约健康度 + 异常（需要全部运单，不含仅签收子集）
    exc = wb_all
    if "exception_type" not in exc.columns:
        exc = exc.assign(exception_type="未知")
    d["health"] = pd.DataFrame([{
        "all_orders": len(exc),
        "exception_orders": int((exc.status == "EXCEPTION").sum()),
        "nextday72_pct": round(s_all.is_on_time.mean() * 100, 2),
        "perfect_order_pct": round(s_all.is_on_time.sum() / len(exc) * 100, 2),
        "first_delivery_pct": round((exc.status == "SIGNED").mean() * 100, 2)}])
    e = exc[exc.status == "EXCEPTION"]
    d["exc_type"] = (e.groupby("exception_type").size().reset_index(name="单量")
                     .rename(columns={"exception_type": "异常类型"}))
    d["exc_type"]["占比"] = (d["exc_type"].单量 / d["exc_type"].单量.sum() * 100).round(1)
    er = (exc.groupby(["warehouse_id", "dest_region"])
            .agg(总单量=("waybill_no", "count"),
                 异常单=("status", lambda s: (s == "EXCEPTION").sum())).reset_index())
    er["异常率"] = (er.异常单 / er.总单量 * 100).round(2)
    d["exc_route"] = (er[er.总单量 >= 300].merge(wh, on="warehouse_id")
                      .sort_values("异常率", ascending=False).head(10)
                      .rename(columns={"warehouse_name": "仓", "dest_region": "目的区域"}))
    pk = s_all.sort_values("pickup_h").groupby("warehouse_name")
    d["pickup"] = pd.DataFrame({
        "仓": pk["warehouse_name"].first(), "单量": pk.size(),
        "平均揽收时长": pk["pickup_h"].mean().round(1),
        "揽收80分位": pk["pickup_h"].quantile(0.8).round(1)}).reset_index(drop=True) \
        .sort_values("平均揽收时长", ascending=False)
    t = s_all[s_all.dest_region.isin(["河南区", "湖北区"])]
    if t.empty:   # 上传数据无该区域时，退化为单量最大的两个目的区域
        top_rg = s_all["dest_region"].value_counts().head(2).index.tolist()
        t = s_all[s_all.dest_region.isin(top_rg)]
    t = t.groupby(["warehouse_name", "dest_region", "order_month"])["trunk_h"] \
         .agg(干线时长=lambda s: round(s.mean(), 1), n="count").reset_index()
    d["trunk"] = t[t.n >= 300].rename(columns={
        "warehouse_name": "仓", "dest_region": "区域", "order_month": "月份"})[["仓", "区域", "月份", "干线时长"]]
    d["weekend"] = pd.DataFrame({
        "时段": ["工作日", "周末"], "单量": s_all.groupby("is_weekend").size().values,
        "平均派送时长": s_all.groupby("is_weekend")["delivery_h"].mean().round(1).values,
        "慢单占比": (s_all.assign(slow=s_all.delivery_h > 12)
                     .groupby("is_weekend")["slow"].mean().mul(100).round(1).values)})
    sl = (s_all.groupby(["warehouse_name", "dest_region"])
            .agg(单量=("waybill_no", "count"), 平均全程时长=("total_h", "mean"),
                 超时率=("total_h", lambda x: (x > 72).mean() * 100)).round(1).reset_index())
    sl = sl[sl.单量 >= 300].sort_values("超时率", ascending=False).head(10)
    d["slow"] = sl.rename(columns={"warehouse_name": "仓", "dest_region": "目的区域"})
    return d


# ---------------- 清洗（与 data/clean_data.py 同一套规则，参数化供上传数据复用） ----------------
CHAIN_ORDER = ["CREATED", "AUDITED", "OUTBOUND", "PICKED_UP", "DEPARTED_HUB",
               "ARRIVED_HUB", "OUT_FOR_DELIVERY", "SIGNED"]


@st.cache_data(show_spinner=False)
def clean_tables(wb: pd.DataFrame, ev: pd.DataFrame):
    """五条规则清洗，返回 (清洗后运单, 清洗后事件, 规则报告, 前后汇总)"""
    wb = wb.copy()
    ev = ev.copy()
    wb["created_at"] = pd.to_datetime(wb["created_at"], errors="coerce")
    wb["signed_at"] = pd.to_datetime(wb["signed_at"], errors="coerce")
    ev["event_time"] = pd.to_datetime(ev["event_time"], errors="coerce")
    report = []

    def log(rule, desc, action, n_wb, n_ev):
        report.append({"规则": rule, "问题": desc, "处理动作": action,
                       "涉及运单": n_wb, "涉及事件": n_ev})

    n_wb_raw, n_ev_raw = len(wb), len(ev)

    # R1 重复运单：保留首条
    dup = wb["waybill_no"].duplicated(keep="first")
    log("R1 重复运单", "同一运单号存在多条记录", "保留首条，剔除重复",
        int(dup.sum()), int(ev["waybill_no"].isin(wb.loc[dup, "waybill_no"]).sum()))
    wb = wb[~dup]

    # R2 签收早于下单
    bad = wb["signed_at"].notna() & (wb["signed_at"] <= wb["created_at"])
    log("R2 签收早于下单", "signed_at <= created_at，时间戳不可信", "剔除整单",
        int(bad.sum()), int(ev["waybill_no"].isin(wb.loc[bad, "waybill_no"]).sum()))
    wb = wb[~bad]

    # R3 重量异常
    bad = ~wb["weight_kg"].between(0.1, 100)
    log("R3 重量异常", "重量超出 [0.1, 100] kg 合理区间", "剔除整单",
        int(bad.sum()), int(ev["waybill_no"].isin(wb.loc[bad, "waybill_no"]).sum()))
    wb = wb[~bad]

    # R4 轨迹时间倒挂（按业务链路顺序检查单调性）
    ev = ev[ev["waybill_no"].isin(wb["waybill_no"])]
    rank = {et: i for i, et in enumerate(CHAIN_ORDER)}
    ev = ev[ev["event_type"].isin(CHAIN_ORDER)].copy()
    ev["ord"] = ev["event_type"].map(rank)
    bad_nos = set()
    for no, sub in ev.groupby("waybill_no"):
        t = sub.sort_values("ord")["event_time"].to_numpy()
        if np.any(np.diff(t) < np.timedelta64(0, "s")):
            bad_nos.add(no)
    log("R4 轨迹时间倒挂", "事件时间戳与业务链路顺序不一致，无法还原真实顺序", "剔除整单",
        len(bad_nos), int(ev["waybill_no"].isin(bad_nos).sum()))
    wb = wb[~wb["waybill_no"].isin(bad_nos)]

    # R5 已签收单缺关键事件（注意 notna().all()：pandas 的 all() 会跳过 NaN）
    ev = ev[ev["waybill_no"].isin(wb["waybill_no"])]
    need = ["AUDITED", "OUTBOUND", "PICKED_UP", "DEPARTED_HUB", "ARRIVED_HUB", "OUT_FOR_DELIVERY"]
    have = (ev[ev["event_type"].isin(need)]
            .assign(v=1).pivot_table(index="waybill_no", columns="event_type",
                                     values="v", aggfunc="first"))
    full = have.notna().all(axis=1)
    signed = wb.loc[wb["status"] == "SIGNED", "waybill_no"]
    missing = signed[~signed.isin(full[full].index)]
    log("R5 缺失关键事件", "已签收运单缺少计算环节时长所必需的中间事件", "剔除整单",
        len(missing), int(ev["waybill_no"].isin(missing).sum()))
    wb = wb[~wb["waybill_no"].isin(missing)]

    ev = ev[ev["waybill_no"].isin(wb["waybill_no"])].drop(columns="ord", errors="ignore")

    summary = pd.DataFrame([
        {"指标": "运单数", "清洗前": f"{n_wb_raw:,}", "清洗后": f"{len(wb):,}",
         "剔除": f"{n_wb_raw - len(wb):,}（{(n_wb_raw - len(wb)) / max(n_wb_raw, 1) * 100:.2f}%）"},
        {"指标": "轨迹事件", "清洗前": f"{n_ev_raw:,}", "清洗后": f"{len(ev):,}",
         "剔除": f"{n_ev_raw - len(ev):,}（{(n_ev_raw - len(ev)) / max(n_ev_raw, 1) * 100:.2f}%）"}])
    return wb.reset_index(drop=True), ev.reset_index(drop=True), pd.DataFrame(report), summary


def _build_dim(wb: pd.DataFrame, dim: pd.DataFrame | None) -> pd.DataFrame:
    """仓库维度：缺省用内置维度，并补齐上传数据中出现但维度缺失的仓（避免 merge 丢行）"""
    if dim is None:
        dim = pd.read_csv(os.path.join(os.path.dirname(__file__), CSV_DIR, "dim_warehouse.csv"))
    ids = set(wb["warehouse_id"].dropna().unique()) - set(dim["warehouse_id"])
    if ids:
        dim = pd.concat([dim, pd.DataFrame({"warehouse_id": list(ids),
                                            "warehouse_name": list(ids), "city": ""})],
                        ignore_index=True)
    return dim


@st.cache_data(show_spinner=False)
def builtin_raw():
    """内置原始数据（data/output，含约 3% 脏数据），供"体验完整流程"使用"""
    base = os.path.join(os.path.dirname(__file__), RAW_DIR)
    wb = pd.read_csv(f"{base}/fact_waybills.csv")
    ev = pd.read_csv(f"{base}/fact_tracking_events.csv")
    dim = pd.read_csv(f"{base}/dim_warehouse.csv")
    return wb, ev, dim


@st.cache_data(show_spinner=False)
def agg_from_custom(wb: pd.DataFrame, ev: pd.DataFrame, wh: pd.DataFrame) -> dict:
    """上传数据（清洗后）-> 看板聚合，与内置数据同一套口径"""
    s_all = stages_from_tables(wb, ev, wh)
    return aggregates_from_tables(s_all, wb, wh)


def load_data():
    """优先级：用户上传（清洗后）> MySQL > 内置 CSV 降级"""
    custom = st.session_state.get("custom_raw")
    if custom:
        d = agg_from_custom(custom["wb"], custom["ev"], custom["wh"])
        st.session_state["src"] = f"上传数据（清洗后 {len(custom['wb']):,} 单）"
        return d
    try:
        d = _load_from_db()
        st.session_state["src"] = "MySQL 数据库"
    except Exception:
        d = csv_aggregates()
        st.session_state["src"] = "内置数据文件"
    return d


def _load_from_db() -> dict:
    d = {}
    d["kpi"] = q("""
        SELECT COUNT(*) signed_orders, ROUND(AVG(total_h),1) avg_total_h,
               ROUND(AVG(total_h>72)*100,2) overtime_pct,
               (SELECT ROUND(SUM(status='SIGNED')/COUNT(*)*100,2)
                  FROM fact_waybills) signed_rate
        FROM v_waybill_stages""")
    d["daily"] = q("""
        SELECT DATE(created_at) dt, COUNT(*) orders, ROUND(AVG(total_h),1) avg_total_h
        FROM v_waybill_stages GROUP BY dt ORDER BY dt""")
    d["stages"] = q("""
        SELECT ROUND(AVG(audit_h),1) audit, ROUND(AVG(outbound_h),1) outbound,
               ROUND(AVG(pickup_h),1) pickup, ROUND(AVG(enter_hub_h),1) enter_hub,
               ROUND(AVG(trunk_h),1) trunk, ROUND(AVG(sort_h),1) sort,
               ROUND(AVG(delivery_h),1) delivery FROM v_waybill_stages""")
    d["wh"] = q("""
        SELECT warehouse_name 仓, COUNT(*) 单量,
               ROUND(AVG(audit_h),1) 审单, ROUND(AVG(outbound_h),1) 出库,
               ROUND(AVG(pickup_h),1) 揽收, ROUND(AVG(trunk_h),1) 干线,
               ROUND(AVG(delivery_h),1) 派送, ROUND(AVG(total_h),1) 全程
        FROM v_waybill_stages GROUP BY warehouse_name ORDER BY 全程 DESC""")
    d["health"] = q("""
        SELECT (SELECT COUNT(*) FROM fact_waybills) all_orders,
               (SELECT COUNT(*) FROM fact_waybills WHERE status='EXCEPTION') exception_orders,
               ROUND(AVG(is_on_time)*100,2) nextday72_pct,
               ROUND(SUM(is_on_time)/(SELECT COUNT(*) FROM fact_waybills)*100,2) perfect_order_pct,
               (SELECT ROUND(SUM(status='SIGNED')/COUNT(*)*100,2) FROM fact_waybills) first_delivery_pct
        FROM v_waybill_stages""")
    d["exc_type"] = q("""
        SELECT exception_type 异常类型, COUNT(*) 单量,
               ROUND(COUNT(*)/(SELECT COUNT(*) FROM fact_waybills WHERE status='EXCEPTION')*100,1) 占比
        FROM fact_waybills WHERE status='EXCEPTION'
        GROUP BY exception_type ORDER BY 单量 DESC""")
    d["exc_route"] = q("""
        SELECT warehouse_name 仓, dest_region 目的区域, COUNT(*) 总单量,
               SUM(status='EXCEPTION') 异常单,
               ROUND(SUM(status='EXCEPTION')/COUNT(*)*100,2) 异常率
        FROM fact_waybills w JOIN dim_warehouse wh USING (warehouse_id)
        GROUP BY warehouse_name, dest_region HAVING 总单量>=300
        ORDER BY 异常率 DESC LIMIT 10""")
    d["pickup"] = q("""
        SELECT warehouse_name 仓, COUNT(*) 单量,
               ROUND(AVG(pickup_h),1) 平均揽收时长,
               ROUND(MAX(CASE WHEN rn=CEIL(cnt*0.8) THEN pickup_h END),1) 揽收80分位
        FROM (SELECT warehouse_name, pickup_h,
                     ROW_NUMBER() OVER (PARTITION BY warehouse_name ORDER BY pickup_h) rn,
                     COUNT(*) OVER (PARTITION BY warehouse_name) cnt
              FROM v_waybill_stages) t
        GROUP BY warehouse_name ORDER BY 平均揽收时长 DESC""")
    d["trunk"] = q("""
        SELECT warehouse_name 仓, dest_region 区域, order_month 月份,
               ROUND(AVG(trunk_h),1) 干线时长
        FROM v_waybill_stages
        WHERE dest_region IN ('河南区','湖北区')
        GROUP BY warehouse_name, dest_region, order_month
        HAVING COUNT(*)>=300
        ORDER BY dest_region, 月份""")
    d["weekend"] = q("""
        SELECT CASE WHEN is_weekend=1 THEN '周末' ELSE '工作日' END 时段,
               COUNT(*) 单量, ROUND(AVG(delivery_h),1) 平均派送时长,
               ROUND(AVG(delivery_h>12)*100,1) 慢单占比
        FROM v_waybill_stages GROUP BY is_weekend""")
    d["slow"] = q("""
        SELECT warehouse_name 仓, dest_region 目的区域, COUNT(*) 单量,
               ROUND(AVG(total_h),1) 平均全程时长, ROUND(AVG(total_h>72)*100,1) 超时率
        FROM v_waybill_stages GROUP BY 仓, 目的区域 HAVING 单量>=300
        ORDER BY 超时率 DESC LIMIT 10""")
    return d


# ---------------- 页面 ----------------
def page_overview(d):
    st.header("📦 时效大盘")
    k = d["kpi"].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已签收运单", f"{int(k.signed_orders):,} 单")
    c2.metric("平均全程时效", f"{k.avg_total_h} 小时")
    c3.metric("72小时超时率", f"{k.overtime_pct} %")
    c4.metric("整体签收率", f"{k.signed_rate} %")

    st.subheader("每日单量与平均时效")
    # --- 筛选器 1：时间粒度（全量数据分桶聚合）---
    win = st.radio("时间粒度", ["按日", "按周", "按两周", "按月"],
                   horizontal=True, label_visibility="collapsed")
    unit_name = {"按日": "日", "按周": "周", "按两周": "两周", "按月": "月"}[win]

    # --- 筛选器 2：显示指标（单选/双选，隐藏另一个序列）---
    metrics = st.multiselect("显示指标", ["单量", "平均时效"],
                             default=["单量", "平均时效"], placeholder="至少选一项")

    daily = d["daily"].copy()
    daily["dt"] = pd.to_datetime(daily["dt"])
    daily = daily.sort_values("dt").reset_index(drop=True)

    def make_bucket(b, label):
        """一个时间桶：单量累加、时效取均值、横轴锚定在桶末端日期"""
        return {"label": label, "orders": int(b["orders"].sum()),
                "avg_total_h": round(b["avg_total_h"].mean(), 1),
                "days": len(b), "end": b["dt"].iloc[-1]}

    if win == "按月":
        # 自然月分桶（首月/尾月可能不满整月，桶内天数如实呈现）
        buckets = []
        for (y, m), b in daily.groupby([daily["dt"].dt.year, daily["dt"].dt.month],
                                       sort=True):
            buckets.append(make_bucket(b, f"{y}年{m}月"))
    elif win in ("按周", "按两周"):
        # 固定 7/14 天一桶，从数据首日起分桶，尾桶不足时如实标注
        size = 7 if win == "按周" else 14
        buckets = []
        for start in range(0, len(daily), size):
            b = daily.iloc[start:start + size]
            s, e = b["dt"].iloc[0], b["dt"].iloc[-1]
            label = (f"{s.month}月{s.day}日-{e.month}月{e.day}日"
                     if len(b) > 1 else f"{s.month}月{s.day}日")
            buckets.append(make_bucket(b, label))
    else:   # 按日
        buckets = [{"label": f"{t.year}年{t.month}月{t.day}日",
                    "orders": int(r.orders), "avg_total_h": float(r.avg_total_h),
                    "days": 1, "end": t}
                   for t, r in zip(daily["dt"], daily.itertuples(index=False))]

    agg = pd.DataFrame(buckets)
    x_vals, x_cn = agg["end"], agg["label"]
    bar_y, line_y = agg["orders"], agg["avg_total_h"]
    days_list = agg["days"].tolist()

    fig = go.Figure()
    if not metrics:
        st.info("请在「显示指标」中至少选择一项")
    if "单量" in metrics:
        fig.add_bar(x=x_vals, y=bar_y, name="单量",
                    marker_color=C_STACK[2], opacity=0.85,
                    customdata=list(zip(x_cn, [f"（{n}天合计）" if n > 1 else "" for n in days_list])),
                    hovertemplate="%{customdata[0]}<br>单量：%{y:,}%{customdata[1]}<extra></extra>")
    if "平均时效" in metrics:
        # 仅显示时效时用主坐标轴；两者都显示时时效挂右轴
        yax = "y2" if "单量" in metrics else "y"
        avg_note = f"（{unit_name}均值）" if unit_name != "日" else ""
        fig.add_scatter(x=x_vals, y=line_y, name="平均全程时效（小时）",
                        mode="lines+markers" if unit_name != "日" else "lines",
                        line=dict(color=C_WARN, width=2), yaxis=yax,
                        customdata=[c + avg_note for c in x_cn],
                        hovertemplate="%{customdata}<br>平均时效：%{y} 小时<extra></extra>")
        if unit_name == "日":
            # 7 日移动平均：抹平周末周期毛刺，看长期趋势（锯齿主因是周末派送慢）
            ma7 = daily["avg_total_h"].rolling(7, min_periods=3).mean().round(1)
            fig.add_scatter(x=daily["dt"], y=ma7, name="7日移动平均",
                            mode="lines", yaxis=yax,
                            line=dict(color="#8A5A2B", width=2, dash="dash"),
                            hovertemplate="%{x|%Y年%-m月%-d日}<br>7日均值：%{y} 小时<extra></extra>")
    if unit_name != "日":
        # 桶粒度：横轴刻度直接显示桶区间/月份
        fig.update_xaxes(tickmode="array", tickvals=list(x_vals),
                         ticktext=list(agg["label"]))
    else:
        # 日粒度：日期刻度用中文，按当前长度自适应抽稀
        step = max(len(daily) // 8, 1)
        tick_vals = list(daily["dt"].iloc[::step])
        fig.update_xaxes(tickvals=tick_vals,
                         ticktext=[f"{t.month}月{t.day}日" for t in tick_vals])
    # 双轴：只有两个指标同时显示时才需要；单指标时单坐标轴
    both = "单量" in metrics and "平均时效" in metrics
    fig.update_layout(
        yaxis=dict(title="单量" if both or "单量" in metrics else "小时"),
        yaxis2=dict(overlaying="y", side="right", title="小时", visible=both),
        height=520, margin=dict(l=0, r=0, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, xanchor="left"))
    st.plotly_chart(fig, use_container_width=True)
    if win == "按日":
        st.caption("💡 按日锯齿为周末效应：周末派送运力不足，时效高约 4 小时（7 天周期），"
                   "详见「慢因定位 · 发现 3」；趋势请看 7 日移动平均虚线，或切换到周/两周/月粒度。")

    st.subheader("全程时效的环节构成（平均小时）")
    s = d["stages"].iloc[0]
    stages_df = pd.DataFrame({"环节": ["审单", "出库", "揽收", "揽收→始发仓", "干线", "分拣", "派送"],
                              "时长": [s.audit, s.outbound, s.pickup, s.enter_hub, s.trunk, s["sort"], s.delivery]})
    fig2 = go.Figure(go.Bar(x=stages_df.时长[::-1], y=stages_df.环节[::-1],
                            orientation="h", marker_color=C_STACK[::-1],
                            text=[f"{v} 小时" for v in stages_df.时长[::-1]],
                            textposition="outside", cliponaxis=False,
                            textfont=dict(size=13, color="#333333")))
    fig2.update_xaxes(range=[0, stages_df.时长.max() * 1.18])
    fig2.update_layout(height=280, margin=dict(t=20, b=0))
    st.plotly_chart(fig2, use_container_width=True)


def page_warehouse(d):
    st.header("🏭 分仓对比")
    st.subheader("各仓分环节平均时长（小时）")
    melted = d["wh"].melt(id_vars=["仓", "单量"],
                          value_vars=["审单", "出库", "揽收", "干线", "派送"],
                          var_name="环节", value_name="时长（小时）")
    fig = px.bar(melted, x="仓", y="时长（小时）", color="环节", barmode="group",
                 color_discrete_sequence=[C_STACK[4], C_STACK[2], C_STACK[0], "#B9CCF0", C_WARN],
                 text="时长（小时）")
    fig.update_layout(height=380, margin=dict(t=20, b=0))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("各仓全程时效与单量")
    c1, c2 = st.columns([3, 2])
    with c1:
        st.dataframe(d["wh"], use_container_width=True, hide_index=True)
    with c2:
        fig2 = px.pie(d["wh"], names="仓", values="单量", hole=0.45,
                      color_discrete_sequence=C_STACK)
        fig2.update_layout(height=300, margin=dict(t=20, b=0))
        st.plotly_chart(fig2, use_container_width=True)


def page_problems(d):
    st.header("🔍 慢因定位")
    st.caption("三个可复现的结论：揽收慢在华南仓、干线恶化在华东→华中、周末派送运力不足")

    st.subheader("发现 1 · 揽收环节：华南仓显著偏慢")
    p = d["pickup"]
    c1, c2 = st.columns([2, 3])
    with c1:
        st.dataframe(p, use_container_width=True, hide_index=True)
    with c2:
        fig = go.Figure()
        fig.add_bar(x=p["仓"], y=p["平均揽收时长"], name="均值",
                    marker_color=[C_WARN if i == 0 else C_STACK[2] for i in range(len(p))],
                    text=[f"{v} 小时" for v in p["平均揽收时长"]], textposition="outside")
        fig.add_scatter(x=p["仓"], y=p["揽收80分位"], name="80分位", mode="markers",
                        marker=dict(color=C_OK, size=12, symbol="diamond"))
        fig.update_layout(height=340, margin=dict(t=20, b=0), yaxis_title="小时")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("发现 2 · 干线环节：华东仓→华中 9 月起恶化")
    t = d["trunk"]
    fig = px.line(t, x="月份", y="干线时长", color="仓", facet_col="区域",
                  markers=True, color_discrete_sequence=[C_WARN, C_STACK[2], C_OK])
    # 月份轴中文化（"2026-09" 会被 Plotly 按英文日期渲染）
    months = sorted(t["月份"].astype(str).unique())
    fig.update_xaxes(tickvals=months,
                     ticktext=[f"{m[:4]}年{int(m[5:])}月" for m in months])
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))  # 分面标题去掉字段名前缀
    fig.update_layout(height=360, margin=dict(t=20, b=0), yaxis_title="小时")
    st.plotly_chart(fig, use_container_width=True)
    st.info("同期华中仓发往同区域干线稳定在 12 小时 —— 排除目的地侧因素，锁定始发侧干线中转问题。")

    st.subheader("发现 3 · 派送环节：周末运力不足")
    w = d["weekend"]
    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(w, x="时段", y="平均派送时长", color="时段",
                     color_discrete_map={"工作日": C_STACK[2], "周末": C_WARN},
                     text="平均派送时长")
        fig.update_layout(height=320, margin=dict(t=20, b=0), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(w, x="时段", y="慢单占比", color="时段",
                      color_discrete_map={"工作日": C_STACK[2], "周末": C_WARN},
                      text="慢单占比")
        fig2.update_yaxes(title=">12h 慢单占比 %")
        fig2.update_layout(height=320, margin=dict(t=20, b=0), showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)


def page_slow(d):
    st.header("🐌 慢线路前十（按 72 小时超时率）")
    s = d["slow"]
    s["线路"] = s["仓"] + " → " + s["目的区域"]
    fig = px.bar(s, x="超时率", y="线路", orientation="h",
                 text=s["超时率"].map(lambda v: f"{v}%"))
    fig.update_traces(marker_color=[C_WARN if i < 5 else C_STACK[0] for i in range(len(s))])
    fig.update_layout(height=460, margin=dict(t=20, b=0), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(s[["线路", "单量", "平均全程时长", "超时率"]],
                 use_container_width=True, hide_index=True)


def page_fulfillment(d):
    st.header("🚚 履约总览")
    h = d["health"].iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("完美订单率", f"{h.perfect_order_pct} %",
              help="已签收 且 72 小时内送达 且 无异常（分母为全部运单）")
    c2.metric("72小时送达率", f"{h.nextday72_pct} %", help="签收单中 72 小时内送达占比")
    c3.metric("首次妥投率", f"{h.first_delivery_pct} %",
              help="数据中无二次投递事件，口径等于签收率")
    c4.metric("异常订单", f"{int(h.exception_orders):,} 单",
              delta=f"-{round(h.exception_orders / h.all_orders * 100, 2)}%", delta_color="off")

    st.subheader("履约漏斗 · 各环节平均耗时")
    s = d["stages"].iloc[0]
    funnel = pd.DataFrame({
        "环节": ["① 审单", "② 出库", "③ 揽收", "④ 进始发仓", "⑤ 干线", "⑥ 分拣", "⑦ 派送"],
        "平均耗时（小时）": [s.audit, s.outbound, s.pickup, s.enter_hub, s.trunk, s["sort"], s.delivery]})
    fig = px.bar(funnel, x="环节", y="平均耗时（小时）", color="平均耗时（小时）",
                 color_continuous_scale=["#8FB6F5", "#E8802E"], text="平均耗时（小时）")
    fig.update_layout(height=340, margin=dict(t=20, b=0), coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("分仓瓶颈定位（各环节平均小时）")
    st.caption("颜色越深 = 该仓该环节平均耗时越长，深色格子即瓶颈（仓 × 环节矩阵）")
    w = d["wh"]
    pivot = w.set_index("仓")[["审单", "出库", "揽收", "干线", "派送"]]
    fig2 = px.imshow(pivot, text_auto=".1f", color_continuous_scale="Blues",
                     aspect="auto", labels=dict(color="平均时长（小时）"))
    fig2.update_layout(height=280, margin=dict(t=20, b=0))
    st.plotly_chart(fig2, use_container_width=True)
    heat = pivot.reset_index().melt(id_vars="仓", var_name="环节", value_name="时长（小时）")
    slowest = heat.loc[heat.groupby("仓")["时长（小时）"].idxmax()]
    st.info("瓶颈速读：" + "；".join(
        f"{r['仓']}最重环节为「{r['环节']}」({r['时长（小时）']} 小时)" for _, r in slowest.iterrows()))


def page_exception(d):
    st.header("⚠️ 异常归因")
    st.caption("异常单 = 状态 EXCEPTION（拒收 / 破损 / 丢失），按类型与线路拆解")
    c1, c2 = st.columns([2, 3])
    with c1:
        fig = px.pie(d["exc_type"], names="异常类型", values="单量", hole=0.45,
                     color_discrete_sequence=[C_WARN, C_STACK[0], C_STACK[2]])
        fig.update_layout(height=320, margin=dict(t=20, b=0))
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        fig2 = px.bar(d["exc_type"], x="异常类型", y="单量", color="异常类型",
                      color_discrete_sequence=[C_WARN, C_STACK[0], C_STACK[2]],
                      text=d["exc_type"]["占比"].map(lambda v: f"{v}%"))
        fig2.update_layout(height=320, margin=dict(t=20, b=0), showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("异常率前十线路（发货仓 → 目的区域）")
    er = d["exc_route"]
    er["线路"] = er["仓"] + " → " + er["目的区域"]
    fig3 = px.bar(er, x="异常率", y="线路", orientation="h",
                  text=er["异常率"].map(lambda v: f"{v}%"),
                  hover_data={"总单量": True, "异常单": True})
    fig3.update_traces(marker_color=[C_WARN if i < 5 else C_STACK[0] for i in range(len(er))])
    fig3.update_layout(height=420, margin=dict(t=20, b=0), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig3, use_container_width=True)


@st.cache_data(ttl=600)
def load_quality():
    """清洗报告（CSV 小文件，两个数据源下都直接读项目内文件）"""
    base = os.path.dirname(__file__)
    rep = pd.read_csv(os.path.join(base, "../data/clean/cleaning_report.csv"))
    summ = pd.read_csv(os.path.join(base, "../data/clean/cleaning_summary.csv"))
    return rep, summ


def page_quality(d):
    st.header("🧹 数据质量")
    st.caption("数据分析流程第 2 步：原始数据含约 3% 脏数据，按 5 条规则清洗，只清洗不修复、全程留痕")
    rep, summ = load_quality()

    st.subheader("清洗前后对比")
    st.table(summ.set_index("指标"))

    st.subheader("清洗规则与命中明细")
    st.table(rep.set_index("规则"))

    st.subheader("清洗口径说明")
    st.markdown(
        "- **只清洗，不修复**：时间倒挂、缺失事件无法从数据还原真实值，修复等于编造数据，统一剔除并留痕\n"
        "- **规则顺序**：先去重（R1），再做整单校验（R2-R4），最后按状态校验事件完整性（R5）\n"
        "- **检出自检**：脏数据由数据层随机注入（生成器留有标准答案标签），"
        "五类脏数据检出率均为 100%（\"缺失关键事件\" 中未检出的部分为在途/异常单，"
        "其停在中途属正常业务状态，不视为脏数据）\n"
        "- 下游所有分析（MySQL、看板、结论报告）均基于清洗后数据")
    st.info("为什么看板要有这一页：清洗决定分析口径的可信度。"
            "同样的慢因分析，清洗前后结论可能不同——这一页让评审能看到\"分析用的数据是怎么来的\"。")


def page_datahub(d):
    st.header("📥 数据入口")
    st.caption("流程：上传数据 → 预览 → 清洗（规则留痕）→ 同步到可视化报表；不上传时看板使用内置数据")

    # ---- ① 上传数据 ----
    st.subheader("① 上传数据")
    c1, c2 = st.columns(2)
    with c1:
        wb_file = st.file_uploader("运单表 fact_waybills.csv", type="csv")
    with c2:
        ev_file = st.file_uploader("轨迹事件表 fact_tracking_events.csv", type="csv")
    dim_file = st.file_uploader("仓库维度表 dim_warehouse.csv（可选，缺省用内置维度）", type="csv")

    wb_raw = ev_raw = dim_raw = None
    src_label = ""
    if wb_file is not None and ev_file is not None:
        wb_raw = pd.read_csv(wb_file)
        ev_raw = pd.read_csv(ev_file)
        dim_raw = pd.read_csv(dim_file) if dim_file is not None else None
        src_label = "上传文件"
        miss_wb = {"waybill_no", "created_at", "warehouse_id", "dest_region",
                   "weight_kg", "status", "signed_at"} - set(wb_raw.columns)
        miss_ev = {"waybill_no", "event_type", "event_time"} - set(ev_raw.columns)
        if miss_wb or miss_ev:
            msg = []
            if miss_wb:
                msg.append("运单表缺: " + "、".join(sorted(miss_wb)))
            if miss_ev:
                msg.append("事件表缺: " + "、".join(sorted(miss_ev)))
            st.error("列不完整，无法处理 —— " + "；".join(msg))
            wb_raw = ev_raw = None
    elif (wb_file is None) != (ev_file is None):
        st.warning("运单表和轨迹事件表需要一起上传才能完成清洗。")
    if wb_raw is None and st.session_state.get("use_builtin") is None \
            and st.button("没有数据？载入内置原始数据，体验完整流程"):
        st.session_state["use_builtin"] = True
        st.rerun()
    if wb_raw is None and st.session_state.get("use_builtin"):
        wb_raw, ev_raw, dim_raw = builtin_raw()
        src_label = "内置原始数据（含约 3% 脏数据）"

    if wb_raw is None:
        st.info("上传两张 CSV 后进入下一步；没有数据可点上方按钮用内置数据走一遍流程。")
        return

    # ---- ② 预览 ----
    st.subheader(f"② 预览（{src_label}）")
    view = st.radio("预览范围", ["前 20 条", "全部"], horizontal=True, label_visibility="collapsed")
    n_show = 20 if view == "前 20 条" else len(wb_raw)
    tab1, tab2 = st.tabs([f"运单表 · {len(wb_raw):,} 行", f"轨迹事件表 · {len(ev_raw):,} 行"])
    WB_CN = {"waybill_no": "运单号", "created_at": "下单时间", "warehouse_id": "发货仓",
             "dest_province": "目的省", "dest_region": "目的区域", "weight_kg": "重量kg",
             "status": "状态", "exception_type": "异常类型", "signed_at": "签收时间"}
    EV_CN = {"event_id": "事件编号", "waybill_no": "运单号",
             "event_type": "事件类型", "event_time": "事件时间"}
    STATUS_CN = {"SIGNED": "已签收", "IN_TRANSIT": "在途", "EXCEPTION": "异常"}
    EVENT_CN = {"CREATED": "下单", "AUDITED": "审单", "OUTBOUND": "出库", "PICKED_UP": "揽收",
                "DEPARTED_HUB": "离开始发仓", "ARRIVED_HUB": "到达目的仓",
                "OUT_FOR_DELIVERY": "开始派送", "SIGNED": "签收"}
    wb_v = wb_raw.head(n_show).copy()
    if "status" in wb_v.columns:
        wb_v["status"] = wb_v["status"].map(STATUS_CN).fillna(wb_v["status"])
    ev_v = ev_raw.head(n_show).copy()
    if "event_type" in ev_v.columns:
        ev_v["event_type"] = ev_v["event_type"].map(EVENT_CN).fillna(ev_v["event_type"])
    with tab1:
        st.dataframe(wb_v.rename(columns=WB_CN), use_container_width=True)
    with tab2:
        st.dataframe(ev_v.rename(columns=EV_CN), use_container_width=True)

    # ---- ③ 清洗 ----
    st.subheader("③ 清洗")
    st.caption("规则：R1 重复运单 · R2 签收早于下单 · R3 重量异常 · R4 轨迹时间倒挂 · R5 缺失关键事件"
               " —— 只清洗不修复，命中明细全部留痕")
    if st.button("🧹 执行清洗", type="primary"):
        wb_c, ev_c, rep, summ = clean_tables(wb_raw, ev_raw)
        st.session_state["clean_res"] = {"wb": wb_c, "ev": ev_c, "rep": rep, "summ": summ}
    cr = st.session_state.get("clean_res")
    if not cr:
        st.info("点击「执行清洗」查看清洗了哪些方面。")
        return
    cA, cB = st.columns([1, 2])
    with cA:
        st.markdown("**清洗前后对比**")
        st.dataframe(cr["summ"].set_index("指标"), use_container_width=True)
    with cB:
        st.markdown("**清洗了哪些方面**")
        st.dataframe(cr["rep"], hide_index=True, use_container_width=True)

    # ---- ④ 同步到报表 ----
    st.subheader("④ 同步到可视化报表")
    if st.button("🔄 同步到报表", type="primary"):
        st.session_state["custom_raw"] = {
            "wb": cr["wb"], "ev": cr["ev"], "wh": _build_dim(cr["wb"], dim_raw)}
        st.rerun()
    if st.session_state.get("custom_raw") is not None:
        st.success("✅ 当前报表正在使用你上传并清洗后的数据 —— 左侧切到其他页面即可查看。")
        if st.button("↩ 恢复内置数据"):
            st.session_state.pop("custom_raw", None)
            st.session_state.pop("use_builtin", None)
            st.session_state.pop("clean_res", None)
            st.rerun()


# ---------------- 页面：报告撰写 ----------------
def _md_inline(par, text: str):
    """把行内 **粗体** 写进 paragraph"""
    for part in re.split(r"(\*\*.+?\*\*)", text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            par.add_run(part[2:-2]).bold = True
        elif part:
            par.add_run(part)


def md_to_docx(md: str) -> bytes:
    """极简 Markdown -> Word（标题/无序列表/有序列表/表格/引用/分隔线/粗体）"""
    from docx import Document
    doc = Document()
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:                                   # 标题
            doc.add_heading(m.group(2).replace("**", ""), level=len(m.group(1)))
            i += 1
        elif set(s) <= set("-*_"):              # 分隔线
            i += 1
        elif s.startswith(">"):                 # 引用
            _md_inline(doc.add_paragraph(style="Intense Quote"), s.lstrip("> "))
            i += 1
        elif re.match(r"^[-*]\s+", s):          # 无序列表
            _md_inline(doc.add_paragraph(style="List Bullet"),
                       re.sub(r"^[-*]\s+", "", s))
            i += 1
        elif re.match(r"^\d+[.、)]\s*", s):     # 有序列表
            _md_inline(doc.add_paragraph(style="List Number"),
                       re.sub(r"^\d+[.、)]\s*", "", s))
            i += 1
        elif s.startswith("|") and i + 1 < len(lines) \
                and set(lines[i + 1].strip()) <= set("|-: "):   # 表格
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows.pop(1)                          # 去掉分隔行
            ncol = max(len(r) for r in rows)
            tb = doc.add_table(rows=len(rows), cols=ncol)
            tb.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, cell in enumerate(row[:ncol]):
                    tb.cell(r, c).text = cell.replace("**", "")
        else:                                    # 普通段落
            _md_inline(doc.add_paragraph(), s)
            i += 1
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _default_report(d) -> str:
    """根据当前报表数据预填一份报告草稿"""
    k, s, h = d["kpi"].iloc[0], d["stages"].iloc[0], d["health"].iloc[0]
    wh = d["wh"]
    slow = wh.iloc[0]                            # 全程最慢的仓
    others = wh.iloc[1:]
    wk = d["weekend"].set_index("时段")
    er = d["exc_route"]
    stages = [("审单", s.audit), ("出库", s.outbound), ("揽收", s.pickup),
              ("进始发仓", s.enter_hub), ("干线", s.trunk), ("分拣", s["sort"]),
              ("派送", s.delivery)]
    trunk_pct = round(s.trunk / max(k.avg_total_h, 1) * 100, 1)
    top_exc = er.iloc[0] if len(er) else None
    lines = [
        "# 快递履约时效分析报告", "",
        f"> 数据范围：90 天 · 3 个区域仓 · 已签收 {int(k.signed_orders):,} 单（整体签收率 {k.signed_rate}%）", "",
        "## 一、核心指标", "",
        "| 指标 | 数值 |", "|---|---|",
        f"| 平均全程时效 | {k.avg_total_h} 小时 |",
        f"| 72小时送达率 | {h.nextday72_pct}% |",
        f"| 完美订单率 | {h.perfect_order_pct}% |",
        f"| 异常订单 | {int(h.exception_orders):,} 单 |", "",
        "## 二、全链路环节耗时", "",
        "链条：" + " → ".join(f"{n} {v}h" for n, v in stages),
        f"干线占全程 {trunk_pct}%，是最大的单环节；前端仓内作业（揽收+出库）合计 "
        f"{round(s.pickup + s.outbound, 1)} 小时，容易被低估。", "",
        "## 三、主要发现", "",
        f"1. **{slow['仓']}的仓内作业偏慢**：全程 {slow['全程']} 小时，高出其余仓均值约 "
        f"{round(slow['全程'] - others['全程'].mean(), 1)} 小时"
        f"（揽收 {slow['揽收']}h、出库 {slow['出库']}h，均约为其他仓两倍）",
        f"2. **周末派送运力不足**：周末平均派送 {wk.loc['周末', '平均派送时长']} 小时，"
        f"工作日仅 {wk.loc['工作日', '平均派送时长']} 小时，慢单占比 {wk.loc['周末', '慢单占比']}%",
        f"3. **异常单集中在拒收**，异常率最高线路为 "
        + (f"{top_exc['仓']}→{top_exc['目的区域']}（{top_exc['异常率']}%）" if top_exc is not None else "（无线路数据）"),
        "", "## 四、优化建议（按收益排序）", "",
        "| 优先级 | 动作 | 预期收益 |", "|---|---|---|",
        f"| 1 | {slow['仓']}出库+揽收作业治理 | 仓均全程时效明显下降 |",
        "| 2 | 干线中转环节排查 | 高超时线路超时率回落 |",
        "| 3 | 周末派送运力补充 | 周末派送时长接近工作日水平 |", "",
        "## 五、结论", "",
        f"- **瓶颈在始发端，不在末端**：各仓派送时长接近（约 {s.delivery} 小时），"
        f"差距集中在仓内——{slow['仓']}揽收+出库约为其他仓两倍，"
        f"是全程 {k.avg_total_h} 小时的主要超标来源",
        f"- **干线是最大单环节**（占全程 {trunk_pct}%），且时效呈明显周期性："
        f"周末派送 {wk.loc['周末', '平均派送时长']} 小时 vs 工作日 "
        f"{wk.loc['工作日', '平均派送时长']} 小时，周末运力是确定性短板",
        f"- **风险可量化、可定向**：异常单 {int(h.exception_orders):,} 单"
        f"（占已签收 {round(h.exception_orders / max(k.signed_orders, 1) * 100, 1)}%）"
        + (f"，最高发线路 {top_exc['仓']}→{top_exc['目的区域']}（{top_exc['异常率']}%）"
           if top_exc is not None else "") + "，按线路治理即可覆盖大部分异常",
        f"- **整体判断**：按收益排序落实三项建议后，仓均全程时效有望缩短约 "
        f"{round(slow['全程'] - others['全程'].mean(), 1)} 小时，"
        f"72 小时送达率可由 {h.nextday72_pct}% 进一步提升", "",
    ]
    return "\n".join(lines)


def page_report(d):
    st.header("📝 报告撰写")
    st.caption("用 Markdown 撰写分析报告，左侧编辑 / 右侧预览；可导出 Markdown 或 Word 到本地")

    if "report_md" not in st.session_state:
        st.session_state["report_md"] = _default_report(d)

    def _regen():
        st.session_state["report_md"] = _default_report(d)
        st.session_state["report_regen"] = True

    if st.button("🔄 重新生成草稿（覆盖当前内容）", on_click=_regen):
        pass
    if st.session_state.pop("report_regen", False):
        st.toast("✅ 已按当前报表数据重新生成草稿", icon="📝")

    tab_edit, tab_prev = st.tabs(["✍️ 编辑（Markdown）", "👁️ 预览"])
    with tab_edit:
        st.text_area("报告内容", key="report_md", height=560, label_visibility="collapsed")
    with tab_prev:
        st.markdown(st.session_state["report_md"])

    st.subheader("导出到本地")
    fname = st.text_input("文件名（不含扩展名）", value="快递履约时效分析报告")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("⬇️ 导出 Markdown（.md）",
                           st.session_state["report_md"].encode("utf-8"),
                           file_name=f"{fname}.md", mime="text/markdown",
                           type="primary", use_container_width=True)
    with c2:
        try:
            st.download_button("⬇️ 导出 Word（.docx）",
                               md_to_docx(st.session_state["report_md"]),
                               file_name=f"{fname}.docx",
                               mime="application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document",
                               type="primary", use_container_width=True)
        except ImportError:
            st.error("导出 Word 需要 python-docx 依赖（pip install python-docx）")


# ---------------- 页面：系统说明 ----------------
def page_system(d):
    st.header("ℹ️ 系统说明")
    st.caption("一个面向商家端的快递履约时效分析系统：从数据到结论到报告，全流程可视、可复现")

    c1, c2, c3 = st.columns([1, 1, 1.25])
    with c1:
        st.markdown("#### ✅ 能做什么")
        st.markdown(
            "**数据分析全流程**\n"
            "1. **数据采集**——内置 5 万运单模拟数据，或上传自己的 CSV\n"
            "2. **数据清洗**——5 条规则（重复运单、时间倒挂、签收早于下单、"
            "重量异常、缺关键事件），只清洗不修复，全程留痕\n"
            "3. **特征构建**——轨迹事件折叠为环节宽表（7 段耗时、72 小时门槛、周末标记）\n"
            "4. **输出结论**——分仓 / 线路 / 时段归因，结论均可由 SQL 复现\n"
            "5. **可视化与报告**——8 个看板页 + 报告撰写导出 Markdown / Word")
        st.info("看板能力：时效大盘（按日/周/两周/月切换）、分仓对比、慢因定位、"
                "异常归因、慢线路排名、数据质量、报告撰写")
    with c2:
        st.markdown("#### ⛔ 不能做什么")
        st.markdown(
            "- 仅商家端履约分析，**不含**平台方与消费者商城\n"
            "- 不接入真实业务系统，**无实时数据**\n"
            "- 数据中无二次投递、预约揽收等事件，"
            "**妥投率即签收率**\n"
            "- **不做**财务对账、WMS 库内作业（库位/波次/盘点）分析\n"
            "- 上传数据缺少必需列时无法清洗（见右侧数据要求）\n"
            "- 时间倒挂/缺失事件的运单只能剔除，无法修复")
    with c3:
        st.markdown("#### 📦 需要什么数据")
        st.caption("在「数据入口」页上传，运单表和轨迹表需一起提供")
        st.markdown(
            "**运单表**（必需）\n"
            "- waybill_no —— 运单号\n"
            "- warehouse_id —— 仓库\n"
            "- dest_region —— 目的区域\n"
            "- status —— 运单状态\n"
            "- created_at —— 下单时间\n"
            "- signed_at —— 签收时间\n"
            "- weight_kg —— 重量")
        st.markdown(
            "**轨迹事件表**（必需）\n"
            "- waybill_no —— 运单号\n"
            "- event_type —— 环节类型\n"
            "- event_time —— 事件时间")
        st.markdown(
            "**仓库维度表**（可选）\n"
            "- warehouse_id —— 仓库\n"
            "- warehouse_name —— 仓名")
        st.caption("event_type 取值：CREATED → AUDITED → OUTBOUND → PICKED_UP → "
                   "DEPARTED_HUB → ARRIVED_HUB → OUT_FOR_DELIVERY → SIGNED")

    st.markdown("---")
    st.markdown("**一图看懂系统边界**：数据源（内置模拟 / 上传 CSV / MySQL）→ "
                "清洗（5 规则留痕）→ 口径宽表 → 分析看板（8 页）→ 报告导出（Markdown / Word）；"
                "数据库、财务系统、WMS、消费者端不在本系统范围内")


PAGES = {"ℹ️ 系统说明": page_system, "📥 数据入口": page_datahub,
         "🚚 履约总览": page_fulfillment,
         "📦 时效大盘": page_overview, "🏭 分仓对比": page_warehouse,
         "🔍 慢因定位": page_problems, "⚠️ 异常归因": page_exception,
         "🐌 慢线路前十": page_slow, "🧹 数据质量": page_quality,
         "📝 报告撰写": page_report}

with st.sidebar:
    st.markdown(
        "<div class='brand-row'><span class='brand-ico'>🚚</span>"
        "<span class='brand-name'>物流时效分析</span></div>"
        "<div class='brand-sub'>快递履约全链路 · 5 万运单 · 90 天</div>"
        "<div class='tape'></div>", unsafe_allow_html=True)
    page = st.radio("页面", list(PAGES), label_visibility="collapsed")
    st.markdown(f"<div class='src-tag'>📦 数据源：{st.session_state.get('src', '加载中…')}</div>",
                unsafe_allow_html=True)

d = load_data()
PAGES[page](d)
