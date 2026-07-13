"""
新闻抓取模块

支持:
- RSS 源抓取 (feedparser)
- 网页抓取 (requests + BeautifulSoup)
- 代理、重试、超时
- 双层过滤（代码过滤 + AI 过滤预留）
- 重要性评分与排序
"""

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime

import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils import (
    load_importance_config,
    load_sources_config,
    normalize_url,
)


def _is_retryable_http_error(retry_state) -> bool:
    """判断 HTTP 错误是否应重试：ConnectionError、Timeout、HTTP 429/5xx"""
    if retry_state.outcome is None:
        return False
    exc = retry_state.outcome.exception()
    if exc is None:
        return False
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        try:
            status = exc.response.status_code
            return status == 429 or status >= 500
        except AttributeError:
            return False
    return False


logger = logging.getLogger("global_news.fetcher")


# ─────────────────────────────────────────────────
# 信任门控 — 新闻源分级（借鉴 trust_gated_agent_team）
# ─────────────────────────────────────────────────

# Gold (60-100): 官方通讯社、顶级金融媒体 — 全文引用
# Silver (40-59): 主流媒体、区域权威 — 需交叉验证
# Bronze (20-39): 国家媒体、小众来源 — 标记 warning
# None (0-19): 社交媒体、匿名来源 — 默认过滤

SOURCE_TRUST_TIERS: dict[str, dict] = {
    # Gold Tier
    "Reuters": {"tier": "gold", "score": 90},
    "Bloomberg": {"tier": "gold", "score": 90},
    "New York Times": {"tier": "gold", "score": 85},
    "Financial Times": {"tier": "gold", "score": 85},
    "Wall Street Journal": {"tier": "gold", "score": 85},
    "BBC": {"tier": "gold", "score": 80},
    "AP": {"tier": "gold", "score": 85},
    # Silver Tier
    "CNN": {"tier": "silver", "score": 55},
    "The Guardian": {"tier": "silver", "score": 60},
    "CNBC": {"tier": "silver", "score": 60},
    "Nikkei Asia": {"tier": "silver", "score": 60},
    "Al Jazeera": {"tier": "silver", "score": 55},
    "SCMP": {"tier": "silver", "score": 55},
    "The Hindu": {"tier": "silver", "score": 55},
    "Yonhap": {"tier": "silver", "score": 55},
    "Japan Times": {"tier": "silver", "score": 55},
    "Deutsche Welle": {"tier": "silver", "score": 55},
    "France 24": {"tier": "silver", "score": 55},
    "ABC Australia": {"tier": "silver", "score": 55},
    # Bronze Tier
    "RT": {"tier": "bronze", "score": 30},
    "Sputnik": {"tier": "bronze", "score": 25},
    "TASS": {"tier": "bronze", "score": 30},
    "新华社英文版": {"tier": "bronze", "score": 35},
    "CGTN": {"tier": "bronze", "score": 30},
    "环球时报英文版": {"tier": "bronze", "score": 30},
    "Press TV": {"tier": "bronze", "score": 20},
    "ZeroHedge": {"tier": "bronze", "score": 20},
}


def get_source_trust(source_name: str) -> dict:
    """获取新闻源的信任等级"""
    for name, info in SOURCE_TRUST_TIERS.items():
        if name.lower() in source_name.lower():
            return info
    return {"tier": "unrated", "score": 40}


# ─────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────


@dataclass
class NewsItem:
    """单条新闻数据"""

    title: str
    url: str
    published: str
    source: str
    region: str
    category: str
    summary: str = ""
    importance_score: float = 0.0
    coverage_count: int = 1  # 被多少个源报道

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────
# 关键词过滤（从配置文件加载）
# ─────────────────────────────────────────────────


def _load_keywords_config() -> dict:
    """加载 importance_keywords.yaml 配置（带缓存）"""
    try:
        return load_importance_config()
    except Exception as e:
        logger.warning(f"加载 importance_keywords.yaml 失败: {e}")
        return {}


def _load_filter_keywords(config: dict | None = None) -> list[str]:
    """从 importance_keywords.yaml 加载过滤词（含噪音黑名单）"""
    if config is None:
        config = _load_keywords_config()
    base = config.get(
        "negative_filter", ["celebrity", "football", "soccer", "basketball", "NFL", "NBA"]
    )
    # 内置噪音黑名单（借鉴 scout.py 的 NOISE_WORDS 模式）
    builtin_noise = [
        "hiring",
        "freelance",
        "internship",
        "job opening",
        "weather forecast",
        "lottery",
        "recipe",
        "diet tip",
        "celebrity gossip",
        "royal baby",
        "award show",
        "click here",
        "sponsored",
        "advertorial",
        "promoted",
        "top 10",
        "you won't believe",
        "shocking",
        "viral video",
        "funny",
        "cute puppy",
        "cat video",
    ]
    return list(set(base + builtin_noise))


def _load_priority_keywords(config: dict | None = None) -> list[str]:
    """从 importance_keywords.yaml 加载优先词"""
    if config is None:
        config = _load_keywords_config()
    return config.get(
        "priority_keywords",
        [
            "international",
            "global",
            "military",
            "sanction",
            "crisis",
            "war",
            "国际",
            "外交",
            "军事",
            "制裁",
            "危机",
        ],
    )


# 模块级加载一次配置，避免重复磁盘读取
_keywords_config = _load_keywords_config()
FILTER_KEYWORDS = _load_filter_keywords(_keywords_config)
PRIORITY_KEYWORDS = _load_priority_keywords(_keywords_config)


def is_relevant_news(title: str, summary: str = "") -> bool:
    """
    第一层：代码过滤

    判断新闻是否与国际局势相关。
    返回 True 表示保留，False 表示过滤掉。
    """
    # 防止 None 被转为字面量 'None'
    title = title or ""
    summary = summary or ""
    text = f"{title} {summary}".lower()

    # 检查是否包含过滤关键词
    for keyword in FILTER_KEYWORDS:
        if keyword.lower() in text:
            # 但如果有优先关键词，仍然保留
            has_priority = any(pk.lower() in text for pk in PRIORITY_KEYWORDS)
            if not has_priority:
                return False

    return True


# ─────────────────────────────────────────────────
# 重要性评分
# ─────────────────────────────────────────────────


class ImportanceScorer:
    """
    新闻重要性评分器 v2.0

    支持:
    - 关键词 + 语义别名匹配
    - 信号组合升级（escalation_rules）
    - 确认等级乘数（confirmation_level）
    - 信号时间衰减（signal_decay）
    - 高权重媒体加成
    - 多源覆盖加成
    - 风险主题匹配
    """

    def __init__(self):
        self.config = load_importance_config()
        self.categories = self.config.get("categories", {})
        self.high_weight_media = [m.lower() for m in self.config.get("high_weight_media", [])]
        self.multi_coverage_bonus = self.config.get("multi_coverage_bonus", {})
        self.risk_themes = self.config.get("risk_themes", {})
        self.negative_filter = [w.lower() for w in self.config.get("negative_filter", [])]
        self.confirmation_levels = self.config.get("confirmation_level", {})
        self.signal_decay = self.config.get("signal_decay", {})

    def is_noise(self, news: NewsItem) -> bool:
        """负向过滤：检测是否为噪音新闻"""
        text = f"{news.title} {news.summary}".lower()
        for noise_word in self.negative_filter:
            if noise_word in text:
                has_signal = False
                for cat_config in self.categories.values():
                    for kw in cat_config.get("keywords", []):
                        if kw.lower() in text:
                            has_signal = True
                            break
                    if has_signal:
                        break
                if not has_signal:
                    return True
        return False

    def score(self, news: NewsItem, all_news: list[NewsItem]) -> float:
        """
        计算新闻重要性分数

        计算流程:
        1. 基础分 = 分类关键词匹配 × 权重
        2. 升级分 = escalation_rules 信号组合触发
        3. 媒体分 = 高权重媒体 + 多源覆盖
        4. 主题分 = 风险主题匹配
        5. 确认乘数 = confirmation_level
        6. 时间衰减 = signal_decay（按发布时间）
        """
        score = 0.0
        text = f"{news.title} {news.summary}".lower()

        # 1. 负向过滤
        if self.is_noise(news):
            return -100

        # 2. 分类关键词 + 语义别名 + 升级规则
        escalation_bonus = 0.0
        matched_categories = []

        for cat_name, cat_config in self.categories.items():
            cat_weight = cat_config.get("weight", 1)
            requires_context = cat_config.get("requires_context", False)
            context_words = cat_config.get("context_required", [])
            keywords = cat_config.get("keywords", [])
            aliases = cat_config.get("aliases", [])
            all_terms = keywords + aliases

            matched = False
            for term in all_terms:
                if term.lower() in text:
                    if requires_context:
                        has_context = any(c.lower() in text for c in context_words)
                        if has_context:
                            matched = True
                            break
                    else:
                        matched = True
                        break

            if matched:
                score += cat_weight
                matched_categories.append(cat_name)

                # 检查升级规则（信号组合）
                for rule in cat_config.get("escalation_rules", []):
                    triggers = rule.get("triggers", [])
                    matched_triggers = sum(1 for t in triggers if t.lower() in text)
                    if matched_triggers >= 2:  # 至少命中2个触发词
                        bonus = rule.get("bonus", 0)
                        escalation_bonus += bonus

        score += min(escalation_bonus, 30)  # cap升级加分,防单维度主导

        # 3. 源权威度评分（借鉴 scout.py 多维加权 + cap 设计）
        source_credibility = getattr(news, "credibility", 5)
        authority_score = min(source_credibility, 10) * 1.5  # max 15
        score += authority_score

        # 4. 高权重媒体加分（cap）
        media_bonus = 0
        if news.source.lower() in self.high_weight_media:
            media_bonus = min(5, 3)  # cap at 5
            score += media_bonus

        # 5. 多源覆盖加分
        coverage = self._count_coverage(news, all_news)
        coverage_bonus = 0
        for threshold, bonus in sorted(self.multi_coverage_bonus.items()):
            # 确保 threshold 是整数（YAML 可能解析为字符串）
            try:
                threshold = int(threshold)
            except (ValueError, TypeError):
                logger.warning(f"multi_coverage_bonus 键类型无效: {threshold}")
                continue
            if coverage >= threshold:
                coverage_bonus = bonus
        score += coverage_bonus

        # 5. 风险主题匹配加分
        theme_bonus = 0
        for _theme_name, theme_config in self.risk_themes.items():
            theme_keywords = theme_config.get("keywords", [])
            for keyword in theme_keywords:
                if keyword.lower() in text:
                    theme_bonus += 2
                    break
        score += theme_bonus

        # 6. 确认等级乘数
        multiplier = self._get_confirmation_multiplier(news, coverage, all_news)
        score = score * multiplier

        # 7. 时间衰减
        decay = self._calculate_time_decay(news.published)
        score = score * decay

        return round(score, 1)

    def _get_confirmation_multiplier(
        self, news: NewsItem, coverage: int, all_news: list[NewsItem]
    ) -> float:
        """
        根据信号确认等级返回乘数

        official: 政府/央行声明 → 1.5x
        multi_source: 3+ 独立来源 → 1.3x
        single_source: 单一来源 → 0.8x
        social_media: 社交媒体 → 0.5x
        """
        levels = self.confirmation_levels

        # 检查是否为官方来源
        official_keywords = levels.get("official", {}).get("sources", [])
        text = f"{news.title} {news.summary}".lower()
        for kw in official_keywords:
            if kw.lower() in text:
                return levels.get("official", {}).get("multiplier", 1.5)

        # 检查多源覆盖
        min_multi = levels.get("multi_source", {}).get("min_sources", 3)
        if coverage >= min_multi:
            return levels.get("multi_source", {}).get("multiplier", 1.3)

        # 检查社交媒体
        social_keywords = levels.get("social_media", {}).get("sources", [])
        for kw in social_keywords:
            if kw.lower() in news.source.lower():
                return levels.get("social_media", {}).get("multiplier", 0.5)

        # 默认：单一来源
        return levels.get("single_source", {}).get("multiplier", 0.8)

    def _calculate_time_decay(self, published: str) -> float:
        """
        根据发布时间计算信号衰减系数

        使用指数衰减：decay = base^(hours_since / half_life)
        """
        if not published:
            return 1.0

        half_life = self.signal_decay.get("half_life_hours", 72)

        # 防止除零或负数 half_life
        if half_life <= 0:
            logger.warning(f"signal_decay.half_life_hours={half_life} 无效，回退到默认 72")
            half_life = 72

        # 解析发布时间
        try:
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"]:
                try:
                    pub_time = datetime.strptime(published[: len(fmt) + 5], fmt)
                    break
                except ValueError:
                    continue
            else:
                return 1.0

            now = datetime.utcnow()
            # 统一时区：将 aware datetime 转为 naive (UTC)
            if pub_time.tzinfo is not None:
                from datetime import timezone

                pub_time = pub_time.astimezone(timezone.utc).replace(tzinfo=None)
            hours_since = (now - pub_time).total_seconds() / 3600
            if hours_since < 0:
                hours_since = 0

            import math

            decay = math.exp(-0.693 * hours_since / half_life)
            return max(0.1, decay)  # 最低保留 10% 权重
        except Exception:
            return 1.0

    def _count_coverage(self, news: NewsItem, all_news: list[NewsItem]) -> int:
        """
        统计相似新闻的覆盖数量（标题词重叠 > 50%）

        优化：使用缓存的标题词集合，避免重复计算
        """
        if not news.title:
            return 1

        count = 1
        title_words = set(news.title.lower().split())
        if not title_words:
            return 1

        for other in all_news:
            if other.url == news.url:
                continue
            if not other.title:
                continue
            other_words = set(other.title.lower().split())
            if not other_words:
                continue
            overlap = len(title_words & other_words)
            similarity = overlap / max(len(title_words), len(other_words))
            if similarity > 0.5:
                count += 1

        return count


# ─────────────────────────────────────────────────
# HTTP 请求工具
# ─────────────────────────────────────────────────


class HttpClient:
    """HTTP 客户端，支持代理、重试、超时。使用线程本地 session 避免并发安全问题。"""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]

    def __init__(self, proxy: str = "", ssl_verify: bool = True):
        self._proxy = proxy
        self._ssl_verify = ssl_verify
        self._local = threading.local()

        # 设置代理
        if proxy:
            # 避免泄露代理凭据：只记录协议和主机
            try:
                from urllib.parse import urlparse

                parsed = urlparse(proxy)
                safe_proxy = f"{parsed.scheme}://{parsed.hostname or ''}"
                if parsed.port:
                    safe_proxy += f":{parsed.port}"
            except Exception:
                safe_proxy = "***"
            logger.info(f"已配置代理: {safe_proxy}")

        # SSL 警告
        if not ssl_verify:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            logger.warning("SSL 验证已关闭，存在安全风险")

    def _get_session(self) -> requests.Session:
        """获取当前线程的 session（线程本地，避免并发共享问题）"""
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                max_retries=0,  # 禁用 HTTPAdapter 重试，由 tenacity 统一管理
                pool_connections=5,
                pool_maxsize=10,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            if self._proxy:
                session.proxies = {
                    "http": self._proxy,
                    "https": self._proxy,
                }
            self._local.session = session
        return session

    # 最大响应体大小（10MB）
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024

    def get(self, url: str, timeout: int = 30) -> requests.Response:
        """
        发送 GET 请求，带重试

        Args:
            url: 请求 URL
            timeout: 超时秒数

        Returns:
            Response 对象

        Raises:
            ValueError: 响应体超过大小限制
        """
        headers = {
            "User-Agent": self.USER_AGENTS[hash(url) % len(self.USER_AGENTS)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        }

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=_is_retryable_http_error,
            reraise=True,
        )
        def _get() -> requests.Response:
            resp = self._get_session().get(
                url,
                headers=headers,
                timeout=timeout,
                verify=self._ssl_verify,
                stream=True,  # 流式下载，先检查大小
            )
            resp.raise_for_status()

            # 检查 Content-Length
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > self.MAX_RESPONSE_SIZE:
                resp.close()
                raise ValueError(
                    f"响应体过大: {int(content_length) / 1024 / 1024:.1f}MB > "
                    f"{self.MAX_RESPONSE_SIZE / 1024 / 1024:.1f}MB 限制"
                )

            # 读取内容并检查实际大小
            content = b""
            for chunk in resp.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > self.MAX_RESPONSE_SIZE:
                    resp.close()
                    raise ValueError(
                        f"响应体过大: > {self.MAX_RESPONSE_SIZE / 1024 / 1024:.1f}MB 限制"
                    )

            # 重新构建 response（因为 stream=True 时 content 为空）
            resp._content = content
            return resp

        return _get()


# ─────────────────────────────────────────────────
# RSS 抓取器
# ─────────────────────────────────────────────────


class RSSFetcher:
    """RSS 源抓取器"""

    def __init__(self, http_client: HttpClient):
        self.http = http_client

    def fetch(self, source: dict) -> list[NewsItem]:
        """
        抓取单个 RSS 源

        Args:
            source: 新闻源配置字典

        Returns:
            新闻列表
        """
        name = source.get("name", "未知")
        url = source.get("url", "")
        region = source.get("region", "未知")
        category = source.get("media_type", source.get("category", "未知"))

        logger.info(f"正在抓取 RSS: {name} ({url})")

        try:
            # 获取 RSS 内容
            response = self.http.get(url, timeout=30)
            if response.status_code == 503:
                import time as _time

                _time.sleep(0.5)
                response = self.http.get(url, timeout=30)
            # 使用 response.content（bytes）而非 response.text（str）
            # feedparser 内置编码检测，传入 bytes 可获得更准确的编码处理
            feed = feedparser.parse(response.content)

            if feed.bozo:
                if not feed.entries:
                    logger.warning(f"RSS 解析异常: {name} - {feed.bozo_exception}")
                    return []
                else:
                    logger.warning(
                        f"RSS 部分解析异常: {name} - {feed.bozo_exception}，"
                        f"仍获取到 {len(feed.entries)} 条条目（可能部分损坏）"
                    )

            news_items = []
            for entry in feed.entries[:30]:  # 每个源最多30条
                title = entry.get("title", "").strip()
                if not title:
                    continue

                link = entry.get("link", "")
                if not link:
                    continue

                # 解析发布时间
                published = self._parse_date(entry)

                # 过滤超过3天的旧文章
                if self._is_stale(published, max_days=3):
                    continue

                # 获取摘要
                summary = ""
                if hasattr(entry, "summary"):
                    summary = BeautifulSoup(entry.summary, "html.parser").get_text()[:500]
                elif hasattr(entry, "description"):
                    summary = BeautifulSoup(entry.description, "html.parser").get_text()[:500]

                # 过滤不相关新闻
                if not is_relevant_news(title, summary):
                    continue

                news_items.append(
                    NewsItem(
                        title=title,
                        url=link,
                        published=published,
                        source=name,
                        region=region,
                        category=category,
                        summary=summary,
                    )
                )

            logger.info(f"RSS {name}: 获取 {len(news_items)} 条有效新闻")
            return news_items

        except Exception as e:
            logger.error(f"RSS 抓取失败 {name}: {e}", exc_info=True)
            return []

    def _parse_date(self, entry) -> str:
        """解析 RSS 条目的发布时间"""
        for attr in ["published_parsed", "updated_parsed"]:
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    return datetime(*parsed[:6]).strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    pass

        for attr in ["published", "updated"]:
            raw = getattr(entry, attr, "")
            if raw:
                # 不截断，保留完整时间字符串（包括时区和毫秒）
                return raw.strip()

        # 无法确定日期时返回空字符串，由调用方决定如何处理
        # 避免返回 datetime.now() 导致旧文章永远不被过滤
        return ""

    def _is_stale(self, published: str, max_days: int = 3) -> bool:
        """
        判断文章是否超过指定天数

        Args:
            published: 发布时间字符串
            max_days: 最大保留天数

        Returns:
            True 表示过期应过滤，False 表示保留
        """
        if not published:
            # 无日期的文章视为过期（避免无限期保留）
            return True

        try:
            # 尝试解析常见日期格式
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"]:
                try:
                    pub_date = datetime.strptime(published[: len(fmt) + 5], fmt)
                    break
                except ValueError:
                    continue
            else:
                return False  # 解析失败则保留

            # 使用 UTC 时间保持与 _calculate_time_decay 一致
            from datetime import timezone

            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            return (now_utc - pub_date).days > max_days
        except Exception:
            return False


# ─────────────────────────────────────────────────
# 网页抓取器
# ─────────────────────────────────────────────────


class WebpageFetcher:
    """网页新闻抓取器"""

    def __init__(self, http_client: HttpClient):
        self.http = http_client

    @staticmethod
    def _extract_publish_date(elem, url: str) -> str:
        """
        尝试从 HTML 元素或 URL 中提取发布时间。

        优先级:
        1. <time> 子元素的 datetime 属性
        2. 父元素中 <time> 的 datetime 属性
        3. URL 中的日期模式 (如 /2024/01/15/)
        4. 元素附近的 meta/date 类名文本
        5. 回退到当前时间
        """
        # 1. 查找 <time> 子元素
        time_tag = elem.find("time")
        if time_tag:
            dt_attr = time_tag.get("datetime", "")
            if dt_attr:
                return dt_attr.strip()

        # 2. 查找父元素中的 <time>
        parent = elem.parent
        if parent:
            time_tag = parent.find("time")
            if time_tag:
                dt_attr = time_tag.get("datetime", "")
                if dt_attr:
                    return dt_attr.strip()

        # 3. 从 URL 提取日期 (支持 /YYYY/MM/DD/ 和 /YYYY-MM-DD/ 模式)
        date_match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
        if date_match:
            y, m, d = date_match.groups()
            return f"{y}-{m}-{d} 00:00"

        # 4. 查找带有 date/time 类名的兄弟元素
        if parent:
            for sibling in parent.find_all(
                attrs={"class": re.compile(r"(date|time|publish)", re.I)}
            ):
                text = sibling.get_text(strip=True)
                if text and len(text) >= 8:
                    return text[:25]

        # 5. 回退
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def fetch(self, source: dict) -> list[NewsItem]:
        """
        抓取网页新闻列表

        Args:
            source: 新闻源配置字典

        Returns:
            新闻列表
        """
        name = source.get("name", "未知")
        url = source.get("url", "")
        region = source.get("region", "未知")
        category = source.get("media_type", source.get("category", "未知"))
        scrape_config = source.get("scrape_config", {})

        logger.info(f"正在抓取网页: {name} ({url})")

        try:
            response = self.http.get(url, timeout=20)
            soup = BeautifulSoup(response.text, "html.parser")

            selector = scrape_config.get("selector", "a")
            base_url = scrape_config.get("base_url", "")

            elements = soup.select(selector)

            news_items = []
            for elem in elements[:30]:
                title = elem.get_text(strip=True)
                if not title or len(title) < 10:
                    continue

                href = elem.get("href", "")
                if not href:
                    continue

                # 补全相对 URL
                if href.startswith("/"):
                    if not base_url:
                        continue  # 无法补全，跳过
                    href = base_url.rstrip("/") + href
                elif not href.startswith("http"):
                    continue

                if not is_relevant_news(title):
                    continue

                published = self._extract_publish_date(elem, href)

                news_items.append(
                    NewsItem(
                        title=title,
                        url=href,
                        published=published,
                        source=name,
                        region=region,
                        category=category,
                    )
                )

            logger.info(f"网页 {name}: 获取 {len(news_items)} 条有效新闻")
            return news_items

        except Exception as e:
            logger.error(f"网页抓取失败 {name}: {e}", exc_info=True)
            return []


# ─────────────────────────────────────────────────
# 主抓取器
# ─────────────────────────────────────────────────


class NewsFetcher:
    """新闻抓取主控制器（支持并发抓取 + 增量去重 + 内容指纹）"""

    # per-source 超时（秒）- 需大于 HTTP 重试最坏情况（~60s）
    SOURCE_TIMEOUT = 65

    def __init__(self, proxy: str = "", ssl_verify: bool = True):
        self.http = HttpClient(proxy=proxy, ssl_verify=ssl_verify)
        self.rss_fetcher = RSSFetcher(self.http)
        self.webpage_fetcher = WebpageFetcher(self.http)
        self.scorer = ImportanceScorer()
        # 指标追踪
        self._last_success_count = 0
        self._last_failed_count = 0
        self._last_sources = []
        self._last_duplicate_count = 0
        self._last_repeat_count = 0
        self._last_failed_names = []
        # 超时取消事件（用于通知正在运行的线程停止）
        self._cancel_event = threading.Event()

    def _fetch_single_source(
        self, source: dict, cancel_event: threading.Event | None = None
    ) -> tuple[str, list[NewsItem]]:
        """
        抓取单个源（含重试 + 备用 URL），返回 (source_name, items)
        供线程池调用

        流程:
        1. 尝试主 URL
        2. 主 URL 失败 → 尝试 backup URLs
        3. 所有 URL 都失败 → 返回空
        """
        name = source.get("name", "未知")
        source_type = source.get("type", "rss")
        fetcher = self.rss_fetcher if source_type == "rss" else self.webpage_fetcher

        # 构建 URL 列表：主 URL + backup URLs
        urls_to_try = [source.get("url", "")]
        for backup_url in source.get("backup", []):
            if backup_url:
                urls_to_try.append(backup_url)

        last_error = None
        for url in urls_to_try:
            # 检查是否已被取消
            if cancel_event and cancel_event.is_set():
                logger.info(f"源 {name} 被取消，停止重试")
                return name, []
            test_source = dict(source)
            test_source["url"] = url
            try:
                items = fetcher.fetch(test_source)
                if items:
                    return name, items
            except Exception as e:
                last_error = e
                logger.warning(f"源 {name} ({url[:50]}...) 失败: {e}")
                continue

        if last_error:
            logger.error(f"源 {name} 所有 URL 均失败")
        return name, []

    def fetch_all(self, max_news: int = 150, max_workers: int = 16) -> list[NewsItem]:
        """
        并发抓取所有配置的新闻源

        Args:
            max_news: 最终返回的最大新闻数量
            max_workers: 并发线程数（最小为 1，默认 16）

        Returns:
            按重要性排序的新闻列表
        """
        # 校验 max_workers
        if max_workers < 1:
            logger.warning(f"max_workers={max_workers} 无效，回退到默认值 8")
            max_workers = 8

        sources = load_sources_config()

        # 检查 Premium 状态，限制源数量
        try:
            from src.premium import get_source_limit, is_premium_enabled

            is_premium = is_premium_enabled()
            source_limit = get_source_limit()
        except ImportError:
            is_premium = False
            source_limit = 10  # 免费版默认10个源

        # 过滤实验性源
        active_sources = [s for s in sources if not s.get("experimental", False)]

        # 免费版限制源数量
        if not is_premium and len(active_sources) > source_limit:
            # 优先保留tier=1的源
            tier1 = [s for s in active_sources if s.get("tier") == 1]
            tier2 = [s for s in active_sources if s.get("tier") != 1]

            # 从tier1中取，不足再从tier2中补
            selected = tier1[:source_limit]
            if len(selected) < source_limit:
                selected.extend(tier2[: source_limit - len(selected)])

            active_sources = selected
            logger.info(f"免费版：限制为 {len(active_sources)} 个新闻源")

        if not active_sources:
            logger.warning("无可用新闻源 (配置为空或所有源均被排除)，将返回空结果")

        # 记录实际使用的源（用于指标上报）
        self._last_sources = active_sources

        logger.info(f"开始并发抓取 {len(active_sources)} 个新闻源 (workers={max_workers})...")

        all_news: list[NewsItem] = []
        failed_names: list[str] = []
        success_count = 0

        # 重置取消事件
        self._cancel_event.clear()

        # 并发抓取
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_source = {
                executor.submit(self._fetch_single_source, source, self._cancel_event): source
                for source in active_sources
            }

            for future in as_completed(future_to_source):
                source = future_to_source[future]
                name = source.get("name", "未知")

                try:
                    src_name, items = future.result(timeout=self.SOURCE_TIMEOUT)
                    if items:
                        all_news.extend(items)
                        success_count += 1
                    else:
                        failed_names.append(name)
                except TimeoutError:
                    self._cancel_event.set()  # 通知所有正在运行的线程停止
                    future.cancel()  # 尝试取消未开始的任务
                    logger.warning(f"源 {name} 超时 ({self.SOURCE_TIMEOUT}s)，已丢弃")
                    failed_names.append(name)
                except Exception as e:
                    logger.error(f"源 {name} 异常: {e}")
                    failed_names.append(name)

        self._last_success_count = success_count
        self._last_failed_count = len(failed_names)
        self._last_failed_names = failed_names

        # 内容指纹去重（URL + title 联合）
        before_dedup = len(all_news)
        all_news = self._deduplicate(all_news)
        self._last_duplicate_count = before_dedup - len(all_news)

        # 增量去重：与上次抓取比较
        repeat_hashes = self._load_fetch_cache_hashes()
        if repeat_hashes:
            repeats = 0
            for news in all_news:
                fp = self._content_fingerprint(news)
                if fp in repeat_hashes:
                    news.importance_score = -50  # 标记为重复但不删除
                    repeats += 1
            self._last_repeat_count = repeats
            if repeats:
                logger.info(f"增量去重: {repeats} 条与上次抓取重复")

        # 计算重要性分数
        for news in all_news:
            if news.importance_score == 0:  # 未被标记为重复的
                news.importance_score = self.scorer.score(news, all_news)
            elif news.importance_score == -50:
                # 重复新闻打折扣
                base_score = self.scorer.score(news, all_news)
                news.importance_score = base_score * 0.7 if base_score > 0 else base_score

        # 过滤噪音（score = -100）
        before_filter = len(all_news)
        all_news = [n for n in all_news if n.importance_score > -100]
        filtered = before_filter - len(all_news)
        if filtered:
            logger.info(f"噪音过滤: 移除 {filtered} 条")

        # 按重要性排序
        all_news.sort(key=lambda x: x.importance_score, reverse=True)

        # 分层抽样：保证每个区域至少有 min_per_region 篇，防止大源碾压小源
        MIN_PER_REGION = 3
        by_region: dict[str, list] = {}
        for n in all_news:
            r = getattr(n, "region", "未知")
            by_region.setdefault(r, []).append(n)

        selected: list = []
        seen_urls: set = set()

        # 第一轮：每个区域取最低保障篇数
        for region, items in by_region.items():
            for item in items:
                if len(selected) >= max_news:
                    break
                url = getattr(item, "url", "")
                if url in seen_urls:
                    continue
                selected.append(item)
                seen_urls.add(url)
                if sum(1 for x in selected if getattr(x, "region", "") == region) >= MIN_PER_REGION:
                    break

        # 第二轮：剩余名额按评分全局排序填充
        remaining_slots = max_news - len(selected)
        if remaining_slots > 0:
            for item in all_news:
                if len(selected) >= max_news:
                    break
                url = getattr(item, "url", "")
                if url in seen_urls:
                    continue
                selected.append(item)
                seen_urls.add(url)

        result = selected

        logger.info(
            f"抓取完成: 成功 {success_count}/{len(active_sources)} 源, "
            f"获取 {len(all_news)} 条, 精选 {len(result)} 条"
        )
        if failed_names:
            logger.warning(f"失败源 ({len(failed_names)}): {', '.join(failed_names[:10])}")

        return result

    def _deduplicate(self, news_list: list[NewsItem]) -> list[NewsItem]:
        """URL 去重 + 内容指纹去重"""
        seen_urls: set[str] = set()
        seen_fingerprints: set[str] = set()
        unique: list[NewsItem] = []

        for news in news_list:
            # URL 去重
            normalized = normalize_url(news.url)
            if not normalized:
                continue  # 跳过空 URL
            url_hash = hashlib.md5(normalized.encode()).hexdigest()
            if url_hash in seen_urls:
                continue

            # 内容指纹去重（title + source）
            fp = self._content_fingerprint(news)
            if fp in seen_fingerprints:
                continue

            seen_urls.add(url_hash)
            seen_fingerprints.add(fp)
            unique.append(news)

        removed = len(news_list) - len(unique)
        if removed > 0:
            logger.info(f"去重: URL + 内容指纹移除 {removed} 条")

        return unique

    @staticmethod
    def _content_fingerprint(news: NewsItem) -> str:
        """生成内容指纹：title前80字符 + source"""
        key = (news.title[:80] + "|" + news.source).lower().strip()
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    @staticmethod
    def _load_fetch_cache_hashes() -> set[str]:
        """加载上次抓取的 URL hash 缓存"""
        from src.utils import get_cache_dir

        cache_file = get_cache_dir() / "last_fetch.json"
        if not cache_file.exists():
            return set()
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("url_hashes", []))
        except Exception:
            return set()
