# -*- coding: utf-8 -*-
"""
快递运单模拟数据生成器（面试作品集：物流时效分析）

生成 3 张表：
  dim_warehouse.csv          仓库维度表
  fact_waybills.csv          运单事实表（一条 = 一票运单）
  fact_tracking_events.csv   轨迹事件表（一条 = 一个环节节点）

时间范围：2026-07-25 ~ 2026-10-22（90 天）
规模：约 5 万运单

故意埋入的 3 个业务问题（供分析环节发现）：
  P1  华南仓（广州）揽收环节系统性偏慢（揽收时长 +6h 偏移，人员排班问题设定）
  P2  华东仓 -> 华中区域的干线自 9 月 10 日起恶化（+12h，设定为中转场搬迁导致）
  P3  周末派送环节偏慢（+3.5h，设定为周末派送人手不足）
  P4  华南仓出库环节偏慢（+4h，与 P1 同源：仓内人手不足），履约漏斗的瓶颈定位素材

履约链路（8 环节）：
  CREATED 下单 -> AUDITED 审单 -> OUTBOUND 出库 -> PICKED_UP 揽收
  -> DEPARTED_HUB 始发仓发出 -> ARRIVED_HUB 到达目的仓 -> OUT_FOR_DELIVERY 派送 -> SIGNED 签收
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

rng = np.random.default_rng(42)
BASE_DATE = datetime(2026, 7, 25)
N_DAYS = 90
N_WAYBILLS = 50_000

# ---------------- 仓库维度 ----------------
warehouses = pd.DataFrame({
    "warehouse_id": ["WH01", "WH02", "WH03"],
    "warehouse_name": ["华东仓", "华南仓", "华中仓"],
    "city": ["上海", "广州", "武汉"],
})

# 目的省份分布（每仓的目的地偏好不同，制造真实感）
PROV_POOLS = {
    "WH01": [("江苏省", .18), ("浙江省", .18), ("上海市", .16), ("安徽省", .10),
             ("山东省", .08), ("河南省", .06), ("湖北省", .06), ("福建省", .05),
             ("江西省", .04), ("北京市", .04), ("广东省", .03), ("四川省", .02)],
    "WH02": [("广东省", .30), ("广西壮族自治区", .10), ("福建省", .10), ("湖南省", .09),
             ("江西省", .08), ("浙江省", .08), ("江苏省", .06), ("湖北省", .05),
             ("四川省", .05), ("贵州省", .04), ("云南省", .03), ("海南省", .02)],
    "WH03": [("湖北省", .26), ("河南省", .16), ("湖南省", .14), ("安徽省", .10),
             ("江西省", .08), ("四川省", .07), ("重庆市", .06), ("陕西省", .05),
             ("江苏省", .04), ("广东省", .04)],
}
# 干线基准时长（小时）：按 (仓, 目的区域) 粗分
REGION_EAST = {"上海市", "江苏省", "浙江省", "安徽省", "山东省", "福建省"}
REGION_SOUTH = {"广东省", "广西壮族自治区", "海南省", "湖南省", "江西省"}
REGION_CENTRAL = {"湖北省", "河南省"}
REGION_WEST = {"四川省", "重庆市", "陕西省", "贵州省", "云南省", "北京市", "山西省", "河北省"}

def trunk_hours(wh, prov):
    """基准干线时长"""
    if wh == "WH01":
        return 16 if prov in REGION_EAST else (30 if prov in REGION_SOUTH else 38)
    if wh == "WH02":
        return 14 if prov in REGION_SOUTH else (30 if prov in REGION_EAST else 36)
    return 12 if prov in REGION_CENTRAL else (26 if prov in REGION_EAST else 32)

def weekend_flag(dt):
    return dt.weekday() >= 5

# ---------------- 生成运单 ----------------
rows_wb, rows_ev = [], []
ev_id = 0
wh_ids = np.array(list(PROV_POOLS))
wh_probs = np.array([.45, .35, .20])  # 单量分布：华东最大

for i in range(N_WAYBILLS):
    day_offset = int(rng.integers(0, N_DAYS))
    created = BASE_DATE + timedelta(days=day_offset,
                                    hours=int(rng.integers(8, 22)),
                                    minutes=int(rng.integers(0, 60)),
                                    seconds=int(rng.integers(0, 60)))
    wh = str(rng.choice(wh_ids, p=wh_probs))
    provs, ps = zip(*PROV_POOLS[wh])
    prov = str(rng.choice(np.array(provs), p=np.array(ps)))
    weight = float(np.round(rng.lognormal(-0.6, 0.7) + 0.3, 2))  # 0.3~5kg 为主

    # ---- 各环节时长（对数正态模拟真实波动）----
    # 审单 -> 出库（漏斗前置环节）
    t_audit = rng.lognormal(np.log(0.8), 0.35)             # 审单 0.5~1.5h
    t_outbound = rng.lognormal(np.log(4.0), 0.35)          # 审单->出库
    # P4: 华南仓出库慢
    if wh == "WH02":
        t_outbound += 4.0 + rng.normal(0, 1.0)
    # P1: 华南仓揽收慢
    pickup_base = 6.0 + (6.0 if wh == "WH02" else 0.0)
    t_pickup = rng.lognormal(np.log(pickup_base), 0.45)
    t_enter_hub = rng.lognormal(np.log(4.0), 0.4)          # 揽收->进始发仓
    t_trunk = rng.lognormal(np.log(trunk_hours(wh, prov)), 0.30)
    # P2: 华东->华中 干线 9/10 起恶化
    if wh == "WH01" and prov in REGION_CENTRAL and created >= datetime(2026, 9, 10):
        t_trunk += 12.0 + rng.normal(0, 2.0)
    t_sort = rng.lognormal(np.log(5.0), 0.35)              # 到达仓->分拣出库
    # P3: 周末派送慢
    delivery_base = 4.0 + (3.5 if weekend_flag(created + timedelta(hours=t_audit + t_outbound + t_pickup + t_enter_hub + t_trunk + t_sort)) else 0.0)
    t_delivery = rng.lognormal(np.log(delivery_base), 0.40)

    times = {
        "CREATED": created,
        "AUDITED": created + timedelta(hours=t_audit),
        "OUTBOUND": created + timedelta(hours=t_audit + t_outbound),
        "PICKED_UP": created + timedelta(hours=t_audit + t_outbound + t_pickup),
        "DEPARTED_HUB": created + timedelta(hours=t_audit + t_outbound + t_pickup + t_enter_hub),
        "ARRIVED_HUB": created + timedelta(hours=t_audit + t_outbound + t_pickup + t_enter_hub + t_trunk),
        "OUT_FOR_DELIVERY": created + timedelta(hours=t_audit + t_outbound + t_pickup + t_enter_hub + t_trunk + t_sort),
    }
    signed_at = times["OUT_FOR_DELIVERY"] + timedelta(hours=t_delivery)

    # ---- 运单状态：93% 已签收，5% 在途，2% 异常 ----
    u = rng.random()
    now = BASE_DATE + timedelta(days=N_DAYS)
    if u < 0.93:
        status = "SIGNED"
        final_signed = signed_at
    elif u < 0.98:
        status = "IN_TRANSIT"
        final_signed = None
    else:
        status = "EXCEPTION"
        final_signed = None

    # 异常类型（3 号扩展）：拒收/破损/丢失，偏远省份丢失率略高
    if status == "EXCEPTION":
        remote = prov in ("云南省", "贵州省", "四川省", "陕西省")
        et_probs = [0.45, 0.30, 0.25] if not remote else [0.40, 0.25, 0.35]
        exc_type = str(rng.choice(np.array(["拒收", "破损", "丢失"]), p=et_probs))
    else:
        exc_type = None

    wb_no = f"SF{day_offset:03d}{i:06d}{rng.integers(100, 999)}"
    rows_wb.append((wb_no, times["CREATED"], wh, prov, prov.replace("省", "").replace("市", "")
                    .replace("壮族自治区", "").replace("回族", "").replace("维吾尔", "") + "区",
                    weight, status, exc_type, final_signed))

    chain = ["CREATED", "AUDITED", "OUTBOUND", "PICKED_UP", "DEPARTED_HUB",
             "ARRIVED_HUB", "OUT_FOR_DELIVERY"]
    if status == "SIGNED":
        chain.append("SIGNED")
    elif status == "IN_TRANSIT":
        # 在途：随机停在某一环
        cut = int(rng.integers(1, 7))
        chain = chain[:cut + 1]
    else:
        # 异常：停在到达仓之后，卡住
        chain = chain[:7]

    for et in chain:
        ev_id += 1
        rows_ev.append((ev_id, wb_no, et, times.get(et, final_signed)))

wb = pd.DataFrame(rows_wb, columns=[
    "waybill_no", "created_at", "warehouse_id", "dest_province", "dest_region",
    "weight_kg", "status", "exception_type", "signed_at"])
ev = pd.DataFrame(rows_ev, columns=[
    "event_id", "waybill_no", "event_type", "event_time"])

# ---------------- 注入脏数据（约 3%，供数据清洗环节处理）----------------
# 5 类脏数据随机分配到被选中的运单上；清洗规则见 data/clean_data.py
DIRTY_RULES = ["重复运单", "时间倒挂", "签收早于下单", "重量异常", "缺失关键事件"]
n_dirty = int(len(wb) * 0.03)
dirty_idx = rng.choice(len(wb), size=n_dirty, replace=False)
rule_of = {int(i): DIRTY_RULES[k % 5] for k, i in enumerate(dirty_idx)}
wb["dirty_rule"] = [rule_of.get(i, None) for i in range(len(wb))]

CHAIN_ORDER = ["CREATED", "AUDITED", "OUTBOUND", "PICKED_UP", "DEPARTED_HUB",
               "ARRIVED_HUB", "OUT_FOR_DELIVERY", "SIGNED"]

# D1 重复运单：整单连轨迹一起复制一份
dup_mask = wb["dirty_rule"] == "重复运单"
dup_nos = set(wb.loc[dup_mask, "waybill_no"])
wb = pd.concat([wb, wb[dup_mask].copy()], ignore_index=True)
ev_dup = ev[ev["waybill_no"].isin(dup_nos)].copy()
ev = pd.concat([ev, ev_dup], ignore_index=True)

# D2 时间倒挂：轨迹里相邻两个事件的时间戳互换
for no in wb.loc[wb["dirty_rule"] == "时间倒挂", "waybill_no"]:
    sub = ev[(ev["waybill_no"] == no) & (ev["event_type"].isin(CHAIN_ORDER[:7]))]
    sub = sub.sort_values("event_time")
    if len(sub) >= 2:
        a, b = sub.index[-2], sub.index[-1]
        ev.loc[[a, b], "event_time"] = ev.loc[[b, a], "event_time"].values

# D3 签收早于下单（SIGNED 单的 signed_at 挪到 created 之前）
d3 = wb["dirty_rule"] == "签收早于下单"
wb.loc[d3, "signed_at"] = wb.loc[d3, "created_at"] - pd.to_timedelta(
    rng.uniform(1, 10, size=int(d3.sum())), unit="h")

# D4 重量异常：远超现实的极小/极大值
d4 = wb["dirty_rule"] == "重量异常"
n4 = int(d4.sum())
wb.loc[d4, "weight_kg"] = np.where(rng.random(n4) < 0.5,
                                   np.round(rng.uniform(0.01, 0.08, n4), 3),
                                   np.round(rng.uniform(300, 900, n4), 1))

# D5 缺失关键事件：已签收运单随机丢掉一个中间环节事件
d5_nos = wb.loc[wb["dirty_rule"] == "缺失关键事件", "waybill_no"]
drop_ids = []
for no in d5_nos:
    sub = ev[(ev["waybill_no"] == no) & (ev["event_type"].isin(CHAIN_ORDER[1:7]))]
    if len(sub):
        drop_ids.append(int(rng.choice(sub.index.to_numpy())))
ev = ev.drop(index=drop_ids)

ev["event_id"] = range(1, len(ev) + 1)
dirty_stat = wb["dirty_rule"].value_counts()

# 时间格式化为 MySQL 友好的字符串
wb["created_at"] = wb["created_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
wb["signed_at"] = wb["signed_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
ev["event_time"] = ev["event_time"].dt.strftime("%Y-%m-%d %H:%M:%S")

import os
out = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(out, exist_ok=True)
warehouses.to_csv(f"{out}/dim_warehouse.csv", index=False, encoding="utf-8-sig")
# 运单表不带脏数据标记（模拟真实系统原始落地），标签单独存档供清洗脚本验证检出率
wb.drop(columns=["dirty_rule"]).to_csv(f"{out}/fact_waybills.csv", index=False, encoding="utf-8-sig")
wb[["waybill_no", "dirty_rule"]].dropna() \
    .to_csv(f"{out}/dirty_labels.csv", index=False, encoding="utf-8-sig")
ev.to_csv(f"{out}/fact_tracking_events.csv", index=False, encoding="utf-8-sig")

print(f"运单数: {len(wb):,}  轨迹事件: {len(ev):,}")
print(f"\n注入脏数据（清洗前原始数据，共 {int(dirty_stat.sum()):,} 单）:")
print(dirty_stat.to_string())
print("\n状态分布:")
print(wb["status"].value_counts().to_string())
