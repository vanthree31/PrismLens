"""
补充测试: 需要精调的指标
1. CNH 离岸人民币 — 多种方式测试
2. 恒生科技/恒生指数 — 新浪实时 + akshare 替代
3. 新浪API DXY 完整解析
4. 新浪A股指数完整解析 (字段映射)
5. 2年期美债替代方案
"""

import json
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
import pandas as pd

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def prn(label, value):
    print(f"    {label}: {value}")


print("=" * 60)
print("  补充测试: 精调关键指标")
print("=" * 60)

# ── 1. CNH离岸人民币 ────────────────────────────
print("\n── 1. CNH离岸人民币 ──")

# 1a: akshare currency_hist (专门获取历史汇率)
print("\n  1a: akshare currency_hist('usdcnh')")
try:
    import akshare as ak
    df = ak.currency_hist(symbol="usdcnh")
    print(f"    行数: {len(df)}, 列: {list(df.columns)}")
    if len(df) > 0:
        latest = df.iloc[-1]
        print(f"    最新行: {latest.to_dict()}")
        # 找 close 或收盘价
        for col in df.columns:
            val = latest.get(col)
            if isinstance(val, (int, float)) and 6.0 < val < 8.0:
                print(f"    >>> 候选CNH值: col={col}, val={val}")
except Exception as e:
    print(f"    [FAIL] {e}")

# 1b: 新浪 USDCNY (在岸) — 作为备用
print("\n  1b: 新浪 USDCNY (在岸人民币)")
try:
    url = "https://hq.sinajs.cn/list=USDCNY"
    resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
    resp.encoding = "gbk"
    print(f"    原始: {resp.text[:300]}")
except Exception as e:
    print(f"    [FAIL] {e}")

# 1c: 新浪 外汇代码尝试 多种格式
print("\n  1c: 新浪外汇代码多格式尝试")
fx_codes = ["USDCNH", "CNH=X", "USDCNY", "CNY=X", "DINIW"]
for code in fx_codes:
    try:
        url = f"https://hq.sinajs.cn/list={code}"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text.strip()
        if text and '=""' not in text:
            print(f"    {code}: OK -> {text[:150]}")
        else:
            print(f"    {code}: EMPTY")
    except Exception as e:
        print(f"    {code}: ERROR {e}")

# 1d: akshare forex 实时汇率 (新浪源)
print("\n  1d: akshare currency_boc_sina() — 检查是否包含CNH")
try:
    import akshare as ak
    # 试试不同货币
    for currency in ["美元", "欧元", "港币"]:
        df = ak.currency_boc_sina(symbol=currency)
        print(f"    {currency}: {len(df)}行, 最新={df.iloc[-1].to_dict() if len(df) > 0 else 'EMPTY'}")
except Exception as e:
    print(f"    [FAIL] {e}")


# ── 2. DXY美元指数现货 (新浪) ──────────────────
print("\n── 2. DXY 美元指数现货 ──")
print("\n  2a: 新浪 DINIW 完整字段解析")
try:
    url = "https://hq.sinajs.cn/list=DINIW"
    resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
    resp.encoding = "gbk"
    raw = resp.text
    print(f"    原始: {raw[:300]}")
    # 解析: var hq_str_DINIW="时间,最新价,昨收,开盘,成交量,最高,最低,..."
    if '="' in raw:
        data_str = raw.split('="')[1].rstrip('";\n')
        fields = data_str.split(",")
        print(f"    字段数: {len(fields)}")
        labels = {
            0: "当前时间",
            1: "最新价",
            2: "昨收",
            3: "今开",
            4: "成交量",
            5: "最高",
            6: "最低",
            7: "未知1",
            8: "名称",
            9: "日期",
        }
        for i, val in enumerate(fields):
            label = labels.get(i, f"字段{i}")
            print(f"    [{i}] {label}: {val}")
except Exception as e:
    print(f"    [FAIL] {e}")


# ── 3. 新浪沪深300/上证综指 正确字段映射 ──────
print("\n── 3. 新浪 A股指数字段映射 ──")
for idx_name, idx_code in [("沪深300", "s_sh000300"), ("上证综指", "s_sh000001")]:
    print(f"\n  3: {idx_name} ({idx_code})")
    try:
        url = f"https://hq.sinajs.cn/list={idx_code}"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        raw = resp.text
        if '="' in raw:
            data_str = raw.split('="')[1].rstrip('";\n')
            fields = data_str.split(",")
            # 新浪A股指数格式: 名称,当前点位,涨跌额,涨跌幅%,成交量(手),成交额(万元)
            print(f"    字段数: {len(fields)}")
            print(f"    名称: {fields[0] if len(fields) > 0 else '?'}")
            print(f"    当前点位: {fields[1] if len(fields) > 1 else '?'}")
            print(f"    涨跌额: {fields[2] if len(fields) > 2 else '?'}")
            print(f"    涨跌幅%: {fields[3] if len(fields) > 3 else '?'}")
            print(f"    成交量(手): {fields[4] if len(fields) > 4 else '?'}")
            print(f"    成交额(万元): {fields[5] if len(fields) > 5 else '?'}")
            if len(fields) > 1:
                price = float(fields[1])
                if idx_name == "沪深300" and 3000 < price < 6000:
                    print(f"    >>> 沪深300实际点位: {price} ✓")
                elif idx_name == "上证综指" and 2000 < price < 5000:
                    print(f"    >>> 上证综指实际点位: {price} ✓")
    except Exception as e:
        print(f"    [FAIL] {e}")


# ── 4. 恒生指数/恒生科技 新浪实时 ───────────────
print("\n── 4. 恒生指数/恒生科技 新浪实时 ──")
hk_codes = [
    ("恒生指数", "int_hangseng"),
    ("恒生科技", "rt_hstec"),  # 可能不同
    ("恒生国企", "int_hscei"),
]
for name, code in hk_codes:
    print(f"\n  4: {name} ({code})")
    try:
        url = f"https://hq.sinajs.cn/list={code}"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        raw = resp.text.strip()
        if raw and '=""' not in raw:
            print(f"    OK: {raw[:200]}")
        else:
            print(f"    EMPTY")
    except Exception as e:
        print(f"    ERROR: {e}")

# 恒生科技更多尝试
print("\n  4b: 恒生科技新浪代码多尝试")
for code in ["rt_hstec", "rt_HSTECH", "hstec", "HSTEC", "int_hstech"]:
    try:
        url = f"https://hq.sinajs.cn/list={code}"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        text = resp.text.strip()
        if text and '=""' not in text:
            print(f"    {code}: OK -> {text[:200]}")
            break
        else:
            print(f"    {code}: EMPTY")
    except Exception as e:
        print(f"    {code}: ERROR")


# ── 5. 美债2年期 ──────────────────────────────
print("\n── 5. 美债2年期替代方案 ──")
# 5a: 尝试 akshare 获取美国国债
print("\n  5a: akshare 尝试美国国债")
try:
    import akshare as ak
    # bond_us_treasury 获取美国国债收益率
    df = ak.bond_zh_us_rate()
    print(f"    行数: {len(df)}, 列: {list(df.columns)}")
    if len(df) > 0:
        print(f"    最新行: {df.iloc[-1].to_dict()}")
        print(f"    前3行:\n{df.head(3)}")
except Exception as e:
    print(f"    [FAIL] {e}")

# 5b: FRED (已有框架，确认DGS2可用)
print("\n  5b: FRED DGS2 测试")
try:
    import urllib.request, urllib.error
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = "series_id=DGS2&api_key=not_set&sort_order=desc&limit=2&file_type=json"
    full_url = f"{url}?{params}"
    req = urllib.request.Request(full_url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode())
    obs = body.get("observations", [])
    for o in obs:
        if o.get("value") != ".":
            print(f"    DGS2: date={o['date']}, value={o['value']}% ✓")
except Exception as e:
    print(f"    [FAIL] {e}")


# ── 6. 新浪港股指数完整解析 ─────────────────
print("\n── 6. 新浪 恒生指数 完整字段解析 ──")
try:
    url = "https://hq.sinajs.cn/list=int_hangseng"
    resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
    resp.encoding = "gbk"
    raw = resp.text
    print(f"    原始: {raw[:500]}")
    if '="' in raw:
        data_str = raw.split('="')[1].rstrip('";\n')
        fields = data_str.split(",")
        print(f"    字段数: {len(fields)}")
        for i, val in enumerate(fields):
            print(f"    [{i}] {val}")
except Exception as e:
    print(f"    [FAIL] {e}")


# ── 7. akshare 港股指数（新浪源，非东方财富） ──
print("\n── 7. akshare 港股指数 (stock_hk_index_daily_sina) ──")
for idx in ["HSI", "HSCEI", "HSTECH"]:
    print(f"\n  7: {idx}")
    try:
        import akshare as ak
        df = ak.stock_hk_index_daily_sina(symbol=idx)
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: {latest.to_dict()}")
    except Exception as e:
        print(f"    [FAIL] {e}")


print("\n\n===== 补充测试完成 =====")
