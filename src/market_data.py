"""
市场数据模块 — 通过 yfinance 获取实时行情

职责：
- 获取美股/港股/A股/美债/大宗商品/汇率等实时数据
- 提供格式化的市场数据摘要供AI分析使用
- 缓存机制避免重复请求
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
import json

logger = logging.getLogger("global_news.market_data")

# 市场数据配置
MARKET_TICKERS = {
    # 美股指数
    "sp500": {"ticker": "^GSPC", "name": "标普500", "category": "美股"},
    "dow": {"ticker": "^DJI", "name": "道琼斯", "category": "美股"},
    "nasdaq": {"ticker": "^IXIC", "name": "纳斯达克", "category": "美股"},
    "vix": {"ticker": "^VIX", "name": "VIX恐慌指数", "category": "美股"},

    # 港股
    "hsi": {"ticker": "^HSI", "name": "恒生指数", "category": "港股"},
    "hstech": {"ticker": "^HSTECH", "name": "恒生科技", "category": "港股"},

    # A股
    "csi300": {"ticker": "000300.SS", "name": "沪深300", "category": "A股"},
    "shanghai": {"ticker": "000001.SS", "name": "上证综指", "category": "A股"},

    # 美债收益率
    "us2y": {"ticker": "^IRX", "name": "2年期美债收益率", "category": "美债"},
    "us10y": {"ticker": "^TNX", "name": "10年期美债收益率", "category": "美债"},
    "us30y": {"ticker": "^TYX", "name": "30年期美债收益率", "category": "美债"},

    # 大宗商品
    "wti": {"ticker": "CL=F", "name": "WTI原油", "category": "大宗商品"},
    "brent": {"ticker": "BZ=F", "name": "Brent原油", "category": "大宗商品"},
    "gold": {"ticker": "GC=F", "name": "黄金", "category": "大宗商品"},
    "copper": {"ticker": "HG=F", "name": "铜", "category": "大宗商品"},

    # 汇率
    "dxy": {"ticker": "DX-Y.NYB", "name": "美元指数", "category": "汇率"},
    "usdcnh": {"ticker": "CNH=X", "name": "离岸人民币", "category": "汇率"},
}


class MarketDataProvider:
    """市场数据提供器"""

    def __init__(self, cache_ttl_minutes: int = 30):
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: dict = {}
        self._last_fetch: Optional[datetime] = None

    def get_market_summary(self) -> str:
        """获取市场数据摘要，供AI分析使用"""
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 未安装，跳过市场数据获取")
            return "市场数据暂未接入。"

        # 检查缓存
        if self._is_cache_valid():
            return self._format_cached_data()

        logger.info("获取实时市场数据...")
        data = {}

        # 批量获取数据
        tickers_str = " ".join(v["ticker"] for v in MARKET_TICKERS.values())
        try:
            import yfinance as yf
            tickers = yf.Tickers(tickers_str)

            for key, config in MARKET_TICKERS.items():
                try:
                    ticker = tickers.tickers.get(config["ticker"])
                    if ticker is None:
                        # 尝试单独获取
                        ticker = yf.Ticker(config["ticker"])

                    info = ticker.fast_info
                    hist = ticker.history(period="5d")

                    if hist is not None and len(hist) > 0:
                        current = hist['Close'].iloc[-1]
                        prev = hist['Close'].iloc[-2] if len(hist) > 1 else current
                        change_pct = ((current - prev) / prev * 100) if prev != 0 else 0

                        data[key] = {
                            "name": config["name"],
                            "category": config["category"],
                            "price": round(current, 2),
                            "change_pct": round(change_pct, 2),
                            "trend": "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"
                        }
                except Exception as e:
                    logger.debug(f"获取 {config['name']} 失败: {e}")
                    continue

            # 更新缓存
            self._cache = data
            self._last_fetch = datetime.now()

            logger.info(f"市场数据获取完成: {len(data)}/{len(MARKET_TICKERS)} 个指标")

        except Exception as e:
            logger.error(f"批量获取市场数据失败: {e}")
            if self._cache:
                return self._format_cached_data()
            return "市场数据获取失败。"

        return self._format_data(data)

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if not self._cache or not self._last_fetch:
            return False
        return datetime.now() - self._last_fetch < self.cache_ttl

    def _format_cached_data(self) -> str:
        """格式化缓存数据"""
        return self._format_data(self._cache) + "\n\n（数据来自缓存）"

    def _format_data(self, data: dict) -> str:
        """格式化市场数据为文本"""
        if not data:
            return "暂无市场数据。"

        lines = ["## 实时市场数据摘要\n"]

        # 按类别分组
        categories = {}
        for key, item in data.items():
            cat = item.get("category", "其他")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(item)

        for cat, items in categories.items():
            lines.append(f"### {cat}")
            for item in items:
                trend = item.get("trend", "→")
                change = item.get("change_pct", 0)
                sign = "+" if change > 0 else ""
                lines.append(f"- {item['name']}: {item['price']} ({sign}{change}%) {trend}")
            lines.append("")

        # 计算2s10s利差（如果有数据）
        if "us2y" in data and "us10y" in data:
            spread = data["us10y"]["price"] - data["us2y"]["price"]
            lines.append(f"### 关键指标")
            lines.append(f"- 2s10s利差: {spread:.2f}% {'（倒挂）' if spread < 0 else ''}")
            lines.append("")

        return "\n".join(lines)

    def get_data_for_prompt(self) -> str:
        """获取用于AI prompt的格式化数据"""
        return self.get_market_summary()


# 全局实例
_market_provider: Optional[MarketDataProvider] = None


def get_market_provider() -> MarketDataProvider:
    """获取市场数据提供器单例"""
    global _market_provider
    if _market_provider is None:
        _market_provider = MarketDataProvider()
    return _market_provider
