"""
AI 总结模块 v2.0 — 三阶段 Pipeline

Stage 1: 新闻 → 结构化事件提取 (JSON)
Stage 2: 结构化事件 → 主题聚类 (JSON)
Stage 3: 聚类结果 + 历史演化 → 最终日报 (Markdown)

相比单阶段方案:
- token 降低 ~60%（每次调用只处理必要信息）
- 输出格式更稳定（结构化 JSON 比自由 Markdown 可靠）
- 幻觉减少（AI 不再需要同时提取+分析+格式化）
"""

import json
import logging
import re

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.fetcher import NewsItem
from src.utils import get_history_dir, get_prompts_dir, load_prompt_template

logger = logging.getLogger("global_news.summarizer")


def _is_retryable_api_error(retry_state) -> bool:
    """判断 API 错误是否应重试：ConnectionError、Timeout、HTTP 429/5xx"""
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


class NewsSummarizer:
    """新闻 AI 分析总结器 — 三阶段 Pipeline"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model_name: str = "deepseek-chat",
        proxy: str = "",
        ssl_verify: bool = True,
        lang: str = "zh",
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.ssl_verify = ssl_verify
        self.lang = lang

        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        if not ssl_verify:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ═══════════════════════════════════════════════
    # 主入口：三阶段 Pipeline
    # ═══════════════════════════════════════════════

    def summarize(self, news_items: list[NewsItem]) -> str:
        """
        三阶段 AI 分析 Pipeline

        Stage 1: 新闻 → 结构化事件 (JSON)
        Stage 2: 事件 → 主题聚类 (JSON)
        Stage 3: 聚类 → 最终日报 (Markdown)
        """
        if not news_items:
            return "# 今日无新闻数据\n\n未获取到有效新闻。"
        if not self.api_key:
            raise ValueError("API_KEY 未配置")

        # Stage 1: 事件提取
        logger.info("  Stage 1/3: 事件提取...")
        events_json = self._stage1_extract_events(news_items)
        if events_json is None:
            logger.warning("Stage 1 失败，回退到单阶段模式")
            return self._fallback_single_stage(news_items)
        if not events_json:
            logger.info("Stage 1 未提取到事件，回退到单阶段模式")
            return self._fallback_single_stage(news_items)

        # Stage 2: 事件聚类
        logger.info("  Stage 2/3: 事件聚类...")
        clusters_json = self._stage2_cluster_events(events_json)
        if clusters_json is None:
            logger.warning("Stage 2 失败，回退到单阶段模式")
            return self._fallback_single_stage(news_items)
        if not clusters_json:
            logger.info("Stage 2 未产生聚类结果，回退到单阶段模式")
            return self._fallback_single_stage(news_items)

        # Stage 3: 日报生成
        logger.info("  Stage 3/3: 日报生成...")
        report = self._stage3_generate_report(clusters_json, events_json, news_items)
        if not report:
            logger.warning("Stage 3 失败，回退到单阶段模式")
            return self._fallback_single_stage(news_items)

        logger.info(f"三阶段 Pipeline 完成: {len(report)} 字符")
        return report

    # ═══════════════════════════════════════════════
    # Stage 1: 事件提取
    # ═══════════════════════════════════════════════

    def _stage1_extract_events(self, news_items: list[NewsItem]) -> list | None:
        """从新闻中提取结构化事件"""
        news_content = self._format_news_for_prompt(news_items)

        # 输入截断保护：超过 80K 字符时按区域轮询截断，避免超出模型上下文窗口
        MAX_NEWS_CHARS = 80000
        if len(news_content) > MAX_NEWS_CHARS:
            logger.warning(f"  新闻内容 {len(news_content)} 字符超过 {MAX_NEWS_CHARS} 上限，将截断")
            news_content = news_content[:MAX_NEWS_CHARS]
            # 在最后一个完整行处截断
            last_nl = news_content.rfind("\n")
            if last_nl > MAX_NEWS_CHARS * 0.8:
                news_content = news_content[:last_nl]

        # 动态 few-shot：从当天新闻选取示例注入提示词
        try:
            dynamic_examples = self._select_dynamic_examples(news_items, n_examples=4)
            fewshot_text = self._render_dynamic_examples(dynamic_examples)
            if fewshot_text:
                logger.info(
                    f"  动态few-shot: 选取 {len(dynamic_examples)} 个示例 "
                    f"({', '.join(getattr(e, 'region', '?') for e in dynamic_examples)})"
                )
        except Exception as e:
            logger.debug(f"动态few-shot选取失败: {e}")
            fewshot_text = ""

        try:
            prompt_template = self._load_stage_prompt("stage1_event_extraction.txt")
            # 将动态示例注入到示例区域之后
            if fewshot_text:
                base_prompt = prompt_template.replace("{news_content}", news_content)
                dynamic_header = (
                    "## 当日动态示例（来自今天真实新闻，参考提取模式）\n"
                    + fewshot_text
                    + "\n\n## 注意事项"
                )
                if "## 注意事项" in base_prompt:
                    prompt = base_prompt.replace("## 注意事项", dynamic_header)
                else:
                    prompt = base_prompt + "\n\n## 当日动态示例\n" + fewshot_text
            else:
                prompt = prompt_template.replace("{news_content}", news_content)
            sys_msg = (
                "You are a precise event extractor. Output only valid JSON arrays, no other text."
                if self.lang == "en"
                else "你是一个精确的事件提取器。只输出合法 JSON 数组，不要输出任何其他文字。"
            )
            result = self._call_api(
                prompt,
                system_msg=sys_msg,
                temperature=0.1,
                max_tokens=32000,
                timeout=300,
            )

            if not result:
                logger.warning("Stage 1 API 返回空内容")
                return None

            events = self._parse_json(result)

            if events is None:
                logger.warning("Stage 1 JSON 解析失败，返回 None")
                return None

            if isinstance(events, list):
                # 验证列表中的每个元素是否为字典
                valid_events = []
                for i, e in enumerate(events):
                    if isinstance(e, dict) and "title" in e:
                        valid_events.append(e)
                    else:
                        logger.warning(f"Stage 1 第 {i} 个事件格式异常: {type(e)}")
                if valid_events:
                    valid_events = self._validate_stage1_events(valid_events)
                    logger.info(f"  Stage 1 完成: 提取 {len(valid_events)} 个事件")
                    return valid_events
                else:
                    logger.info("Stage 1 AI 返回空事件列表")
                    return []
            elif isinstance(events, dict):
                # 可能是 {events: [...]} 格式
                if "events" in events and isinstance(events["events"], list):
                    logger.info(f"  Stage 1 完成: 提取 {len(events['events'])} 个事件")
                    return events["events"]
                # 可能是单个事件对象
                elif "title" in events:
                    logger.info("  Stage 1 完成: 提取 1 个事件")
                    return [events]
                else:
                    logger.warning(f"Stage 1 返回字典格式异常，keys: {list(events.keys())[:5]}")
                    return None
            else:
                logger.warning(f"Stage 1 返回格式异常: {type(events)}")
                return None
        except Exception as e:
            logger.error(f"Stage 1 失败: {e}", exc_info=True)
            return None

    # ═══════════════════════════════════════════════
    # Stage 2: 事件聚类
    # ═══════════════════════════════════════════════

    def _stage2_cluster_events(self, events: list) -> list | None:
        """将事件按主题聚类"""
        # 使用全部事件进行聚类（不再截断）
        events_content = json.dumps(events, ensure_ascii=False, indent=2)

        # 加载昨日事件用于新兴信号判断
        yesterday_context = self._load_yesterday_events()

        try:
            prompt_template = self._load_stage_prompt("stage2_event_clustering.txt")
            prompt = (
                prompt_template.replace("{events_content}", events_content)
                .replace("{events_count}", str(len(events)))
                .replace("{yesterday_context}", yesterday_context)
            )
            sys_msg = (
                "You are an event clustering analyst. Output only valid JSON arrays, no other text."
                if self.lang == "en"
                else "你是一个事件聚类分析师。只输出合法 JSON 数组，不要输出任何其他文字。"
            )
            result = self._call_api(
                prompt,
                system_msg=sys_msg,
                temperature=0.1,
                max_tokens=12000,
                timeout=120,
            )
            # Stage 2 失败时重试：用更少的事件（截断到15个）减少 token 压力
            if not result and len(events) > 15:
                logger.warning("Stage 2 首次失败，用精简事件集重试...")
                short_events = events[:15]
                short_content = json.dumps(short_events, ensure_ascii=False, indent=2)
                short_prompt = (
                    prompt_template.replace("{events_content}", short_content)
                    .replace("{events_count}", str(len(short_events)))
                    .replace("{yesterday_context}", yesterday_context)
                )
                result = self._call_api(
                    short_prompt,
                    system_msg=sys_msg,
                    temperature=0.1,
                    max_tokens=6000,
                    timeout=120,
                )
            if not result:
                logger.warning("Stage 2 API 返回空内容")
                return None
            clusters = self._parse_json(result)
            if isinstance(clusters, list):
                clusters = self._validate_stage2_clusters(clusters, len(events))
                logger.info(f"  Stage 2 完成: {len(clusters)} 个聚类")
                return clusters
            elif isinstance(clusters, dict) and "clusters" in clusters:
                logger.info(f"  Stage 2 完成: {len(clusters['clusters'])} 个聚类")
                return clusters["clusters"]
            else:
                logger.warning("Stage 2 返回格式异常")
                return None
        except Exception as e:
            logger.error(f"Stage 2 失败: {e}", exc_info=True)
            return None

    # ═══════════════════════════════════════════════
    # Stage 3: 日报生成
    # ═══════════════════════════════════════════════

    def _stage3_generate_report(
        self, clusters: list, events: list, news_items: list[NewsItem]
    ) -> str | None:
        """基于聚类结果生成最终 Markdown 日报（含 h2 校验和自动重试）"""
        # 检查 Premium 状态
        try:
            from src.premium import get_chapter_limit, is_premium_enabled

            is_premium = is_premium_enabled()
            chapter_limit = get_chapter_limit()
        except ImportError:
            is_premium = False
            chapter_limit = 3  # 免费版默认3章

        if not is_premium:
            logger.info("  免费版：仅生成3章精简日报")

        clusters_text = json.dumps(clusters, ensure_ascii=False, indent=2)
        events_text = json.dumps(events[:30], ensure_ascii=False, indent=2)
        history_context = self._load_history_context()
        news_links = self._format_news_links(news_items)

        # 获取市场数据（仅Premium）
        market_data = "Market data not available." if self.lang == "en" else "市场数据暂未接入。"
        if is_premium:
            try:
                from src.market_data import get_market_provider

                market_data = get_market_provider().get_data_for_prompt()
            except Exception as e:
                logger.warning(f"获取市场数据失败: {e}")

        try:
            prompt_template = load_prompt_template(lang=self.lang)

            # 根据版本调整prompt
            if not is_premium:
                # 免费版：精简prompt，只要求3章
                if self.lang == "en":
                    premium_hint = """

**Edition Limitation**: You are using the Free Edition. Output only the following 3 chapters:
1. Executive Summary
2. Core Risks and Watchpoints
3. Core International Event Analysis

Other chapters are not required. For the full 10-chapter report, please upgrade to Premium Edition.
"""
                else:
                    premium_hint = """

**版本限制**：当前为免费版，仅需输出以下3个章节：
1. 今日核心判断
2. 核心风险与观察哨
3. 核心国际事件分析

其他章节无需输出。如需完整10章日报，请升级到付费版。
"""
            else:
                premium_hint = ""

            # 计算区域分布统计
            region_counts: dict[str, int] = {}
            for n in news_items:
                region = getattr(n, "region", "未知")
                region_counts[region] = region_counts.get(region, 0) + 1
            region_stats = "\n".join(
                f"- {r}: {c} 篇" for r, c in sorted(region_counts.items(), key=lambda x: -x[1])
            )
            low_coverage = [r for r, c in region_counts.items() if c < 3]
            low_warning = ""
            if low_coverage:
                low_warning = (
                    f"\n\n**区域覆盖预警**：以下区域文章数不足3篇：{', '.join(low_coverage)}。"
                    f"日报中不得完全忽略这些区域，至少为每个区域分配1-2句分析。"
                )

            # 区域平衡指令（独立变量，模板直接引用）
            region_balance_directive = (
                "**区域平衡强制指令**：任何单一区域（如中东）不得占据日报超过40%的篇幅。"
                "如果某个区域当日无重大事件，用1-2句诚实说明即可，但不能完全略过。"
                "非洲、南美、中亚、东南亚、南亚、太平洋岛国等区域即使新闻较少，也要在"
                "第6章（事件聚类）或第7章（长期趋势）中至少提及。"
            )

            # 市场数据强制指令（独立变量）
            market_directive = (
                "**强制指令：以下市场数据必须逐行原样填入第4章表格。"
                "每个数值必须出现在报告中，绝对禁止使用 —、暂无、暂未接入、N/A 等占位符。"
                "若某数据源确实异常，标注'数据源异常(具体原因)'而非留空。**"
            )

            prompt = prompt_template.format(
                source_count=len({n.source for n in news_items}),
                news_count=len(news_items),
                history_context=history_context,
                region_distribution=f"{region_stats}{low_warning}",
                region_balance_directive=region_balance_directive,
                stage2_clusters=clusters_text,
                stage1_events=events_text,
                market_data_reference=f"{market_directive}\n\n{market_data}",
                news_links=news_links,
            )

            # 添加版本限制提示
            if premium_hint:
                prompt = prompt + premium_hint
        except (FileNotFoundError, KeyError, ValueError) as e:
            logger.error(f"Stage 3 prompt 准备失败: {e}", exc_info=True)
            return None

        # 付费版强制10章，免费版3章
        MIN_H2 = chapter_limit  # 付费版10章，免费版3章
        result = ""
        base_prompt = prompt  # 保存初始 prompt，避免重试时 token 膨胀

        for attempt in range(3):
            try:
                sys_msg = (
                    "You are a professional international strategic intelligence analyst serving a private OSINT strategic observation system. Your analysis must be professional, calm, specific, and free of empty words."
                    if self.lang == "en"
                    else "你是一名国际战略情报分析员，服务于私人 OSINT 战略观察系统。你的分析必须专业、冷静、具体，不说空话。"
                )
                result = self._call_api(
                    prompt,
                    system_msg=sys_msg,
                    temperature=0.3,
                    max_tokens=32000,
                    timeout=300,
                )

                h2_count = len(re.findall(r"^##\s+", result, re.MULTILINE))
                logger.info(
                    f"  Stage 3 第{attempt + 1}次: {len(result)} 字符, {h2_count} 个 h2 章节"
                )

                if h2_count >= MIN_H2:
                    logger.info(f"  Stage 3 通过校验 (h2 >= {MIN_H2})")
                    return result

                if attempt < 2:
                    logger.warning(
                        f"  Stage 3 h2 章节不足 ({h2_count} < {MIN_H2})，"
                        f"第 {attempt + 2} 次重试（追加强制指令）..."
                    )
                    # 基于初始 prompt 重试，避免 token 膨胀
                    chapter_desc = (
                        "全部 10 个 ## 章节（一至十）"
                        if self.lang == "zh"
                        else "all 10 ## chapters (I through X)"
                    )
                    if self.lang == "en":
                        prompt = base_prompt + (
                            f"\n\nPrevious output was missing {MIN_H2 - h2_count} required chapters. "
                            f"Regenerate immediately, must include {chapter_desc}. "
                            f"Even chapters lacking information must have a heading and brief explanation."
                        )
                    else:
                        prompt = base_prompt + (
                            f"\n\n上一条输出缺少了 {MIN_H2 - h2_count} 个必要章节。"
                            f"请立即重新生成完整日报，必须包含{chapter_desc}。"
                            f"即使某些章节当日缺乏信息，也必须输出章节标题并简述原因。"
                        )
                else:
                    logger.warning(f"  Stage 3 3次尝试后仍不足 {MIN_H2} 个章节，使用最后一次结果")

            except Exception as e:
                logger.error(f"Stage 3 第{attempt + 1}次失败: {e}", exc_info=True)
                if attempt >= 2:
                    return None

        return result

    # ═══════════════════════════════════════════════
    # Fallback: 单阶段模式（兼容旧版）
    # ═══════════════════════════════════════════════

    def _fallback_single_stage(self, news_items: list[NewsItem]) -> str:
        """单阶段 fallback：直接用完整 prompt 生成日报"""
        logger.info("使用单阶段 fallback 模式...")
        history_context = self._load_history_context()
        # 检查 Premium 状态，限制章节
        try:
            from src.premium import is_premium_enabled

            is_premium = is_premium_enabled()
        except ImportError:
            is_premium = False

        try:
            # 计算区域分布（用于模板变量）
            region_counts: dict[str, int] = {}
            for n in news_items:
                r = getattr(n, "region", "未知")
                region_counts[r] = region_counts.get(r, 0) + 1
            region_stats = "\n".join(
                f"- {r}: {c} 篇" for r, c in sorted(region_counts.items(), key=lambda x: -x[1])
            )
            region_directive = "**区域平衡强制指令**：任何单一区域不得占据日报超过40%的篇幅。"
            market_directive = "**强制指令：市场数据必须逐行原样填入第4章表格，禁止使用占位符。**"

            prompt_template = load_prompt_template(lang=self.lang)
            prompt = prompt_template.format(
                source_count=len({n.source for n in news_items}),
                news_count=len(news_items),
                history_context=history_context,
                region_distribution=region_stats,
                region_balance_directive=region_directive,
                stage2_clusters="（单阶段模式：事件聚类由AI直接生成）",
                stage1_events="（单阶段模式：事件提取与日报生成合并）",
                market_data_reference=market_directive,
                news_links=self._format_news_links(news_items),
            )

            # 免费版：添加章节限制指令
            if not is_premium:
                if self.lang == "en":
                    premium_hint = """

**Edition Limitation**: You are using the Free Edition. Output only the following 3 chapters:
1. Executive Summary
2. Core Risks and Watchpoints
3. Core International Event Analysis

Other chapters are not required. For the full 10-chapter report, please upgrade to Premium Edition.
"""
                else:
                    premium_hint = """

**版本限制**：当前为免费版，仅需输出以下3个章节：
1. 今日核心判断
2. 核心风险与观察哨
3. 核心国际事件分析

其他章节无需输出。如需完整10章日报，请升级到付费版。
"""
                prompt = prompt + premium_hint
        except (FileNotFoundError, KeyError, ValueError) as e:
            logger.error(f"Fallback prompt 准备失败: {type(e).__name__}: {e}")
            return ""

        sys_msg = (
            "You are a professional international strategic intelligence analyst serving a private OSINT strategic observation system."
            if self.lang == "en"
            else "你是一名国际战略情报分析员，服务于私人 OSINT 战略观察系统。"
        )

        try:
            return self._call_api(prompt, system_msg=sys_msg)
        except Exception as e:
            logger.error(f"单阶段 fallback 失败: {e}", exc_info=True)
            return ""

    # ═══════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════

    def _load_stage_prompt(self, filename: str) -> str:
        """加载指定 stage 的 prompt 模板（根据语言选择）"""
        if self.lang == "en":
            name, ext = filename.rsplit(".", 1)
            en_filename = f"{name}_en.{ext}"
            en_path = get_prompts_dir() / en_filename
            if en_path.exists():
                prompt_path = en_path
            else:
                prompt_path = get_prompts_dir() / filename
        else:
            prompt_path = get_prompts_dir() / filename
        if not prompt_path.exists():
            raise FileNotFoundError(f"Stage prompt 不存在: {prompt_path}")
        with open(prompt_path, encoding="utf-8") as f:
            return f.read()

    def _format_news_for_prompt(self, news_items: list[NewsItem]) -> str:
        """格式化新闻为 prompt 输入（区域轮询排序，打破区域块效应）"""
        lines = []
        by_region: dict[str, list[NewsItem]] = {}
        for news in news_items:
            by_region.setdefault(news.region, []).append(news)

        summary_label = "Summary:" if self.lang == "en" else "摘要:"
        published_label = "Published:" if self.lang == "en" else "发布:"

        # 区域轮询排序：从每个区域轮流取1条，打破同一区域连续出现的锚定效应
        region_queues = {r: list(items) for r, items in by_region.items()}
        counter = 0
        while region_queues:
            for region in sorted(region_queues.keys()):
                items = region_queues[region]
                if not items:
                    continue
                item = items.pop(0)
                counter += 1
                lines.append(f"{counter}. **[{item.source}]** [{region}] {item.title}")
                if item.summary:
                    lines.append(f"   {summary_label} {item.summary[:400]}")
                lines.append(f"   {published_label} {item.published}")
            # 清理空队列
            region_queues = {r: q for r, q in region_queues.items() if q}

        return "\n".join(lines)

    def _format_news_links(self, news_items: list[NewsItem]) -> str:
        """格式化新闻链接列表（用于日报的'原始新闻链接'章节）"""
        lines = []

        def _is_valid_url(url: str) -> bool:
            """过滤明显无效的 URL（占位符、缺少域名等）"""
            if not url or not isinstance(url, str):
                return False
            placeholder_patterns = [
                "xxxx",
                "placeholder",
                "example.com/test",
                "/xxxx",
                "undefined",
                "null",
                "about:blank",
            ]
            url_lower = url.lower()
            if any(p in url_lower for p in placeholder_patterns):
                return False
            # 匹配 /a-数字 格式的占位符路径（如 /a-123456），但排除 /a-字母 的合法路径
            if re.search(r"/a-\d+", url_lower):
                return False
            if not (url.startswith("http://") or url.startswith("https://")):
                return False
            return True

        by_region: dict[str, list[NewsItem]] = {}
        for news in news_items:
            by_region.setdefault(news.region, []).append(news)

        for region, items in by_region.items():
            lines.append(f"\n**{region}：**")
            for item in items:
                if _is_valid_url(item.url):
                    if self.lang == "en":
                        lines.append(
                            f"- {item.source} - {item.title} ({item.published}) [Read original]({item.url})"
                        )
                    else:
                        lines.append(
                            f"- {item.source} - {item.title} （{item.published}）[阅读原文]({item.url})"
                        )
                else:
                    # 无效 URL 时只显示来源和标题，不显示链接
                    lines.append(f"- {item.source} - {item.title} （{item.published}）")

        return "\n".join(lines)

    def _load_history_context(self) -> str:
        """加载历史上下文（增强版：含90天趋势仪表盘 + 14天事件摘要）"""
        no_data_msg = "No historical data available." if self.lang == "en" else "暂无历史数据。"
        cluster_label = "Event Clusters:" if self.lang == "en" else "事件聚类:"
        signal_label = "Risk Signals:" if self.lang == "en" else "风险信号:"

        # 尝试加载90天趋势仪表盘
        trend_context = ""
        try:
            from src.history_analyzer import HistoryAnalyzer

            analyzer = HistoryAnalyzer(max_days=90)
            trend_context = analyzer.render_for_prompt(lang=self.lang)
            if trend_context:
                logger.info("  已加载90天历史趋势仪表盘")
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"HistoryAnalyzer 加载失败: {e}")

        # 尝试加载演化追踪数据
        evolution_context = ""
        try:
            from src.evolution_tracker import EvolutionTracker

            tracker = EvolutionTracker()
            active = tracker.get_active_events() or []
            if active:
                phase_label = "阶段跃迁事件" if self.lang == "zh" else "Phase Transition Events"
                evo_lines = [f"\n**{phase_label}**:"]
                for evt in active[:5]:
                    if isinstance(evt, dict):
                        title = evt.get("title", evt.get("name", ""))
                        phase = evt.get("current_phase", "")
                        if title:
                            evo_lines.append(
                                f"- {title} (阶段: {phase})" if phase else f"- {title}"
                            )
                evolution_context = "\n".join(evo_lines)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"EvolutionTracker 加载失败: {e}")

        history_dir = get_history_dir()
        if not history_dir.exists():
            return trend_context or no_data_msg

        # 检查 Premium 状态，限制历史天数
        try:
            from src.premium import get_history_days

            history_days = get_history_days()
        except ImportError:
            history_days = 0  # 免费版无历史

        # 免费版不加载历史
        if history_days == 0:
            return no_data_msg

        from datetime import datetime, timedelta

        context_parts = []
        today = datetime.now()

        for i in range(1, min(history_days, 14) + 1):  # 最多加载3天
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            history_file = history_dir / f"history_{date_str}.json"

            if history_file.exists():
                try:
                    with open(history_file, encoding="utf-8") as f:
                        data = json.load(f)

                    signals = data.get("signals", [])
                    clusters = data.get("clusters", [])
                    if not isinstance(signals, list):
                        signals = []
                    if not isinstance(clusters, list):
                        clusters = []

                    if signals or clusters:
                        context_parts.append(f"\n--- {date_str} ---")
                        if clusters:
                            context_parts.append(cluster_label)
                            for c in clusters[:3]:
                                if isinstance(c, dict):
                                    context_parts.append(f"  - {c.get('title', str(c))}")
                                else:
                                    context_parts.append(f"  - {c}")
                        if signals:
                            context_parts.append(signal_label)
                            for s in signals[:3]:
                                if isinstance(s, dict):
                                    context_parts.append(f"  - {s.get('title', str(s))}")
                                else:
                                    context_parts.append(f"  - {s}")

                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"解析历史文件 {history_file.name} 失败: {e}")

        event_context = "\n".join(context_parts) if context_parts else ""

        # 合并趋势仪表盘、演化追踪和事件摘要
        parts = []
        if trend_context:
            parts.append(trend_context)
        if evolution_context:
            parts.append(evolution_context)
        if event_context:
            parts.append("\n### 近期事件摘要\n" + event_context)

        return "\n".join(parts) if parts else no_data_msg

    def _parse_json(self, text: str) -> object | None:
        """从 AI 输出中解析 JSON（支持多种格式）"""
        if not text:
            return None

        # 0. 移除 <thinking>...</thinking> 标签内容（CoT推理步骤）
        text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()

        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 从 ```json ... ``` 代码块中提取
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 3. 从第一个 [ 或 { 开始解析
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass

        # 4. 清理常见格式问题后重试
        # 找到第一个 [ 或 { 的位置
        json_start = -1
        for i, char in enumerate(text):
            if char in "[{":
                json_start = i
                break
        if json_start > 0:
            cleaned = text[json_start:]
            # 找到最后一个 ] 或 } 的位置
            for end_char in ["]", "}"]:
                last_idx = cleaned.rfind(end_char)
                if last_idx != -1:
                    cleaned = cleaned[: last_idx + 1]
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        pass

        # 5. 尝试修复常见JSON格式问题
        # 移除尾随逗号
        text_fixed = re.sub(r",\s*([\]}])", r"\1", text)
        try:
            return json.loads(text_fixed)
        except json.JSONDecodeError:
            pass

        # 6. 截断 JSON 修复：补齐缺失的括号
        # 从第一个 [ 或 { 开始，统计开闭括号，补齐缺失的闭合括号
        json_start = -1
        for i, char in enumerate(text):
            if char in "[{":
                json_start = i
                break
        if json_start >= 0:
            truncated = text[json_start:]
            # 统计括号
            stack: list[str] = []
            for char in truncated:
                if char in "[{":
                    stack.append("]" if char == "[" else "}")
                elif char in "]}":
                    if stack and stack[-1] == char:
                        stack.pop()
            # 补齐缺失的闭合括号
            if stack:
                truncated += "".join(reversed(stack))
                try:
                    return json.loads(truncated)
                except json.JSONDecodeError:
                    # 尝试只保留完整的项（数组的情况下）
                    pass

        # 7. 单引号 JSON 修复（某些模型输出单引号格式）
        try:
            fixed_quotes = text.replace("'", '"')
            return json.loads(fixed_quotes)
        except json.JSONDecodeError:
            pass

        return None

    # ═══════════════════════════════════════════════
    # Post-Validation: Stage 1/2 输出质量校验
    # ═══════════════════════════════════════════════

    _VALID_REGIONS = frozenset(
        {
            "中东",
            "欧洲",
            "北美",
            "亚太",
            "东南亚",
            "南亚",
            "中亚",
            "中国",
            "俄罗斯",
            "非洲",
            "南美",
            "全球",
        }
    )

    _VALID_EVENT_TYPES = frozenset(
        {
            "军事冲突",
            "外交博弈",
            "经济金融",
            "科技竞争",
            "资源能源",
            "社会政治",
            "环境气候",
        }
    )

    _VALID_SOURCE_LEAN = frozenset(
        {
            "偏好西方叙事",
            "偏好中方叙事",
            "偏好俄方叙事",
            "区域本土视角",
            "全球南方视角",
            "多源平衡",
            "叙事撕裂",
            "亲西方",
            "亲中方",
            "亲俄方",
            "中立",
            "混合",
        }
    )

    # ═══════════════════════════════════════════════
    # Dynamic Few-Shot: 从当天新闻自动选取示例
    # ═══════════════════════════════════════════════

    def _select_dynamic_examples(
        self, news_items: list[NewsItem], n_examples: int = 4
    ) -> list[NewsItem]:
        """6维加权贪心算法：从当天新闻中选取 few-shot 示例，最大化区域/类型/来源多样性"""
        if len(news_items) <= n_examples:
            return list(news_items)

        # 统计
        region_counts: dict[str, int] = {}
        for n in news_items:
            r = getattr(n, "region", "未知")
            region_counts[r] = region_counts.get(r, 0) + 1
        total = len(news_items)

        # 区域反权重：新闻越少的区域权重越高（弱势区域优先被选为示例）
        region_weights: dict[str, float] = {}
        for r in {getattr(n, "region", "未知") for n in news_items}:
            cnt = region_counts.get(r, 0)
            region_weights[r] = 1.0 / (cnt / max(total, 1) + 0.01)

        selected: list[NewsItem] = []
        covered_regions: set[str] = set()
        covered_sources: set[str] = set()
        available = list(news_items)

        for _ in range(n_examples):
            if not available:
                break

            def _score(item: NewsItem) -> float:
                r = getattr(item, "region", "未知")
                s = getattr(item, "source", "")
                title = getattr(item, "title", "")
                summary = getattr(item, "summary", "")
                # 6维加权
                region_diversity = (
                    (0 if r in covered_regions else 1) * region_weights.get(r, 1) * 30
                )
                weak_region = region_weights.get(r, 1) * 20
                summary_len = min(len(summary or ""), 200) / 200 * 15
                title_len = min(len(title or ""), 100) / 100 * 10
                source_diversity = (0 if s in covered_sources else 1) * 15
                # 中东减分（反anchoring）
                middle_east_penalty = -15 if r in ("中东", "Middle East") else 0
                return (
                    region_diversity
                    + weak_region
                    + summary_len
                    + title_len
                    + source_diversity
                    + middle_east_penalty
                )

            best = max(available, key=_score)
            selected.append(best)
            covered_regions.add(getattr(best, "region", ""))
            covered_sources.add(getattr(best, "source", ""))
            available.remove(best)

        return selected

    def _build_example_event(self, item: NewsItem) -> dict:
        """从单个 NewsItem 构建 few-shot 示例事件 JSON"""
        region = getattr(item, "region", "未知")
        source = getattr(item, "source", "")
        # 根据来源推断 source_lean（子串匹配，兼容全名和缩写）
        western_sources = [
            "cnn",
            "bbc",
            "reuters",
            "nyt",
            "bloomberg",
            "guardian",
            "wsj",
            "ft",
            "new york times",
            "wall street journal",
            "financial times",
            "ap",
            "associated press",
            "washington post",
        ]
        chinese_sources = [
            "新华社",
            "cgtn",
            "环球时报",
            "观察者网",
            "财新",
            "scmp",
            "xinhua",
            "global times",
            "china daily",
            "people's daily",
        ]
        russian_sources = ["rt", "tass", "sputnik", "ria novosti"]
        source_lower = source.lower()
        if any(s in source_lower for s in western_sources):
            lean = "偏好西方叙事"
        elif any(s in source_lower for s in chinese_sources):
            lean = "偏好中方叙事"
        elif any(s in source_lower for s in russian_sources):
            lean = "偏好俄方叙事"
        else:
            lean = "区域本土视角"

        return {
            "id": "evt_dyn",
            "title": getattr(item, "title", ""),
            "actors": [],
            "signal_level": "B",
            "region": region,
            "event_type": "外交博弈",
            "keywords": [],
            "summary": (getattr(item, "summary", "") or "")[:200],
            "source_count": 1,
            "source_lean": lean,
            "lean_reasoning": f"来自{source}的单源报道",
        }

    def _render_dynamic_examples(self, examples: list[NewsItem]) -> str:
        """将动态选取的新闻渲染为 few-shot 示例 prompt 文本"""
        if not examples:
            return ""
        parts = []
        for i, item in enumerate(examples, 1):
            event = self._build_example_event(item)
            input_text = (
                f"{i}. **[{getattr(item, 'source', '')}]** {getattr(item, 'title', '')}\n"
                f"   摘要: {getattr(item, 'summary', '')[:200]}"
            )
            parts.append(
                f"\n### 动态示例 {i}\n\n输入新闻：\n{input_text}\n\n"
                f"输出（参考模式，不代表相同事件）：\n```json\n"
                f"{json.dumps([event], ensure_ascii=False, indent=2)}\n```"
            )
        return "\n".join(parts)

    def _load_yesterday_events(self) -> str:
        """加载昨日事件列表用于 Stage 2 新兴信号判断"""
        from datetime import datetime, timedelta

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        history_dir = get_history_dir()

        # 尝试从历史文件加载
        history_file = history_dir / f"history_{yesterday}.json"
        if not history_file.exists():
            # 尝试从事件文件加载
            events_dir = history_dir.parent / "events"
            if events_dir.exists():
                events_file = events_dir / f"events_{yesterday}.json"
                if events_file.exists():
                    history_file = events_file

        if not history_file.exists():
            return "昨日无历史数据可供对比。"

        try:
            with open(history_file, encoding="utf-8") as f:
                data = json.load(f)

            # 提取事件标题
            yesterday_titles: list[str] = []
            if isinstance(data, dict):
                events = data.get("events", data.get("signals", []))
                if isinstance(events, list):
                    for e in events[:20]:
                        if isinstance(e, dict):
                            title = e.get("title", e.get("name", ""))
                            if title:
                                yesterday_titles.append(title)
                        elif isinstance(e, str):
                            yesterday_titles.append(e)
            elif isinstance(data, list):
                for e in data[:20]:
                    if isinstance(e, dict):
                        title = e.get("title", e.get("name", ""))
                        if title:
                            yesterday_titles.append(title)

            if not yesterday_titles:
                return "昨日无事件数据可供对比。"

            label = (
                "昨日已提取的事件主题" if self.lang == "zh" else "Event themes extracted yesterday"
            )
            return (
                f"**{label}**（如当前事件在此列表中大量出现，"
                f"则该聚类不是新兴信号，is_emerging=false）：\n"
                + "\n".join(f"- {t}" for t in yesterday_titles)
            )
        except Exception:
            return "昨日事件数据读取失败，无法判断新兴信号。"

    def _validate_stage1_events(self, events: list) -> list:
        """Stage 1 输出校验：必填字段 + 区域词汇 + 信号分布"""
        if not events:
            return events
        valid = []
        regions_seen: set[str] = set()
        s_count = a_count = 0
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                continue
            required = ["title", "actors", "signal_level", "region", "keywords", "summary"]
            missing = [f for f in required if f not in e or not e[f]]
            if missing:
                logger.warning(f"Stage 1 校验: 事件'{e.get('title', '?')}' 缺字段: {missing}")
                for f in missing:
                    e[f] = (
                        []
                        if f in ("actors", "keywords")
                        else ("无摘要" if f == "summary" else "未知")
                    )
            if "id" not in e or not e["id"]:
                e["id"] = f"evt_{i + 1:03d}"
            if "event_type" not in e:
                e["event_type"] = "外交博弈"
            if "source_count" not in e:
                e["source_count"] = 1
            regions_seen.add(str(e.get("region", "")))
            sl = str(e.get("signal_level", "C")).upper()
            if sl == "S":
                s_count += 1
            elif sl == "A":
                a_count += 1
            valid.append(e)
        if s_count > 1:
            logger.warning(f"Stage 1 校验: S级>{1} ({s_count})")
        if a_count > 5:
            logger.warning(f"Stage 1 校验: A级>{5} ({a_count})")
        if len(regions_seen) < 3:
            logger.warning(f"Stage 1 校验: 仅{len(regions_seen)}区域")
        logger.info(f"  Stage 1 校验通过: {len(valid)}事件, {len(regions_seen)}区域")
        return valid

    def _validate_stage2_clusters(self, clusters: list, events_count: int) -> list:
        """Stage 2 输出校验：聚类数量 + schema 补全"""
        if not clusters:
            return clusters
        valid = []
        for _i, c in enumerate(clusters):
            if not isinstance(c, dict):
                continue
            if "event_count" not in c:
                c["event_count"] = len(c.get("events", []))
            if "source_count" not in c:
                c["source_count"] = c.get("event_count", 1)
            if "consensus" not in c:
                sc = c.get("source_count", 1)
                c["consensus"] = "高" if sc > 5 else ("中" if sc > 1 else "低")
            if "is_emerging" not in c:
                c["is_emerging"] = False
            if "region" not in c:
                c["region"] = "全球"
            if "regions" not in c:
                c["regions"] = [c["region"]]
            valid.append(c)
        if events_count >= 10 and len(valid) < 5:
            logger.warning(f"Stage 2 校验: 聚类不足({len(valid)}, 期望>=8)")
        elif events_count >= 5 and len(valid) < 3:
            logger.warning(f"Stage 2 校验: 聚类不足({len(valid)}, 期望>=5)")
        logger.info(f"  Stage 2 校验通过: {len(valid)}聚类")
        return valid

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        retry=_is_retryable_api_error,
        reraise=True,
    )
    def _call_api(
        self,
        prompt: str,
        system_msg: str = "你是一名专业的国际政治分析员。",
        temperature: float = 0.3,
        max_tokens: int = 128000,
        timeout: int = 300,
    ) -> str:
        """调用 OpenAI 兼容 API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = None  # 显式初始化，确保 except 块中可访问
        try:
            response = self.session.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=timeout,
                verify=self.ssl_verify,
            )
            response.raise_for_status()
            data = response.json()

            choices = data.get("choices", [])
            if not choices:
                raise ValueError(
                    "API 响应中未包含有效结果（choices 为空），请检查 API 配置是否正确"
                )

            content = choices[0].get("message", {}).get("content", "")
            finish_reason = choices[0].get("finish_reason", "unknown")
            usage = data.get("usage", {})

            logger.info(
                f"AI 完成: finish_reason={finish_reason}, "
                f"in={usage.get('prompt_tokens', '?')}, "
                f"out={usage.get('completion_tokens', '?')}"
            )

            if not content:
                raise ValueError("API 返回了空的分析内容，请检查输入或稍后重试")

            if finish_reason == "length":
                logger.warning("API 输出被截断（输出长度达到上限），返回已生成的部分内容")
                return content

            return content

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if status == 429:
                logger.warning("API 请求被限流 (HTTP 429)，将自动重试")
            elif status >= 500:
                logger.warning(f"API 服务端错误 (HTTP {status})，将自动重试")
            else:
                logger.error(f"API 请求失败 (HTTP {status})，请检查 API_URL 和 API_KEY 配置")
            raise
        except json.JSONDecodeError:
            logger.error("API 返回了无法解析的响应格式，请检查 API_URL 是否正确")
            raise ValueError("API 返回了无法解析的响应，请检查 API 地址是否正确") from None
