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
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.utils import (
    load_sources_config,
    load_importance_config,
    normalize_url,
)

logger = logging.getLogger("global_news.fetcher")


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
        return {
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "source": self.source,
            "region": self.region,
            "category": self.category,
            "summary": self.summary,
            "importance_score": self.importance_score,
            "coverage_count": self.coverage_count,
        }


# ─────────────────────────────────────────────────
# 关键词过滤（从配置文件加载）
# ─────────────────────────────────────────────────

def _load_filter_keywords() -> list[str]:
    """从 importance_keywords.yaml 加载过滤词"""
    try:
        config = load_importance_config()
        return config.get("negative_filter", [])
    except Exception:
        return ["celebrity", "football", "soccer", "basketball", "NFL", "NBA"]


def _load_priority_keywords() -> list[str]:
    """从 importance_keywords.yaml 加载优先词"""
    try:
        config = load_importance_config()
        return config.get("priority_keywords", [])
    except Exception:
        return ["international", "global", "military", "sanction", "crisis", "war",
                "国际", "外交", "军事", "制裁", "危机"]


FILTER_KEYWORDS = _load_filter_keywords()
PRIORITY_KEYWORDS = _load_priority_keywords()


def is_relevant_news(title: str, summary: str = "") -> bool:
    """
    第一层：代码过滤

    判断新闻是否与国际局势相关。
    返回 True 表示保留，False 表示过滤掉。
    """
    text = f"{title} {summary}".lower()

    # 检查是否包含过滤关键词
    for keyword in FILTER_KEYWORDS:
        if keyword.lower() in text:
            # 但如果有优先关键词，仍然保留
            has_priority = any(
                pk.lower() in text for pk in PRIORITY_KEYWORDS
            )
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
        self.high_weight_media = [
            m.lower() for m in self.config.get("high_weight_media", [])
        ]
        self.multi_coverage_bonus = self.config.get("multi_coverage_bonus", {})
        self.risk_themes = self.config.get("risk_themes", {})
        self.negative_filter = [
            w.lower() for w in self.config.get("negative_filter", [])
        ]
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

        score += escalation_bonus

        # 3. 高权重媒体加分
        media_bonus = 0
        if news.source.lower() in self.high_weight_media:
            media_bonus = 3
            score += media_bonus

        # 4. 多源覆盖加分
        coverage = self._count_coverage(news, all_news)
        coverage_bonus = 0
        for threshold, bonus in sorted(self.multi_coverage_bonus.items()):
            if coverage >= threshold:
                coverage_bonus = bonus
        score += coverage_bonus

        # 5. 风险主题匹配加分
        theme_bonus = 0
        for theme_name, theme_config in self.risk_themes.items():
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

    def _get_confirmation_multiplier(self, news: NewsItem, coverage: int,
                                      all_news: list[NewsItem]) -> float:
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

        # 解析发布时间
        try:
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"]:
                try:
                    pub_time = datetime.strptime(published[:25], fmt)
                    break
                except ValueError:
                    continue
            else:
                return 1.0

            hours_since = (datetime.now() - pub_time).total_seconds() / 3600
            if hours_since < 0:
                hours_since = 0

            import math
            decay = math.exp(-0.693 * hours_since / half_life)
            return max(0.1, decay)  # 最低保留 10% 权重
        except Exception:
            return 1.0

    def _count_coverage(self, news: NewsItem, all_news: list[NewsItem]) -> int:
        """统计相似新闻的覆盖数量（标题词重叠 > 50%）"""
        count = 1
        title_words = set(news.title.lower().split())

        for other in all_news:
            if other.url == news.url:
                continue
            other_words = set(other.title.lower().split())
            if not title_words or not other_words:
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
    """HTTP 客户端，支持代理、重试、超时"""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]

    def __init__(self, proxy: str = "", ssl_verify: bool = True):
        self.session = requests.Session()
        self.ssl_verify = ssl_verify

        # 连接池配置，提升并发稳定性
        adapter = requests.adapters.HTTPAdapter(
            max_retries=3,
            pool_connections=10,
            pool_maxsize=20,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # 设置代理
        if proxy:
            self.session.proxies = {
                "http": proxy,
                "https": proxy,
            }
            logger.info(f"已配置代理: {proxy}")

        # SSL 警告
        if not ssl_verify:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            logger.warning("SSL 验证已关闭，存在安全风险")

    def get(self, url: str, timeout: int = 30) -> requests.Response:
        """
        发送 GET 请求，带重试

        Args:
            url: 请求 URL
            timeout: 超时秒数

        Returns:
            Response 对象
        """
        headers = {
            "User-Agent": self.USER_AGENTS[hash(url) % len(self.USER_AGENTS)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        }

        @retry(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=3, max=15),
            retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
            reraise=True,
        )
        def _get() -> requests.Response:
            resp = self.session.get(
                url,
                headers=headers,
                timeout=timeout,
                verify=self.ssl_verify,
            )
            resp.raise_for_status()
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
        name = source["name"]
        url = source["url"]
        region = source.get("region", "未知")
        category = source.get("category", "未知")

        logger.info(f"正在抓取 RSS: {name} ({url})")

        try:
            # 获取 RSS 内容
            response = self.http.get(url, timeout=20)
            feed = feedparser.parse(response.text)

            if feed.bozo and not feed.entries:
                logger.warning(f"RSS 解析异常: {name} - {feed.bozo_exception}")
                return []

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

                news_items.append(NewsItem(
                    title=title,
                    url=link,
                    published=published,
                    source=name,
                    region=region,
                    category=category,
                    summary=summary,
                ))

            logger.info(f"RSS {name}: 获取 {len(news_items)} 条有效新闻")
            return news_items

        except Exception as e:
            logger.error(f"RSS 抓取失败 {name}: {type(e).__name__}: {e}")
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
                return raw[:25]  # 截断过长的时间字符串

        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _is_stale(self, published: str, max_days: int = 3) -> bool:
        """判断文章是否超过指定天数"""
        try:
            # 尝试解析常见日期格式
            for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S"]:
                try:
                    pub_date = datetime.strptime(published[:len(fmt)+5], fmt)
                    break
                except ValueError:
                    continue
            else:
                return False  # 解析失败则保留

            return (datetime.now() - pub_date).days > max_days
        except Exception:
            return False


# ─────────────────────────────────────────────────
# 网页抓取器
# ─────────────────────────────────────────────────

class WebpageFetcher:
    """网页新闻抓取器"""

    def __init__(self, http_client: HttpClient):
        self.http = http_client

    def fetch(self, source: dict) -> list[NewsItem]:
        """
        抓取网页新闻列表

        Args:
            source: 新闻源配置字典

        Returns:
            新闻列表
        """
        name = source["name"]
        url = source["url"]
        region = source.get("region", "未知")
        category = source.get("category", "未知")
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
                    href = base_url + href
                elif not href.startswith("http"):
                    continue

                if not is_relevant_news(title):
                    continue

                news_items.append(NewsItem(
                    title=title,
                    url=href,
                    published=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    source=name,
                    region=region,
                    category=category,
                ))

            logger.info(f"网页 {name}: 获取 {len(news_items)} 条有效新闻")
            return news_items

        except Exception as e:
            logger.error(f"网页抓取失败 {name}: {type(e).__name__}: {e}")
            return []


# ─────────────────────────────────────────────────
# 主抓取器
# ─────────────────────────────────────────────────

class NewsFetcher:
    """新闻抓取主控制器（支持并发抓取 + 增量去重 + 内容指纹）"""

    # per-source 超时（秒）
    SOURCE_TIMEOUT = 10

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

    def _fetch_single_source(self, source: dict) -> tuple[str, list[NewsItem]]:
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

    def fetch_all(self, max_news: int = 150, max_workers: int = 8) -> list[NewsItem]:
        """
        并发抓取所有配置的新闻源

        Args:
            max_news: 最终返回的最大新闻数量
            max_workers: 并发线程数

        Returns:
            按重要性排序的新闻列表
        """
        sources = load_sources_config()
        self._last_sources = sources

        # 检查 Premium 状态，限制源数量
        try:
            from src.premium import is_premium_enabled, get_source_limit
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
                selected.extend(tier2[:source_limit - len(selected)])

            active_sources = selected
            logger.info(f"免费版：限制为 {len(active_sources)} 个新闻源")

        logger.info(f"开始并发抓取 {len(active_sources)} 个新闻源 (workers={max_workers})...")

        all_news: list[NewsItem] = []
        failed_names: list[str] = []
        success_count = 0

        # 并发抓取
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_source = {
                executor.submit(self._fetch_single_source, source): source
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

        # 截取到配置数量
        result = all_news[:max_news]

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
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get("url_hashes", []))
        except Exception:
            return set()
