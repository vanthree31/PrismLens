"""
测试脚本: 评估中国特有市场数据的多数据源方案
指标: 沪深300, 上证综指, CNH离岸人民币, 恒生科技, DXY美元指数, 商品期货, 美债2年期
数据源: akshare, 新浪API, 东方财富API, ExchangeRate-API
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 强制 UTF-8 输出，解决 Windows GBK 编码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
import pandas as pd


def header(s: str):
    print(f"\n{'='*60}")
    print(f"  {s}")
    print(f"{'='*60}")


def subtest(name: str):
    print(f"\n  >>> {name}")


def ok(msg: str):
    print(f"    [PASS] {msg}")


def warn(msg: str):
    print(f"    [WARN] {msg}")


def fail(msg: str):
    print(f"    [FAIL] {msg}")


# ═══════════════════════════════════════════════════════════
# PART 1: akshare — 中国金融数据（最核心方案）
# ═══════════════════════════════════════════════════════════

def test_akshare_csi300():
    """沪深300指数 — akshare stock_zh_index_daily()"""
    header("A1: akshare 沪深300 (stock_zh_index_daily)")
    try:
        import akshare as ak
        # 沪深300 代码: sh000300
        subtest("沪深300日线 (sh000300)")
        df = ak.stock_zh_index_daily(symbol="sh000300")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: date={latest.get('date','?')}, close={latest.get('close','?')}")
            # 检查值是否合理 (>3000)
            if float(latest.get('close', 0)) > 3000:
                ok(f"值合理: {latest['close']}")
            else:
                warn(f"值异常: {latest.get('close')}")
            return True, float(latest['close']), len(df)
        else:
            fail("无数据返回")
            return False, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


def test_akshare_shanghai():
    """上证综指 — akshare"""
    header("A2: akshare 上证综指 (stock_zh_index_daily)")
    try:
        import akshare as ak
        subtest("上证综指日线 (sh000001)")
        df = ak.stock_zh_index_daily(symbol="sh000001")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: date={latest.get('date','?')}, close={latest.get('close','?')}")
            if float(latest.get('close', 0)) > 2000:
                ok(f"值合理: {latest['close']}")
            else:
                warn(f"值异常: {latest.get('close')}")
            return True, float(latest['close']), len(df)
        else:
            fail("无数据返回")
            return False, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


def test_akshare_hstech():
    """恒生科技指数 — akshare stock_hk_index_daily()"""
    header("A3: akshare 恒生科技 (stock_hk_index_daily_em)")
    try:
        import akshare as ak
        subtest("恒生科技日线 (东方财富接口)")
        df = ak.stock_hk_index_daily_em(symbol="HSTECH")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: date={latest.get('date','?')}, close={latest.get('close','?')}")
            if float(latest.get('close', 0)) > 1000:
                ok(f"值合理: {latest['close']}")
            else:
                warn(f"值异常: {latest.get('close')}")
            return True, float(latest['close']), len(df)
        else:
            fail("无数据返回")
            return False, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


def test_akshare_hsi():
    """恒生指数 — akshare"""
    header("A4: akshare 恒生指数")
    try:
        import akshare as ak
        subtest("恒生指数日线 (HSI)")
        df = ak.stock_hk_index_daily_em(symbol="HSI")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: date={latest.get('date','?')}, close={latest.get('close','?')}")
            ok(f"值: {latest['close']}")
            return True, float(latest['close']), len(df)
        else:
            fail("无数据返回")
            return False, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


def test_akshare_cnh():
    """CNH离岸人民币 — akshare currency_boc_sina()"""
    header("A5: akshare CNH离岸人民币")
    try:
        import akshare as ak
        # 方法1: 外汇牌价
        subtest("方法1: currency_boc_sina() 美元/人民币")
        df = ak.currency_boc_sina(symbol="美元")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: date={latest.get('日期','?')}, 现汇卖出={latest.get('现汇卖出','?')}")
            ok(f"在岸人民币参考: {latest.get('现汇卖出')}")
        else:
            warn("currency_boc_sina 无数据")
    except Exception as e:
        warn(f"currency_boc_sina 异常: {e}")

    # 方法2: 外汇即期
    try:
        subtest("方法2: fx_spot_quote() 尝试")
        import akshare as ak
        df2 = ak.fx_spot_quote()
        if df2 is not None and len(df2) > 0:
            print(f"    行数: {len(df2)}, 列: {list(df2.columns)}")
            print(f"    前3行:\n{df2.head(3)}")
            # 找USD/CNH
            cnh_rows = df2[df2.astype(str).apply(lambda r: r.str.contains('CNH|CNY|人民币|美元', case=False)).any(axis=1)]
            if len(cnh_rows) > 0:
                print(f"    相关行:\n{cnh_rows}")
                ok("找到CNH/CNY数据")
                return True, None, -1
            else:
                warn("未找到CNH/CNY匹配")
        else:
            warn("fx_spot_quote 无数据")
    except Exception as e:
        warn(f"fx_spot_quote 异常: {e}")

    # 方法3: 尝试 currency_hist 获取历史
    try:
        subtest("方法3: currency_hist() 美元兑离岸人民币")
        import akshare as ak
        df3 = ak.currency_hist(symbol="usdcnh")
        print(f"    行数: {len(df3)}, 列: {list(df3.columns)}")
        if len(df3) > 0:
            latest = df3.iloc[-1]
            print(f"    最新: {latest.to_dict()}")
            ok(f"USDCNH: {latest.get('close', latest.iloc[-1])}")
            return True, float(latest.get('close', latest.iloc[-1])), len(df3)
    except Exception as e:
        warn(f"currency_hist 异常: {e}")

    return False, None, 0


def test_akshare_commodities():
    """商品期货 — akshare futures_zh_spot()"""
    header("A6: akshare 中国商品期货")
    try:
        import akshare as ak
        subtest("futures_zh_spot() 实时行情")
        df = ak.futures_zh_spot()
        print(f"    品种数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            # 找铁矿石、铝
            targets = ['铁矿', '铁矿石', '沪铝', '铝', '螺纹', '热卷']
            for t in targets:
                rows = df[df.astype(str).apply(lambda r: r.str.contains(t, case=False)).any(axis=1)]
                if len(rows) > 0:
                    print(f"    [{t}] 找到 {len(rows)} 条:")
                    for _, r in rows.iterrows():
                        print(f"      {r.get('name', r.get('symbol', '?'))}: price={r.get('price', r.get('last_price', '?'))}")
                else:
                    warn(f"    [{t}] 未找到")
        return True, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


def test_akshare_ironore():
    """铁矿石期货详细"""
    header("A7: akshare 铁矿石期货详细 (futures_main_sina)")
    try:
        import akshare as ak
        subtest("futures_zh_daily_sina(symbol='I0') 铁矿石连续")
        df = ak.futures_zh_daily_sina(symbol="I0")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: {latest.to_dict()}")
            ok(f"铁矿石(大连商品交易所): close={latest.get('close','?')}")
            return True, float(latest.get('close', 0)), len(df)
        return False, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


def test_akshare_aluminum():
    """铝期货详细"""
    header("A8: akshare 铝期货详细 (futures_main_sina)")
    try:
        import akshare as ak
        subtest("futures_zh_daily_sina(symbol='AL0') 沪铝连续")
        df = ak.futures_zh_daily_sina(symbol="AL0")
        print(f"    行数: {len(df)}, 列: {list(df.columns)}")
        if len(df) > 0:
            latest = df.iloc[-1]
            print(f"    最新: {latest.to_dict()}")
            ok(f"铝(上期所): close={latest.get('close','?')}")
            return True, float(latest.get('close', 0)), len(df)
        return False, None, 0
    except Exception as e:
        fail(f"异常: {e}")
        return False, None, 0


# ═══════════════════════════════════════════════════════════
# PART 2: 新浪财经API (hq.sinajs.cn) — 实时行情
# ═══════════════════════════════════════════════════════════

SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def test_sina_dxy():
    """美元指数现货 — 新浪API"""
    header("S1: 新浪API DXY美元指数现货")
    try:
        # DINIW 为美元指数现货代码 (不同于期货DX-Y.NYB)
        url = "https://hq.sinajs.cn/list=DINIW"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        print(f"    状态码: {resp.status_code}")
        print(f"    原始: {resp.text[:300]}")
        if resp.text and '=""' not in resp.text:
            ok("DXY现货数据可用")
            return True
        else:
            warn("DXY现货无数据或空响应")
            return False
    except Exception as e:
        fail(f"异常: {e}")
        return False


def test_sina_csi300():
    """沪深300实时 — 新浪API"""
    header("S2: 新浪API 沪深300实时")
    try:
        url = "https://hq.sinajs.cn/list=s_sh000300"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        print(f"    状态码: {resp.status_code}")
        print(f"    原始: {resp.text[:300]}")
        if resp.text and '=""' not in resp.text:
            ok("沪深300实时数据可用")
            return True
        else:
            warn("沪深300无数据")
            return False
    except Exception as e:
        fail(f"异常: {e}")
        return False


def test_sina_shanghai():
    """上证综指实时 — 新浪API"""
    header("S3: 新浪API 上证综指实时")
    try:
        url = "https://hq.sinajs.cn/list=s_sh000001"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        print(f"    状态码: {resp.status_code}")
        print(f"    原始: {resp.text[:300]}")
        if resp.text and '=""' not in resp.text:
            ok("上证综指实时数据可用")
            return True
        else:
            warn("上证综指无数据")
            return False
    except Exception as e:
        fail(f"异常: {e}")
        return False


def test_sina_cnh():
    """CNH汇率 — 新浪API"""
    header("S4: 新浪API CNH离岸人民币")
    try:
        # USDCNH 代码
        url = "https://hq.sinajs.cn/list=USDCNH"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        print(f"    状态码: {resp.status_code}")
        print(f"    原始: {resp.text[:300]}")
        if resp.text and '=""' not in resp.text:
            ok("CNH数据可用")
            return True
        else:
            warn("USDCNH无数据")
    except Exception as e:
        fail(f"异常: {e}")
    return False


# ═══════════════════════════════════════════════════════════
# PART 3: 东方财富API (push2.eastmoney.com)
# ═══════════════════════════════════════════════════════════

EM_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def test_eastmoney_dxy():
    """东方财富 DXY美元指数"""
    header("E1: 东方财富API DXY美元指数")
    try:
        # 东方财富外汇页面: https://quote.eastmoney.com/gb/{code}.html
        # DXY美元指数代码: 105.UDI
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "105.UDI",  # 美元指数
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
        data = resp.json()
        print(f"    状态码: {resp.status_code}")
        print(f"    响应: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}")
        if data.get("data"):
            d = data["data"]
            price = d.get("f43", 0) / 100  # 这是当前价
            change_pct = d.get("f170", 0) / 100
            print(f"    DXY: {price}, 涨跌: {change_pct}%")
            if 80 < price < 120:
                ok(f"DXY现货价格合理: {price}")
                return True, price
            else:
                warn(f"DXY价格异常: {price}")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_eastmoney_csi300():
    """东方财富 沪深300"""
    header("E2: 东方财富API 沪深300")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "1.000300",
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
        data = resp.json()
        print(f"    状态码: {resp.status_code}")
        d = data.get("data", {})
        if d:
            price = d.get("f43", 0) / 100
            change_pct = d.get("f170", 0) / 100
            print(f"    沪深300: {price}, 涨跌: {change_pct}%")
            if 2500 < price < 6000:
                ok(f"沪深300实际指数点位: {price} ✓")
                return True, price
            else:
                warn(f"值异常: {price}")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_eastmoney_shanghai():
    """东方财富 上证综指"""
    header("E3: 东方财富API 上证综指")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "1.000001",
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
        data = resp.json()
        print(f"    状态码: {resp.status_code}")
        d = data.get("data", {})
        if d:
            price = d.get("f43", 0) / 100
            change_pct = d.get("f170", 0) / 100
            print(f"    上证综指: {price}, 涨跌: {change_pct}%")
            if 2000 < price < 5000:
                ok(f"上证综指实际指数点位: {price} ✓")
                return True, price
            else:
                warn(f"值异常: {price}")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_eastmoney_hstech():
    """东方财富 恒生科技"""
    header("E4: 东方财富API 恒生科技")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "124.HSTECH",
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
        data = resp.json()
        print(f"    状态码: {resp.status_code}")
        d = data.get("data", {})
        if d:
            price = d.get("f43", 0) / 100
            change_pct = d.get("f170", 0) / 100
            print(f"    恒生科技: {price}, 涨跌: {change_pct}%")
            if 1000 < price < 8000:
                ok(f"恒生科技实际指数: {price} ✓")
                return True, price
            else:
                warn(f"值异常: {price}")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_eastmoney_cnh():
    """东方财富 CNH汇率"""
    header("E5: 东方财富API CNH离岸人民币")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "133.USDCNH",
            "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
        data = resp.json()
        print(f"    状态码: {resp.status_code}")
        d = data.get("data", {})
        if d:
            price = d.get("f43", 0) / 100
            change_pct = d.get("f170", 0) / 100
            print(f"    USDCNH: {price}, 涨跌: {change_pct}%")
            if 6.5 < price < 8.0:
                ok(f"CNH价格合理: {price} ✓")
                return True, price
            else:
                warn(f"CNH值异常: {price}")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_eastmoney_ironore():
    """东方财富 铁矿石期货"""
    header("E6: 东方财富API 铁矿石期货")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        # 大商所铁矿石主力: 113.I
        # 尝试不同代码格式
        codes = ["113.IM0", "113.I0", "113.i2209", "113.IM"]
        for code in codes:
            subtest(f"尝试代码: {code}")
            params = {
                "secid": code,
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            }
            resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
            data = resp.json()
            d = data.get("data", {})
            if d and d.get("f43", 0) != 0:
                price = d.get("f43", 0) / 100
                print(f"    铁矿石价格: {price}")
                if 300 < price < 1500:
                    ok(f"铁矿石价格合理: {price}")
                    return True, price
        warn("所有铁矿石代码均失败")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_eastmoney_aluminum():
    """东方财富 沪铝期货"""
    header("E7: 东方财富API 沪铝期货")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        codes = ["118.AL0", "118.ALM0", "118.AL"]
        for code in codes:
            subtest(f"尝试代码: {code}")
            params = {
                "secid": code,
                "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f107,f116,f117,f162,f167,f168,f169,f170,f171",
                "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            }
            resp = requests.get(url, params=params, headers=EM_HEADERS, timeout=10)
            data = resp.json()
            d = data.get("data", {})
            if d and d.get("f43", 0) != 0:
                price = d.get("f43", 0) / 100
                print(f"    沪铝价格: {price}")
                if 10000 < price < 30000:
                    ok(f"沪铝价格合理: {price}")
                    return True, price
        warn("所有沪铝代码均失败")
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


# ═══════════════════════════════════════════════════════════
# PART 4: ExchangeRate-API (免费汇率)
# ═══════════════════════════════════════════════════════════

def test_exchangerate_api():
    """ExchangeRate-API (免费层: 1500次/月)"""
    header("X1: ExchangeRate-API (USD→CNY)")
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        cny = data.get("rates", {}).get("CNY")
        print(f"    USD→CNY: {cny}")
        if cny and 6.5 < cny < 8.0:
            ok(f"汇率合理: {cny}")
            return True, cny
        else:
            warn(f"汇率异常: {cny}")
            return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


# ═══════════════════════════════════════════════════════════
# PART 5: 新浪API 实时行情解析测试
# ═══════════════════════════════════════════════════════════

def test_sina_parse_csi300_full():
    """新浪沪深300完整数据解析"""
    header("S5: 新浪沪深300 实时数据解析")
    try:
        url = "https://hq.sinajs.cn/list=s_sh000300"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        raw = resp.text
        print(f"    原始: {raw[:500]}")

        # 解析: var hq_str_s_sh000300="名称,今开,昨收,现价,最高,最低,..."
        if '="' in raw:
            data_str = raw.split('="')[1].rstrip('";\n')
            fields = data_str.split(",")
            print(f"    字段数: {len(fields)}")
            labels = ["名称", "今开", "昨收", "现价", "最高", "最低"]
            for i, (label, val) in enumerate(zip(labels, fields)):
                print(f"    [{i}] {label}: {val}")
            if len(fields) >= 4:
                price = float(fields[3])
                if price > 3000:
                    ok(f"沪深300实时价: {price} ✓")
                    return True, price
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


def test_sina_parse_cnh_full():
    """新浪 CNH 完整数据解析"""
    header("S6: 新浪 USDCNH 实时数据解析")
    try:
        url = "https://hq.sinajs.cn/list=USDCNH"
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
        raw = resp.text
        print(f"    原始: {raw[:500]}")

        if '="' in raw:
            data_str = raw.split('="')[1].rstrip('";\n')
            fields = data_str.split(",")
            print(f"    字段数: {len(fields)}")
            print(f"    所有字段: {fields}")
            if len(fields) >= 2:
                ok(f"USDCNH: bid/ask 可用, fields={fields[:5]}")
                return True, None
        return False, None
    except Exception as e:
        fail(f"异常: {e}")
        return False, None


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  中国特有市场数据 多源方案测试")
    print(f"  时间: {datetime.now().isoformat()}")
    print("=" * 60)

    results = {}

    # --- akshare 测试 ---
    results["csi300_ak"] = test_akshare_csi300()
    results["shanghai_ak"] = test_akshare_shanghai()
    results["hstech_ak"] = test_akshare_hstech()
    results["hsi_ak"] = test_akshare_hsi()
    results["cnh_ak"] = test_akshare_cnh()
    results["commodities_ak"] = test_akshare_commodities()
    results["ironore_ak"] = test_akshare_ironore()
    results["aluminum_ak"] = test_akshare_aluminum()

    # --- 新浪API 测试 ---
    test_sina_dxy()
    test_sina_csi300()
    test_sina_shanghai()
    test_sina_cnh()
    results["csi300_sina"] = test_sina_parse_csi300_full()
    results["cnh_sina"] = test_sina_parse_cnh_full()

    # --- 东方财富API 测试 ---
    results["dxy_em"] = test_eastmoney_dxy()
    results["csi300_em"] = test_eastmoney_csi300()
    results["shanghai_em"] = test_eastmoney_shanghai()
    results["hstech_em"] = test_eastmoney_hstech()
    results["cnh_em"] = test_eastmoney_cnh()
    results["ironore_em"] = test_eastmoney_ironore()
    results["aluminum_em"] = test_eastmoney_aluminum()

    # --- ExchangeRate-API ---
    results["usdcny_exr"] = test_exchangerate_api()

    # --- 汇总 ---
    print("\n\n")
    print("=" * 60)
    print("  汇总报告")
    print("=" * 60)

    for name, r in results.items():
        if isinstance(r, tuple) and len(r) >= 1:
            status = "[PASS]" if r[0] else "[FAIL]"
            extra = ""
            if len(r) >= 2 and r[1] is not None:
                extra = f" value={r[1]}"
            if len(r) >= 3 and r[2] and r[2] > 0:
                extra += f" rows={r[2]}"
            print(f"  {status} | {name}{extra}")
        else:
            print(f"  ❓ ???? | {name}")

    print("\n测试完成。")
