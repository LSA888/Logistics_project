# -*- coding: utf-8 -*-
"""验证 CSV 降级数据源与 MySQL 口径一致"""
import sys, os, warnings
sys.path.insert(0, os.path.dirname(__file__))
warnings.filterwarnings("ignore")

import app  # noqa: E402
import pandas as pd  # noqa: E402
import pymysql  # noqa: E402

d_csv = app.csv_aggregates()
conn = pymysql.connect(**app.DB)

k_db = pd.read_sql("SELECT COUNT(*) s, ROUND(AVG(total_h),1) a, ROUND(AVG(total_h>72)*100,2) o FROM v_waybill_stages", conn).iloc[0]
k_csv = d_csv["kpi"].iloc[0]
print(f"KPI  db: signed={k_db.s} avg={k_db.a} ot={k_db.o}  csv: signed={k_csv.signed_orders} avg={k_csv.avg_total_h} ot={k_csv.overtime_pct}")

h_db = pd.read_sql("SELECT ROUND(AVG(is_on_time)*100,2) n72, ROUND(SUM(is_on_time)/(SELECT COUNT(*) FROM fact_waybills)*100,2) pof FROM v_waybill_stages", conn).iloc[0]
h_csv = d_csv["health"].iloc[0]
print(f"健康 db: 72h={h_db.n72} 完美={h_db.pof}  csv: 72h={h_csv.nextday72_pct} 完美={h_csv.perfect_order_pct}")

st_db = pd.read_sql("SELECT ROUND(AVG(outbound_h),1) ob FROM v_waybill_stages", conn).iloc[0]
print(f"出库 db: {st_db.ob}h  csv: {d_csv['stages'].iloc[0].outbound}h")

et_db = pd.read_sql("SELECT exception_type t, COUNT(*) n FROM fact_waybills WHERE status='EXCEPTION' GROUP BY t ORDER BY n DESC", conn)
print("异常类型 db:", dict(zip(et_db.t, et_db.n)), " csv:", dict(zip(d_csv["exc_type"]["异常类型"], d_csv["exc_type"]["单量"])))

er_db = pd.read_sql("SELECT wh.warehouse_name w, w.dest_region r, ROUND(SUM(w.status='EXCEPTION')/COUNT(*)*100,2) p FROM fact_waybills w JOIN dim_warehouse wh USING(warehouse_id) GROUP BY w, r HAVING COUNT(*)>=300 ORDER BY p DESC LIMIT 3", conn)
print("异常线路db:", [tuple(x) for x in er_db.values])
print("异常线路csv:", [(r["仓"], r["目的区域"], r["异常率"]) for _, r in d_csv["exc_route"].head(3).iterrows()])

wh_db = pd.read_sql("SELECT warehouse_name w, ROUND(AVG(total_h),1) t FROM v_waybill_stages GROUP BY w ORDER BY t DESC", conn)
print("分仓  db:", dict(zip(wh_db.w, wh_db.t)), " csv:", dict(zip(d_csv["wh"]["仓"], d_csv["wh"]["全程"])))

wk_db = pd.read_sql("SELECT is_weekend w, ROUND(AVG(delivery_h),1) d FROM v_waybill_stages GROUP BY w", conn)
print("周末  db:", dict(zip(wk_db.w.astype(int), wk_db.d)), " csv:", dict(zip(d_csv["weekend"]["时段"], d_csv["weekend"]["平均派送h"])))

sl_db = pd.read_sql("SELECT warehouse_name w, dest_region r, ROUND(AVG(total_h>72)*100,1) o FROM v_waybill_stages GROUP BY w,r HAVING COUNT(*)>=300 ORDER BY o DESC LIMIT 3", conn)
print("慢线路db:", [tuple(x) for x in sl_db.values])
print("慢线路csv:", [(r["仓"], r["目的区域"], r["超时率"]) for _, r in d_csv["slow"].head(3).iterrows()])
conn.close()
