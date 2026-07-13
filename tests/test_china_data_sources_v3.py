"""
最后验证:
1. 新浪 USDCNY 正确字段映射
2. futures_zh_daily_sina 通用代码映射表
3. cross-check akshare vs 新浪 沪深300 一致性
"""

import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests
import json

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0",
}

print("=== 新浪 USDCNY 字段映射 ===")
url = "https://hq.sinajs.cn/list=USDCNY"
resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
resp.encoding = "gbk"
raw = resp.text
print(f"原始: {raw}")
if '="' in raw:
    fields = raw.split('="')[1].rstrip('";\n').split(",")
    labels = ["时间","最新价(卖一)","昨收","今开","成交量(手)","买一价","卖一价","最低","最高","名称","日期"]
    for i, (label, val) in enumerate(zip(labels, fields)):
        print(f"  [{i}] {label}: {val}")

print("\n=== futures_zh_daily_sina 代码映射表 ===")
# 测试更多品种
import akshare as ak
futures_map = {
    "I0": "铁矿石(大商所)",
    "AL0": "沪铝(上期所)",
    "CU0": "沪铜(上期所)",
    "RB0": "螺纹钢(上期所)",
    "HC0": "热卷(上期所)",
    "ZN0": "沪锌(上期所)",
    "AU0": "沪金(上期所)",
    "AG0": "沪银(上期所)",
    "MA0": "甲醇(郑商所)",
    "TA0": "PTA(郑商所)",
    "M0": "豆粕(大商所)",
    "Y0": "豆油(大商所)",
    "P0": "棕榈油(大商所)",
    "SR0": "白糖(郑商所)",
    "CF0": "棉花(郑商所)",
    "SC0": "原油(上期能源)",
    "FU0": "燃油(上期所)",
}
for code, name in futures_map.items():
    try:
        df = ak.futures_zh_daily_sina(symbol=code)
        if len(df) > 0:
            latest = df.iloc[-1]
            close = latest.get('close', '?')
            print(f"  {code} {name}: close={close}, rows={len(df)}")
        else:
            print(f"  {code} {name}: EMPTY")
    except Exception as e:
        print(f"  {code} {name}: ERROR - {str(e)[:80]}")

print("\n=== 交叉验证: akshare vs 新浪 沪深300 ===")
df_ak = ak.stock_zh_index_daily(symbol="sh000300")
ak_close = float(df_ak.iloc[-1]['close'])
print(f"  akshare 历史收盘: {ak_close}")

url = "https://hq.sinajs.cn/list=s_sh000300"
resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
resp.encoding = "gbk"
sina_price = float(resp.text.split('="')[1].split(",")[1])
print(f"  新浪 实时价格: {sina_price}")

diff_pct = abs(sina_price - ak_close) / ak_close * 100
print(f"  偏差: {diff_pct:.3f}%")
if diff_pct < 2:
    print("  [PASS] 双源一致性良好 (<2%)")
else:
    print(f"  [WARN] 偏差较大: {diff_pct:.2f}% (可能是日内波动)")

print("\n=== 新浪 HSI 实时 vs akshare 历史收盘 ===")
df_hsi = ak.stock_hk_index_daily_sina(symbol="HSI")
hsi_close = float(df_hsi.iloc[-1]['close'])
print(f"  akshare 历史收盘: {hsi_close}")

url = "https://hq.sinajs.cn/list=int_hangseng"
resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
resp.encoding = "gbk"
hsi_realtime = float(resp.text.split('="')[1].split(",")[1])
print(f"  新浪 实时: {hsi_realtime}")

diff_pct = abs(hsi_realtime - hsi_close) / hsi_close * 100
print(f"  偏差: {diff_pct:.3f}%")
if diff_pct < 2:
    print("  [PASS] 双源一致性良好 (<2%)")
else:
    print(f"  [WARN] 偏差较大: {diff_pct:.2f}% (可能是日内波动)")

print("\n=== akshare bond_zh_us_rate 完整字段 ===")
df_bonds = ak.bond_zh_us_rate()
print(f"  列: {list(df_bonds.columns)}")
latest = df_bonds.iloc[-1]
print(f"  中国2Y: {latest.get('中国国债收益率2年')}%")
print(f"  中国5Y: {latest.get('中国国债收益率5年')}%")
print(f"  中国10Y: {latest.get('中国国债收益率10年')}%")
print(f"  中国30Y: {latest.get('中国国债收益率30年')}%")
print(f"  美国2Y: {latest.get('美国国债收益率2年')}%")
print(f"  美国5Y: {latest.get('美国国债收益率5年')}%")
print(f"  美国10Y: {latest.get('美国国债收益率10年')}%")
print(f"  美国30Y: {latest.get('美国国债收益率30年')}%")
print(f"  美国10Y-2Y: {latest.get('美国国债收益率10年-2年')}%")
print(f"  中国10Y-2Y: {latest.get('中国国债收益率10年-2年')}%")

print("\n=== 完整验证完成 ===")
