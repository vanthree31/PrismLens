"""
市场数据模块 v2.0 — 多源架构 + 交叉验证 + 质量报告

职责：
- 获取美股/港股/A股/美债/大宗商品/汇率等实时数据
- 主源: yfinance; 辅助源: FRED (美债)
- 自动交叉验证、新鲜度检查、异常检测
- 每日数据质量报告自动生成
- 缓存机制 + 降级策略

设计原则（用户强制要求）：
  宁愿不显示也不能显示错误数据。
  每个指标必须标注质量等级和置信度。
"""

import json
import logging
import math
import os
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("global_news.market_data")


def _safe_int(env_key: str, default: int) -> int:
    """安全读取环境变量并转换为 int"""
    raw = os.getenv(env_key, "")
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("环境变量 %s='%s' 非有效整数，使用默认值 %d", env_key, raw, default)
        return default


# ── 可重试异常 ──────────────────────────────────
try:
    import requests.exceptions as _req_exc

    RETRYABLE_EXCEPTIONS = (
        ConnectionError,
        TimeoutError,
        OSError,
        _req_exc.ConnectionError,
        _req_exc.Timeout,
        _req_exc.HTTPError,
        _req_exc.ChunkedEncodingError,
    )
except ImportError:
    RETRYABLE_EXCEPTIONS = (ConnectionError, TimeoutError, OSError)


# ── 配置 ────────────────────────────────────────
MARKET_DATA_TIMEOUT = _safe_int("MARKET_DATA_TIMEOUT", 30)
MARKET_DATA_RETRY_COUNT = _safe_int("MARKET_DATA_RETRY_COUNT", 2)
def _get_fred_api_key() -> str:
    """延迟读取 FRED_API_KEY，确保 .env 已加载"""
    return os.getenv("FRED_API_KEY", "")


# ── Sina Finance API（新浪财经，中国数据主源）───
SINA_BASE_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://finance.sina.com.cn",
}
SINA_TIMEOUT = 15

# 新浪符号 → 内部键映射
SINA_SYMBOLS: dict[str, dict] = {
    "sh000001": {"key": "shanghai", "name": "上证综指", "category": "A股", "unit": "点"},
    "sz399300": {"key": "csi300", "name": "沪深300", "category": "A股", "unit": "点"},
    "rt_hkHSI": {"key": "hsi", "name": "恒生指数", "category": "港股", "unit": "点"},
    "rt_hkHSTECH": {"key": "hstech", "name": "恒生科技", "category": "港股", "unit": "点"},
    "fx_susdcnh": {"key": "usdcnh", "name": "离岸人民币(CNH)", "category": "汇率", "unit": ""},
    "fx_susdkrw": {"key": "krwusd", "name": "美元/韩元", "category": "汇率", "unit": ""},
}


def _fetch_sina_realtime(symbols: list[str] | None = None) -> dict[str, dict]:
    """从新浪财经获取实时行情数据。

    返回 {内部key: {price, prev_close, change_pct, ...}}
    """
    if symbols is None:
        symbols = list(SINA_SYMBOLS.keys())

    result: dict[str, dict] = {}
    if not symbols:
        return result

    try:
        import requests as _req

        url = SINA_BASE_URL + ",".join(symbols)
        resp = _req.get(url, headers=SINA_HEADERS, timeout=SINA_TIMEOUT)
        resp.encoding = "gbk"
        text = resp.text
    except Exception as e:
        logger.warning(f"Sina API 请求失败: {e}")
        return result

    for line in text.strip().split("\n"):
        if not line.strip() or "=" not in line:
            continue
        try:
            raw_key, raw_val = line.split("=", 1)
            symbol = raw_key.replace("var hq_str_", "").strip()
            value_str = raw_val.strip().strip('";')

            cfg = SINA_SYMBOLS.get(symbol)
            if not cfg:
                continue

            fields = value_str.split(",")
            if len(fields) < 4:
                continue

            # 判断数据类型：汇率/港股/指数
            # Sina 指数不返回数据时间戳，标注获取时间；非交易时段实际为最近收盘价
            now = datetime.now()
            hour, minute, wd = now.hour, now.minute, now.weekday()
            in_session = wd < 5 and (
                (hour == 9 and minute >= 30)
                or (hour > 9 and hour < 11)
                or (hour == 11 and minute <= 30)
                or (hour >= 13 and hour < 15)
                or (hour == 15 and minute == 0)
            )
            if in_session:
                data_time = now.strftime("%m-%d %H:%M CST")
            else:
                data_time = now.strftime("%m-%d") + " 收盘"

            if symbol.startswith("fx_"):
                # 汇率格式: fields[0]=时间(HH:MM:SS UTC), [1]=最新价, [2]=买入, [3]=卖出,
                #   [4]=成交量, [5]=今开, [6]=最高, [7]=最低, [8]=昨收(实时=最新价, 不可靠),
                #   [9]=名称, [10]=涨跌幅%(Sina原生)
                price = float(fields[1]) if len(fields) > 1 else 0
                # 优先使用 Sina 原生涨跌幅 (fields[10])
                # 非交易时段不用 fields[5](今开)计算涨跌幅，避免失真
                if len(fields) > 10 and fields[10]:
                    try:
                        change_pct = float(fields[10])
                    except ValueError:
                        change_pct = 0
                elif in_session and len(fields) > 5 and fields[5]:
                    # 交易时段用今开价计算日内变动
                    prev_close = float(fields[5])
                    if prev_close == 0:
                        prev_close = price
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
                else:
                    # 非交易时段无 Sina 原生涨跌幅 → 标记为 0
                    change_pct = 0
                # 汇率数据时间: fields[0] 是 HH:MM:SS UTC，补上当日日期
                if fields[0]:
                    today = datetime.now().strftime("%m-%d")
                    data_time = f"{today} {fields[0]} UTC"
            elif symbol.startswith("rt_hk"):
                # 港股指数格式: [0]=代码, [1]=名称, [2]=当前价, [3]=昨收, [4]=今开, [5]=最高, [6]=最低,...
                price = float(fields[2]) if len(fields) > 2 else 0
                prev_close = float(fields[3]) if len(fields) > 3 else price
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
            else:
                # A股指数格式: [0]=名称, [1]=当前价, [2]=昨收, [3]=今开, [4]=最高, [5]=最低,...
                price = float(fields[1]) if len(fields) > 1 else 0
                prev_close = float(fields[2]) if len(fields) > 2 else price
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

            if price <= 0:
                continue

            result[cfg["key"]] = {
                "name": cfg["name"],
                "category": cfg["category"],
                "price": round(price, 4),
                "change_pct": round(change_pct, 2),
                "trend": "↑" if change_pct > 0 else ("↓" if change_pct < 0 else "→"),
                "source": "sina",
                "unit": cfg.get("unit", ""),
                "data_time": data_time,
            }
        except (ValueError, IndexError, KeyError) as e:
            logger.debug(f"Sina 解析失败 {symbol}: {e}")
            continue

    return result


# ── 交叉验证阈值表 ──────────────────────────────
# 同一指标两个数据源的偏差超过此阈值 → 标记为 DISPUTED
CROSS_VALIDATION_THRESHOLDS: dict[str, float] = {
    "sp500": 1.0,
    "dow": 1.0,
    "nasdaq": 1.0,
    "vix": 1.0,
    "russell": 1.5,
    "hsi": 1.5,
    "hstech": 1.5,
    "csi300": 2.0,
    "shanghai": 2.0,
    "kospi": 1.5,
    "nikkei": 1.5,
    "nifty": 2.0,
    "stoxx": 1.5,
    "us2y": 0.10,
    "us5y": 0.15,
    "us10y": 0.15,
    "us30y": 0.15,
    "us3m": 0.10,
    "wti": 1.0,
    "brent": 1.0,
    "natgas": 2.0,
    "gold": 1.0,
    "silver": 1.0,
    "copper": 1.0,
    "aluminum": 1.5,
    "ironore": 2.0,
    "wheat": 2.0,
    "corn": 2.0,
    "dxy": 0.5,
    "eurusd": 0.005,
    "usdjpy": 0.5,
    "krwusd": 5.0,
    "move": 2.0,
    "hyg": 1.0,
    "lqd": 1.0,
    "emeq": 1.0,
    "bund": 1.0,
    "bitcoin": 100.0,
}

# 异常检测: z-score 阈值
ANOMALY_Z_THRESHOLD = float(os.getenv("MARKET_ANOMALY_Z", "3.0"))
ANOMALY_WARN_Z = float(os.getenv("MARKET_ANOMALY_WARN_Z", "2.0"))

# 新鲜度: 数据时间戳必须 < 此阈值（小时）
DATA_FRESHNESS_MAX_HOURS = float(os.getenv("MARKET_FRESHNESS_HOURS", "24"))

# 最小数据点: yfinance 返回数据点少于此数 → 警告
MIN_DATA_POINTS_PER_TICKER = 4


# ── 市场指标配置 (修正版) ───────────────────────
MARKET_TICKERS: dict[str, dict] = {
    # ── 美股指数 ──
    "sp500": {"ticker": "^GSPC", "name": "标普500", "category": "美股", "unit": "点"},
    "dow": {"ticker": "^DJI", "name": "道琼斯", "category": "美股", "unit": "点"},
    "nasdaq": {"ticker": "^IXIC", "name": "纳斯达克", "category": "美股", "unit": "点"},
    "vix": {"ticker": "^VIX", "name": "VIX恐慌指数", "category": "美股", "unit": ""},
    # ── 港股 ──
    "hsi": {
        "ticker": "rt_hkHSI",
        "name": "恒生指数",
        "category": "港股",
        "source": "sina",
        "unit": "点",
    },
    # 注: ^HSTECH 已退市，恒生科技改用 Sina 实时指数
    "hstech": {
        "ticker": "rt_hkHSTECH",
        "name": "恒生科技",
        "category": "港股",
        "source": "sina",
        "unit": "点",
    },
    # ── A股 ──
    "csi300": {
        "ticker": "sz399300",
        "name": "沪深300",
        "category": "A股",
        "source": "sina",
        "unit": "点",
    },
    "shanghai": {
        "ticker": "sh000001",
        "name": "上证综指",
        "category": "A股",
        "source": "sina",
        "unit": "点",
    },
    # ── 亚太 ──
    "kospi": {"ticker": "^KS11", "name": "韩国KOSPI", "category": "亚太股市", "unit": "点"},
    "nikkei": {"ticker": "^N225", "name": "日经225", "category": "亚太股市", "unit": "点"},
    "nifty": {"ticker": "^NSEI", "name": "印度Nifty50", "category": "亚太股市", "unit": "点"},
    # ── 欧洲 ──
    "stoxx": {"ticker": "^STOXX", "name": "欧洲Stoxx600", "category": "欧洲股市", "unit": "点"},
    # ── 美股小盘 ──
    "russell": {"ticker": "^RUT", "name": "罗素2000(小盘)", "category": "美股", "unit": "点"},
    # ── 美债 ──
    "us5y": {
        "ticker": "^FVX",
        "name": "5年期美债收益率",
        "category": "美债",
        "fred_series": "DGS5",
        "unit": "%",
    },
    "us10y": {
        "ticker": "^TNX",
        "name": "10年期美债收益率",
        "category": "美债",
        "fred_series": "DGS10",
        "unit": "%",
    },
    "us30y": {
        "ticker": "^TYX",
        "name": "30年期美债收益率",
        "category": "美债",
        "fred_series": "DGS30",
        "unit": "%",
    },
    "us3m": {
        "ticker": "^IRX",
        "name": "3个月期国库券",
        "category": "美债",
        "fred_series": "DTB3",
        "unit": "%",
    },
    # 注: ^UST2Y 在 yfinance 已退市。2年期美债通过 FRED DGS2 获取。
    "us2y": {
        "ticker": "__fred__",
        "name": "2年期美债收益率",
        "category": "美债",
        "fred_series": "DGS2",
        "fred_only": True,
        "unit": "%",
    },
    # ── 大宗商品 ──
    "wti": {"ticker": "CL=F", "name": "WTI原油", "category": "大宗商品", "unit": "USD/桶"},
    "brent": {"ticker": "BZ=F", "name": "Brent原油", "category": "大宗商品", "unit": "USD/桶"},
    "natgas": {"ticker": "NG=F", "name": "天然气", "category": "大宗商品", "unit": "USD/MMBtu"},
    "gold": {"ticker": "GC=F", "name": "黄金", "category": "大宗商品", "unit": "USD/盎司"},
    "silver": {"ticker": "SI=F", "name": "白银", "category": "大宗商品", "unit": "USD/盎司"},
    "copper": {"ticker": "HG=F", "name": "铜", "category": "大宗商品", "unit": "USD/磅"},
    "aluminum": {"ticker": "ALI=F", "name": "铝", "category": "大宗商品", "unit": "USD/吨"},
    "ironore": {"ticker": "TIO=F", "name": "铁矿石", "category": "大宗商品", "unit": "USD/吨"},
    "wheat": {"ticker": "ZW=F", "name": "小麦", "category": "农产品", "unit": "美分/蒲式耳"},
    "corn": {"ticker": "ZC=F", "name": "玉米", "category": "农产品", "unit": "美分/蒲式耳"},
    # ── 汇率 ──
    "dxy": {"ticker": "DX-Y.NYB", "name": "美元指数", "category": "汇率", "unit": ""},
    "eurusd": {"ticker": "EURUSD=X", "name": "欧元/美元", "category": "汇率", "unit": ""},
    "usdjpy": {"ticker": "JPY=X", "name": "美元/日元", "category": "汇率", "unit": ""},
    "krwusd": {
        "ticker": "fx_susdkrw",
        "name": "美元/韩元",
        "category": "汇率",
        "source": "sina",
        "unit": "",
    },
    "usdcnh": {
        "ticker": "fx_susdcnh",
        "name": "离岸人民币(CNH)",
        "category": "汇率",
        "source": "sina",
        "unit": "",
    },
    # CNH 数据现在从 Sina Finance 获取（实时、完整OHLC）
    # ── 债券波动率 ──
    "move": {"ticker": "^MOVE", "name": "MOVE债券波动率", "category": "波动率", "unit": ""},
    # ── 信用利差 ──
    "hyg": {"ticker": "HYG", "name": "高收益债ETF", "category": "信用", "unit": "USD"},
    "lqd": {"ticker": "LQD", "name": "投资级债ETF", "category": "信用", "unit": "USD"},
    # ── 全球/另类 ──
    "emeq": {"ticker": "EEM", "name": "MSCI新兴市场ETF", "category": "全球市场", "unit": "USD"},
    "bund": {
        "ticker": "IS0L.F",
        "name": "德国国债(ETF代理)",
        "category": "欧洲债券",
        "unit": "EUR",
    },
    "bitcoin": {"ticker": "BTC-USD", "name": "比特币", "category": "另类资产", "unit": "USD"},
    # 注: BDI(波罗的海干散货指数)在yfinance无对应ticker,需Baltic Exchange或tradingeconomics API。
    #      SCFI(集装箱运价指数)需上海航运交易所数据,当前不可用。
}


# ── 内部工具函数 ────────────────────────────────


def _fetch_history_with_timeout(ticker, period: str, timeout: int):
    """线程超时保护调用 ticker.history()"""
    result_box: list = []
    error_box: list = []
    done_event = threading.Event()

    def _target():
        try:
            result_box.append(ticker.history(period=period))
        except Exception as exc:
            error_box.append(exc)
        finally:
            done_event.set()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    if not done_event.wait(timeout=timeout):
        raise TimeoutError(f"获取 {getattr(ticker, 'ticker', '?')} 超时({timeout}s)")
    if error_box:
        raise error_box[0]
    return result_box[0] if result_box else None


def _load_history_prices(key: str, lookback_days: int = 14) -> list[float]:
    """从历史日报文件中加载某指标的近期价格用于异常检测"""
    history_dir = Path(os.getenv("NEWS_OUTPUT_DIR", "data/output")) / ".." / "history"
    # 解析为绝对路径
    if not history_dir.is_absolute():
        history_dir = Path.cwd() / "data" / "history"
    try:
        history_dir = history_dir.resolve()
    except Exception:
        history_dir = Path("data/history")

    prices: list[float] = []
    if not history_dir.exists():
        return prices

    today = datetime.now()
    for i in range(1, lookback_days + 1):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for ext in (".json", "_market.json"):
            fpath = history_dir / f"history_{date_str}{ext}"
            if fpath.exists():
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    continue
                market_data = data.get("market_data", data.get("markets", {}))
                if isinstance(market_data, dict):
                    point = market_data.get(key)
                    if isinstance(point, dict):
                        price = point.get("price")
                        if isinstance(price, (int, float)) and not math.isnan(price):
                            prices.append(float(price))
                    elif isinstance(point, (int, float)):
                        prices.append(float(point))
                break  # 每天只用第一个匹配文件
    return prices


# ── 辅助源: FRED 美债收益率 ──────────────────────


def _fetch_fred_treasury(series_id: str, timeout: int = 15) -> float | None:
    """从 FRED API 获取最新美债收益率（免费，无 API key 限额较高）"""
    import urllib.error
    import urllib.request

    url = "https://api.stlouisfed.org/fred/series/observations"
    params = f"series_id={series_id}&api_key={_get_fred_api_key() or 'not_set'}&sort_order=desc&limit=2&file_type=json"
    full_url = f"{url}?{params}"

    try:
        req = urllib.request.Request(full_url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"FRED {series_id} 请求失败: {e}")
        return None

    observations = body.get("observations", [])
    if not observations:
        logger.debug(f"FRED {series_id} 无数据")
        return None

    for obs in observations:
        val_str = obs.get("value", "")
        if val_str and val_str != ".":
            try:
                return float(val_str)
            except ValueError:
                continue
    return None


# ── 数据质量标签 ──────────────────────────────────


class DataQuality:
    """数据质量等级"""

    VERIFIED = "verified"  # 双源交叉验证通过 (偏差 < 阈值)
    UNVERIFIED = "unverified"  # 仅单源，未交叉验证
    DISPUTED = "disputed"  # 双源偏差超过阈值
    STALE = "stale"  # 数据时间戳 > 24h
    ANOMALY = "anomaly"  # z-score 超过异常阈值
    UNAVAILABLE = "unavailable"  # 所有源均失败


# ── 质量报告 ──────────────────────────────────────


class DataQualityReport:
    """每日市场数据质量报告"""

    def __init__(self):
        self.indicators: dict[str, dict] = {}
        self.generated_at: datetime | None = None
        self.overall: str = "pending"

    def add_indicator(
        self,
        key: str,
        name: str,
        price: float | None,
        quality: str,
        freshness_hours: float | None,
        data_points: int,
        anomaly_z: float | None,
        source_count: int,
        notes: str = "",
    ):
        self.indicators[key] = {
            "name": name,
            "price": price,
            "quality": quality,
            "freshness_hours": round(freshness_hours, 1) if freshness_hours is not None else None,
            "data_points": data_points,
            "anomaly_z": round(anomaly_z, 2) if anomaly_z is not None else None,
            "source_count": source_count,
            "notes": notes,
        }

    def finalize(self):
        """汇总整体质量评估"""
        total = len(self.indicators)
        if total == 0:
            self.overall = "empty"
            return

        verified = sum(1 for v in self.indicators.values() if v["quality"] == DataQuality.VERIFIED)
        disputed = sum(1 for v in self.indicators.values() if v["quality"] == DataQuality.DISPUTED)
        anomaly = sum(1 for v in self.indicators.values() if v["quality"] == DataQuality.ANOMALY)
        stale = sum(1 for v in self.indicators.values() if v["quality"] == DataQuality.STALE)
        unavailable = sum(
            1 for v in self.indicators.values() if v["quality"] == DataQuality.UNAVAILABLE
        )

        bad = disputed + anomaly + stale + unavailable
        if bad == 0 and verified >= total * 0.5:
            self.overall = "excellent"
        elif bad <= 2:
            self.overall = "good"
        elif bad <= 5:
            self.overall = "fair"
        else:
            self.overall = "poor"

        self.generated_at = datetime.now()

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "overall": self.overall,
            "total_indicators": len(self.indicators),
            "by_quality": {
                q: sum(1 for v in self.indicators.values() if v["quality"] == q)
                for q in (
                    DataQuality.VERIFIED,
                    DataQuality.UNVERIFIED,
                    DataQuality.DISPUTED,
                    DataQuality.STALE,
                    DataQuality.ANOMALY,
                    DataQuality.UNAVAILABLE,
                )
            },
            "indicators": self.indicators,
        }

    def to_markdown(self) -> str:
        d = self.to_dict()
        lines = [
            "## 市场数据质量报告",
            "",
            f"**整体评级**: {self.overall.upper()}",
            f"**生成时间**: {d['generated_at']}",
            f"**指标总数**: {d['total_indicators']}",
            "",
            "### 质量分布",
        ]
        quality_labels = {
            DataQuality.VERIFIED: "✅ 已验证",
            DataQuality.UNVERIFIED: "⚠️ 单源未验证",
            DataQuality.DISPUTED: "🔴 双源偏差超标",
            DataQuality.STALE: "🕐 数据过期",
            DataQuality.ANOMALY: "⚡ 异常波动",
            DataQuality.UNAVAILABLE: "❌ 不可用",
        }
        for q, count in d["by_quality"].items():
            if count > 0:
                lines.append(f"- {quality_labels.get(q, q)}: {count}")

        lines.extend(["", "### 逐指标详情", ""])
        lines.append("| 指标 | 最新值 | 质量 | 新鲜度(h) | 数据点 | Z-score | 源数 | 备注 |")
        lines.append("|------|--------|------|-----------|--------|---------|------|------|")
        for _key, info in self.indicators.items():
            price_str = f"{info['price']:.2f}" if info["price"] is not None else "—"
            quality_icon = quality_labels.get(info["quality"], info["quality"])
            freshness = (
                f"{info['freshness_hours']}h" if info["freshness_hours"] is not None else "—"
            )
            dp = str(info["data_points"])
            z = f"{info['anomaly_z']:.1f}σ" if info["anomaly_z"] is not None else "—"
            sc = str(info["source_count"])
            notes = info["notes"][:60] if info["notes"] else "—"
            lines.append(
                f"| {info['name']} | {price_str} | {quality_icon} | {freshness} | {dp} | {z} | {sc} | {notes} |"
            )

        return "\n".join(lines)


# ── 市场数据提供器 v2.0 ──────────────────────────


class MarketDataProvider:
    """市场数据提供器 — 多源架构版

    获取链路：
      1. yfinance (主源) → 所有指标
      2. FRED (辅助源) → 仅美债 (需 FRED_API_KEY)
      3. 交叉验证 → 偏差 vs 阈值表
      4. 新鲜度检查 → 数据时间戳 vs 24h
      5. 异常检测 → z-score vs 14天历史
      6. 质量报告 → 每日自动生成
    """

    def __init__(self, cache_ttl_minutes: int | None = None):
        if cache_ttl_minutes is None:
            cache_ttl_minutes = _safe_int("MARKET_DATA_CACHE_TTL", 30)
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: dict = {}
        self._last_fetch: datetime | None = None
        self._last_report: DataQualityReport | None = None

    # ── 公共 API (保持向后兼容) ──

    def get_market_summary(self) -> str:
        """获取市场数据摘要，供 AI 分析使用"""
        try:
            import yfinance as yf  # noqa: F401
        except ImportError:
            logger.warning("yfinance 未安装")
            return "市场数据暂未接入。"

        if self._is_cache_valid():
            return self._format_cached_data()

        logger.info("获取实时市场数据 (多源架构)...")
        data = self._fetch_all()
        return self._format_data(data)

    def get_data_for_prompt(self) -> str:
        """获取用于 AI prompt 的格式化数据（含质量标注）"""
        summary = self.get_market_summary()
        if "暂无" in summary or "获取失败" in summary:
            return summary

        # 附注质量标记
        if self._last_report:
            flags = []
            for _key, info in self._last_report.indicators.items():
                q = info["quality"]
                if q != DataQuality.VERIFIED and q != DataQuality.UNVERIFIED:
                    flags.append(f"  {info['name']}: {q.upper()} {info['notes']}")
            if flags:
                summary += "\n\n### 数据质量标记\n" + "\n".join(flags)

        return summary

    def get_reference_card(self) -> str:
        """获取结构化参考卡格式"""
        summary = self.get_market_summary()
        if "暂无" in summary or "获取失败" in summary:
            return summary

        data_time = datetime.now().strftime("%m-%d %H:%M")
        lines = [
            f"| 数据键 | 指标名称 | 最新值 | 日变动 | 质量 | 数据时间({data_time}) |",
            "|--------|---------|--------|--------|------|------------------|",
        ]
        if not self._cache:
            return summary

        for key, item in sorted(self._cache.items()):
            name = item.get("name", key)
            price = item.get("price", "—")
            change_pct = item.get("change_pct", 0)
            change_str = f"{'+' if change_pct > 0 else ''}{change_pct}%"
            quality = item.get("quality", DataQuality.UNVERIFIED)
            quality_icon = {
                DataQuality.VERIFIED: "OK",
                DataQuality.UNVERIFIED: "~",
                DataQuality.DISPUTED: "!!",
                DataQuality.STALE: "OLD",
                DataQuality.ANOMALY: "!",
                DataQuality.UNAVAILABLE: "X",
            }.get(quality, "?")
            source = item.get("source", "yf")
            source_label = {"sina": "Sina", "fred": "FRED", "yf": "YF"}.get(source, source)
            lines.append(
                f"| {key} | {name} | {price} | {change_str} | {quality_icon} | {source_label} |"
            )

        return "\n".join(lines)

    def get_quality_report(self) -> DataQualityReport | None:
        """获取最新质量报告"""
        if self._last_report is None:
            # 触发一次数据获取以生成报告
            self.get_market_summary()
        return self._last_report

    # ── 内部: 缓存 ──

    def _is_cache_valid(self) -> bool:
        if not self._cache or not self._last_fetch:
            return False
        return datetime.now() - self._last_fetch < self.cache_ttl

    # ── 内部: 全量获取 ──

    def _fetch_all(self) -> dict:
        """获取所有指标数据，含交叉验证和异常检测"""
        report = DataQualityReport()
        data: dict[str, dict] = {}

        # 第一数据源: Sina Finance（中国A股/港股/CNH/韩元）
        sina_data = _fetch_sina_realtime()
        if sina_data:
            for key, item in sina_data.items():
                data[key] = item
            logger.info(f"  Sina: 获取 {len(sina_data)} 个中国指标")
        else:
            logger.warning("  Sina: 数据获取失败，中国指标将回退到 yfinance")

        # 第二数据源: yfinance（全球指标）
        try:
            import yfinance as yf
        except ImportError:
            self._store_report(report)
            return data

        # 跳过已从 Sina 获取的指标
        yf_tickers = {
            k: v for k, v in MARKET_TICKERS.items() if v.get("source") != "sina" and k not in data
        }
        tickers_str = " ".join(v["ticker"] for v in yf_tickers.values())
        try:
            tickers = yf.Tickers(tickers_str) if tickers_str else None
        except Exception as e:
            logger.warning(f"批量 Tickers() 失败: {e}")
            tickers = None

        for key, config in yf_tickers.items():
            # 跳过 FRED-only 指标（无 yfinance ticker）
            if config.get("fred_only"):
                continue
            item = self._fetch_single(key, config, yf, tickers)
            if item is not None:
                data[key] = item

        # FRED-only 指标（如 us2y）
        for key, config in MARKET_TICKERS.items():
            if config.get("fred_only") and key not in data:
                fred_series = config.get("fred_series")
                if fred_series:
                    val = _fetch_fred_treasury(fred_series)
                    if val is not None:
                        data[key] = {
                            "name": config["name"],
                            "category": config["category"],
                            "price": round(val, 2),
                            "change_pct": 0,
                            "trend": "→",
                            "source": "fred",
                            "unit": config.get("unit", ""),
                            "data_time": datetime.now().strftime("%m-%d"),
                        }

        # 辅助源: FRED (仅美债交叉验证)
        self._cross_validate_fred(data, report)

        # 异常检测
        self._detect_anomalies(data, report)

        # 新鲜度检查
        self._check_freshness(data, report)

        # 汇总质量报告
        for key, item in data.items():
            q = item.get("quality", DataQuality.UNVERIFIED)
            report.add_indicator(
                key=key,
                name=item["name"],
                price=item.get("price"),
                quality=q,
                freshness_hours=item.get("freshness_hours"),
                data_points=item.get("data_points", 0),
                anomaly_z=item.get("anomaly_z"),
                source_count=item.get("source_count", 1),
                notes=item.get("quality_note", ""),
            )

        report.finalize()
        self._last_report = report

        # 更新缓存
        self._cache = data
        self._last_fetch = datetime.now()

        # 记录质量报告到磁盘
        self._save_quality_report(report)

        logger.info(
            f"市场数据获取完成: {len(data)}/{len(MARKET_TICKERS)} 指标, 质量: {report.overall}"
        )
        return data

    def _fetch_single(self, key: str, config: dict, yf, tickers) -> dict | None:
        """从 yfinance 获取单个指标"""
        last_err = None
        for attempt in range(MARKET_DATA_RETRY_COUNT + 1):
            try:
                ticker = tickers.tickers.get(config["ticker"]) if tickers is not None else None
                if ticker is None:
                    ticker = yf.Ticker(config["ticker"])

                hist = _fetch_history_with_timeout(ticker, "5d", MARKET_DATA_TIMEOUT)

                if hist is None or len(hist) == 0:
                    logger.debug(f"{config['name']} 无数据返回")
                    break

                data_points = len(hist)
                current = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2] if len(hist) > 1 else current

                if math.isnan(current) or math.isnan(prev):
                    logger.debug(f"{config['name']} 数据为 NaN（可能停牌）")
                    break

                change_pct = ((current - prev) / prev * 100) if prev != 0 else 0

                # 获取最新数据的时间戳
                freshness_hours: float | None = None
                data_time: str = ""
                try:
                    ts = hist.index[-1]
                    if hasattr(ts, "to_pydatetime"):
                        ts = ts.to_pydatetime()
                    if isinstance(ts, datetime):
                        from datetime import timezone as _tz

                        now_utc = datetime.now(_tz.utc).replace(tzinfo=None)
                        ts_naive = ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
                        freshness_hours = (now_utc - ts_naive).total_seconds() / 3600
                        # 若时分秒全为0，是 daily bar 的日期标记，只显示日期
                        if ts_naive.hour == 0 and ts_naive.minute == 0 and ts_naive.second == 0:
                            data_time = ts_naive.strftime("%m-%d") + " 收盘"
                        else:
                            data_time = ts_naive.strftime("%m-%d %H:%M UTC")
                except Exception:
                    pass

                item = {
                    "name": config["name"],
                    "category": config["category"],
                    "price": round(current, 2),
                    "change_pct": round(change_pct, 2),
                    "trend": "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→",
                    "data_points": data_points,
                    "freshness_hours": freshness_hours,
                    "quality": DataQuality.UNVERIFIED,
                    "source_count": 1,
                    "quality_note": "",
                    "anomaly_z": None,
                    "unit": config.get("unit", ""),
                    "data_time": data_time,
                }

                # 数据点过少警告
                if data_points < MIN_DATA_POINTS_PER_TICKER:
                    item["quality_note"] = (
                        f"仅{data_points}个数据点(需>={MIN_DATA_POINTS_PER_TICKER})"
                    )
                    if data_points <= 1:
                        item["quality"] = DataQuality.UNAVAILABLE

                return item

            except RETRYABLE_EXCEPTIONS as e:
                last_err = e
                if attempt < MARKET_DATA_RETRY_COUNT:
                    logger.debug(f"获取 {config['name']} 第{attempt + 1}次失败，重试: {e}")
                continue
            except Exception as e:
                last_err = e
                logger.debug(f"获取 {config['name']} 非瞬态错误: {e}")
                break

        if last_err is not None:
            if key in ("sp500", "vix", "us10y", "us5y", "us30y"):
                logger.warning(f"获取 {config['name']} 失败: {last_err}")
            else:
                logger.debug(f"获取 {config['name']} 失败: {last_err}")

        return None

    # ── 交叉验证: FRED ──

    def _cross_validate_fred(self, data: dict, report: DataQualityReport) -> None:
        """使用 FRED 数据对美债进行交叉验证"""
        if not _get_fred_api_key():
            # 无 FRED API key，仅标记为单源
            for key in ("us2y", "us3m", "us5y", "us10y", "us30y"):
                if key in data:
                    data[key]["quality_note"] = (
                        data[key].get("quality_note", "") + " 仅单源(yfinance)"
                    )
            return

        for key in ("us2y", "us3m", "us5y", "us10y", "us30y"):
            if key not in data:
                continue

            config = MARKET_TICKERS.get(key, {})
            series_id = config.get("fred_series", "")
            if not series_id:
                continue

            fred_value = _fetch_fred_treasury(series_id)
            if fred_value is None:
                data[key]["quality_note"] = data[key].get("quality_note", "") + " FRED不可用"
                continue

            yf_value = data[key]["price"]
            threshold = CROSS_VALIDATION_THRESHOLDS.get(key, 0.15)

            diff = abs(yf_value - fred_value)
            if diff <= threshold:
                data[key]["quality"] = DataQuality.VERIFIED
                data[key]["source_count"] = 2
                data[key]["quality_note"] = (
                    f"yfinance={yf_value:.2f}, FRED={fred_value:.2f}, diff={diff:.3f}"
                )
            else:
                data[key]["quality"] = DataQuality.DISPUTED
                data[key]["source_count"] = 2
                data[key]["quality_note"] = (
                    f"偏差{diff:.3f}>{threshold:.3f}阈值! "
                    f"yfinance={yf_value:.2f} vs FRED={fred_value:.2f}"
                )
                logger.warning(f"{config['name']} 交叉验证失败: {data[key]['quality_note']}")

    # ── 异常检测 ──

    def _detect_anomalies(self, data: dict, report: DataQualityReport) -> None:
        """基于14天历史价格的 z-score 异常检测"""
        for key, item in data.items():
            history_prices = _load_history_prices(key, lookback_days=14)
            if len(history_prices) < 5:
                item["anomaly_z"] = None
                continue

            current = item["price"]
            mean = statistics.mean(history_prices)
            stdev = statistics.stdev(history_prices) if len(history_prices) >= 2 else 0

            if stdev == 0:
                item["anomaly_z"] = 0.0
                continue

            z = abs(current - mean) / stdev
            item["anomaly_z"] = round(z, 2)

            if z >= ANOMALY_Z_THRESHOLD:
                item["quality"] = DataQuality.ANOMALY
                item["quality_note"] = f"异常波动: {z:.1f}σ vs 14日均值{mean:.2f}±{stdev:.2f}"
                logger.warning(f"{item['name']} 异常波动: {z:.1f}σ")
            elif z >= ANOMALY_WARN_Z:
                if item.get("quality_note"):
                    item["quality_note"] += f" | 波动偏高: {z:.1f}σ"

    # ── 新鲜度检查 ──

    def _check_freshness(self, data: dict, report: DataQualityReport) -> None:
        """检查数据时间戳是否在24h内"""
        for _key, item in data.items():
            fh = item.get("freshness_hours")
            if fh is not None and fh > DATA_FRESHNESS_MAX_HOURS:
                if item["quality"] == DataQuality.UNVERIFIED:
                    item["quality"] = DataQuality.STALE
                # VERIFIED 保持不变（交叉验证过的数据即使稍旧也比单源新鲜数据可靠）
                item["quality_note"] = (
                    item.get("quality_note", "")
                    + f" 数据过期({fh:.0f}h>{DATA_FRESHNESS_MAX_HOURS:.0f}h)"
                )

    # ── 格式化 ──

    def _format_cached_data(self) -> str:
        return self._format_data(self._cache) + "\n\n（数据来自缓存）"

    def _format_data(self, data: dict) -> str:
        if not data:
            return "暂无市场数据。"

        data_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "## 实时市场数据摘要",
            f"_数据获取时间: {data_time} (UTC+8)_\n",
        ]
        quality_icons = {
            DataQuality.VERIFIED: "",
            DataQuality.UNVERIFIED: " ⚠",
            DataQuality.DISPUTED: " 🔴",
            DataQuality.STALE: " 🕐",
            DataQuality.ANOMALY: " ⚡",
            DataQuality.UNAVAILABLE: " ❌",
        }

        categories: dict[str, list] = {}
        for _key, item in data.items():
            cat = item.get("category", "其他")
            categories.setdefault(cat, []).append(item)

        for cat, items in categories.items():
            lines.append(f"### {cat}")
            for item in items:
                trend = item.get("trend", "→")
                change = item.get("change_pct", 0)
                sign = "+" if change > 0 else ""
                qi = quality_icons.get(item.get("quality", ""), "")
                unit = item.get("unit", "")
                unit_str = f" {unit}" if unit else ""
                dt = item.get("data_time", "")
                time_str = f" [{dt}]" if dt else ""
                lines.append(
                    f"- {item['name']}: {item['price']}{unit_str} ({sign}{change}% vs昨收) {trend}{qi}{time_str}"
                )
            lines.append("")

        # 2s10s 利差（优先使用 FRED DGS2 真实2年期，否则用5年期代理）
        if "us10y" in data:
            if "us2y" in data and data["us2y"]["price"] > 0:
                spread = data["us10y"]["price"] - data["us2y"]["price"]
                label = "2s10s利差"
            elif "us5y" in data:
                spread = data["us10y"]["price"] - data["us5y"]["price"]
                label = "2s10s利差(5年期代理)"
            else:
                spread = None
            if spread is not None:
                lines.append("### 关键指标")
                inverted = "（倒挂）" if spread < 0 else ""
                lines.append(f"- {label}: {spread:.2f}% {inverted}")
                # 信用利差 (HYG vs LQD)
                if "hyg" in data and "lqd" in data:
                    credit_spread = ((1 / data["hyg"]["price"]) - (1 / data["lqd"]["price"])) * 100
                    lines.append(f"- 信用利差(HYG-LQD): {credit_spread:.2f}%")
                lines.append("")

        return "\n".join(lines)

    # ── 质量报告持久化 ──

    def _save_quality_report(self, report: DataQualityReport) -> None:
        """将每日质量报告保存到磁盘"""
        try:
            from src.utils import get_output_dir

            output_dir = get_output_dir()
        except Exception:
            output_dir = Path("data/output")
        report_dir = output_dir / "quality"
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        report_path = report_dir / f"market_quality_{today}.json"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"质量报告已保存: {report_path}")
        except OSError as e:
            logger.warning(f"保存质量报告失败: {e}")

        # 同时保存 Markdown 版本
        md_path = report_dir / f"market_quality_{today}.md"
        try:
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(report.to_markdown())
        except OSError:
            pass


# ── 全局单例 ────────────────────────────────────

_market_provider: MarketDataProvider | None = None
_provider_lock = threading.Lock()


def get_market_provider() -> MarketDataProvider:
    """获取市场数据提供器单例（线程安全）"""
    global _market_provider
    if _market_provider is None:
        with _provider_lock:
            if _market_provider is None:
                _market_provider = MarketDataProvider()
    return _market_provider
