# -*- coding: utf-8 -*-
"""
运单数据清洗脚本（数据分析流程第 2 步：数据清洗）

输入: data/output/ 下的原始数据（含约 3% 脏数据）
输出: data/clean/ 下的清洗后数据 + 清洗报告

清洗规则（按依赖顺序执行，每条规则的命中量都记录进报告）:
  R1 重复运单      同一 waybill_no 出现多条完整记录 -> 保留首条，其余剔除
  R2 签收早于下单   signed_at <= created_at，时间戳不可信 -> 剔除整单
  R3 重量异常      重量不在 [0.1, 100] kg 物理合理区间 -> 剔除整单
  R4 轨迹时间倒挂   同一运单事件按业务链路顺序检查，时间戳非单调递增 -> 剔除整单
  R5 缺失关键事件   已签收运单缺少计算环节时长所必需的中间事件 -> 剔除整单

清洗口径说明：
  - 只清洗，不修复：时间倒挂/缺失事件无法从数据还原真实值，
    修复等于编造数据，因此统一剔除并留痕。
  - 剔除的都是 SIGNED 单或整单，在途/异常单中除 R4 外不受影响。
  - 清洗后数据用于全部下游分析（MySQL 导入、看板、结论报告）。
"""
import os
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "output")
CLEAN = os.path.join(BASE, "clean")
os.makedirs(CLEAN, exist_ok=True)

CHAIN_ORDER = ["CREATED", "AUDITED", "OUTBOUND", "PICKED_UP", "DEPARTED_HUB",
               "ARRIVED_HUB", "OUT_FOR_DELIVERY", "SIGNED"]
report = []   # (规则, 说明, 动作, 命中运单数, 命中事件数)


def log(rule, desc, action, n_wb, n_ev):
    report.append({"规则": rule, "问题": desc, "处理动作": action,
                   "涉及运单": n_wb, "涉及事件": n_ev})


wb = pd.read_csv(f"{RAW}/fact_waybills.csv", parse_dates=["created_at", "signed_at"])
ev = pd.read_csv(f"{RAW}/fact_tracking_events.csv", parse_dates=["event_time"])
dim = pd.read_csv(f"{RAW}/dim_warehouse.csv")
n_wb_raw, n_ev_raw = len(wb), len(ev)

# ---- R1 重复运单：waybill_no 重复 -> 保留首条 ----
dup = wb["waybill_no"].duplicated(keep="first")
log("R1 重复运单", "同一运单号存在多条记录", "保留首条，剔除重复",
    int(dup.sum()), int(ev["waybill_no"].isin(wb.loc[dup, "waybill_no"]).sum()))
wb = wb[~dup]

# ---- R2 签收早于下单：时间戳不可信 ----
bad = wb["signed_at"].notna() & (wb["signed_at"] <= wb["created_at"])
log("R2 签收早于下单", "signed_at <= created_at", "剔除整单",
    int(bad.sum()), int(ev["waybill_no"].isin(wb.loc[bad, "waybill_no"]).sum()))
wb = wb[~bad]

# ---- R3 重量异常：物理合理区间 [0.1, 100] kg ----
bad = ~wb["weight_kg"].between(0.1, 100)
log("R3 重量异常", "重量超出 [0.1, 100] kg 合理区间", "剔除整单",
    int(bad.sum()), int(ev["waybill_no"].isin(wb.loc[bad, "waybill_no"]).sum()))
wb = wb[~bad]

# ---- R4 轨迹时间倒挂：按业务链路顺序检查事件时间单调性 ----
ev = ev[ev["waybill_no"].isin(wb["waybill_no"])]          # 只看已保留运单的事件
rank = {et: i for i, et in enumerate(CHAIN_ORDER)}
ev = ev[ev["event_type"].isin(CHAIN_ORDER)].copy()
ev["ord"] = ev["event_type"].map(rank)
bad_nos = set()
for no, sub in ev.groupby("waybill_no"):
    t = sub.sort_values("ord")["event_time"].to_numpy()
    if np.any(np.diff(t) < np.timedelta64(0, "s")):
        bad_nos.add(no)
log("R4 轨迹时间倒挂", "事件时间戳与业务链路顺序不一致，无法还原真实顺序",
    "剔除整单", len(bad_nos), int(ev["waybill_no"].isin(bad_nos).sum()))
wb = wb[~wb["waybill_no"].isin(bad_nos)]

# ---- R5 缺失关键事件：SIGNED 单缺中间环节，环节时长算不出来 ----
ev = ev[ev["waybill_no"].isin(wb["waybill_no"])]
need = ["AUDITED", "OUTBOUND", "PICKED_UP", "DEPARTED_HUB", "ARRIVED_HUB", "OUT_FOR_DELIVERY"]
have = (ev[ev["event_type"].isin(need)]
        .assign(v=1).pivot_table(index="waybill_no", columns="event_type",
                                 values="v", aggfunc="first"))
# 注意用 notna().all()：pandas 的 all() 会跳过 NaN，缺事件的行会被误判为齐全
full = have.notna().all(axis=1)
signed = wb.loc[wb["status"] == "SIGNED", "waybill_no"]
missing = signed[~signed.isin(full[full].index)]
log("R5 缺失关键事件", "已签收运单缺少揽收/干线/派送等关键环节事件",
    "剔除整单", len(missing), int(ev["waybill_no"].isin(missing).sum()))
wb = wb[~wb["waybill_no"].isin(missing)]

ev = ev[ev["waybill_no"].isin(wb["waybill_no"])].drop(columns="ord", errors="ignore")

# ---- 清洗后与标签对比（生成器留了标准答案，用于验证检出率） ----
# 口径：R1 去重保留首条，运单本体仍在（这是正确行为），效果体现为重复行被移除，
#       不适用"运单是否被剔除"的检出率口径；其余规则按运单是否被剔除计算。
labels = pd.read_csv(f"{RAW}/dirty_labels.csv").drop_duplicates("waybill_no")
res = labels[labels["dirty_rule"] != "重复运单"].groupby("dirty_rule")["waybill_no"].apply(
    lambda s: f"{(~s.isin(wb['waybill_no'])).mean() * 100:.1f}%") \
    if len(labels) else pd.Series(dtype=str)
dup_fixed = f"{int((labels['dirty_rule'] == '重复运单').sum())} 单的重复记录已全部移除"

# ---- 输出 ----
wb_out = wb.copy()
wb_out["created_at"] = wb_out["created_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
wb_out["signed_at"] = wb_out["signed_at"].dt.strftime("%Y-%m-%d %H:%M:%S")
ev_out = ev.copy()
ev_out["event_time"] = ev_out["event_time"].dt.strftime("%Y-%m-%d %H:%M:%S")

dim.to_csv(f"{CLEAN}/dim_warehouse.csv", index=False, encoding="utf-8-sig")
wb_out.to_csv(f"{CLEAN}/fact_waybills.csv", index=False, encoding="utf-8-sig")
ev_out.to_csv(f"{CLEAN}/fact_tracking_events.csv", index=False, encoding="utf-8-sig")
pd.DataFrame(report).to_csv(f"{CLEAN}/cleaning_report.csv", index=False, encoding="utf-8-sig")

summary = pd.DataFrame([
    {"指标": "运单数", "清洗前": f"{n_wb_raw:,}", "清洗后": f"{len(wb):,}",
     "剔除": f"{n_wb_raw - len(wb):,}（{((n_wb_raw - len(wb)) / n_wb_raw) * 100:.2f}%）"},
    {"指标": "轨迹事件", "清洗前": f"{n_ev_raw:,}", "清洗后": f"{len(ev):,}",
     "剔除": f"{n_ev_raw - len(ev):,}（{((n_ev_raw - len(ev)) / n_ev_raw) * 100:.2f}%）"}])
summary.to_csv(f"{CLEAN}/cleaning_summary.csv", index=False, encoding="utf-8-sig")

print(summary.to_string(index=False))
print("\n各规则命中:")
print(pd.DataFrame(report).to_string(index=False))
if len(res):
    print("\n脏数据检出率（对照生成器标签）:")
    print(res.to_string())
    print(f"重复运单: {dup_fixed}")
