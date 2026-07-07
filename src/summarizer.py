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
import time
from typing import Optional

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from src.fetcher import NewsItem
from src.utils import load_prompt_template, get_history_dir, get_prompts_dir

logger = logging.getLogger("global_news.summarizer")


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
        if not events_json:
            logger.warning("Stage 1 失败，回退到单阶段模式")
            return self._fallback_single_stage(news_items)

        # Stage 2: 事件聚类
        logger.info("  Stage 2/3: 事件聚类...")
        clusters_json = self._stage2_cluster_events(events_json)
        if not clusters_json:
            logger.warning("Stage 2 失败，回退到单阶段模式")
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

    def _stage1_extract_events(self, news_items: list[NewsItem]) -> Optional[list]:
        """从新闻中提取结构化事件"""
        news_content = self._format_news_for_prompt(news_items)

        prompt_template = self._load_stage_prompt("stage1_event_extraction.txt")
        prompt = prompt_template.format(news_content=news_content)

        try:
            sys_msg = ("You are a precise event extractor. Output only valid JSON arrays, no other text."
                       if self.lang == "en"
                       else "你是一个精确的事件提取器。只输出合法 JSON 数组，不要输出任何其他文字。")
            result = self._call_api(
                prompt,
                system_msg=sys_msg,
                temperature=0.1,
                max_tokens=384000,
                timeout=300,
            )

            if not result:
                logger.warning("Stage 1 API 返回空内容")
                return None

            # 记录返回内容的前500字符用于调试
            logger.debug(f"Stage 1 返回内容前500字符: {result[:500]}")

            events = self._parse_json(result)

            if events is None:
                logger.warning("Stage 1 JSON 解析失败，返回 None")
                return None

            if isinstance(events, list):
                # 验证列表中的每个元素是否为字典
                valid_events = []
                for i, e in enumerate(events):
                    if isinstance(e, dict) and 'title' in e:
                        valid_events.append(e)
                    else:
                        logger.warning(f"Stage 1 第 {i} 个事件格式异常: {type(e)}")
                if valid_events:
                    logger.info(f"  Stage 1 完成: 提取 {len(valid_events)} 个事件")
                    return valid_events
                else:
                    logger.warning("Stage 1 无有效事件")
                    return None
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

    def _stage2_cluster_events(self, events: list) -> Optional[list]:
        """将事件按主题聚类"""
        # 使用全部事件进行聚类（不再截断）
        events_content = json.dumps(events, ensure_ascii=False, indent=2)

        prompt_template = self._load_stage_prompt("stage2_event_clustering.txt")
        prompt = prompt_template.format(events_content=events_content, events_count=len(events))

        try:
            sys_msg = ("You are an event clustering analyst. Output only valid JSON arrays, no other text."
                       if self.lang == "en"
                       else "你是一个事件聚类分析师。只输出合法 JSON 数组，不要输出任何其他文字。")
            result = self._call_api(
                prompt,
                system_msg=sys_msg,
                temperature=0.1,
                max_tokens=8000,
                timeout=120,
            )
            if not result:
                logger.warning("Stage 2 返回空内容，重试一次...")
                result = self._call_api(
                    prompt,
                    system_msg=sys_msg,
                    temperature=0.1,
                    max_tokens=8000,
                    timeout=120,
                )
            clusters = self._parse_json(result)
            if isinstance(clusters, list):
                logger.info(f"  Stage 2 完成: {len(clusters)} 个聚类")
                return clusters
            elif isinstance(clusters, dict) and "clusters" in clusters:
                logger.info(f"  Stage 2 完成: {len(clusters['clusters'])} 个聚类")
                return clusters["clusters"]
            else:
                logger.warning("Stage 2 返回格式异常")
                return None
        except Exception as e:
            logger.error(f"Stage 2 失败: {e}")
            return None

    # ═══════════════════════════════════════════════
    # Stage 3: 日报生成
    # ═══════════════════════════════════════════════

    def _stage3_generate_report(self, clusters: list, events: list,
                                 news_items: list[NewsItem]) -> Optional[str]:
        """基于聚类结果生成最终 Markdown 日报（含 h2 校验和自动重试）"""
        # 检查 Premium 状态
        try:
            from src.premium import is_premium_enabled, get_chapter_limit
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
        market_data = "市场数据暂未接入。"
        if is_premium:
            try:
                from src.market_data import get_market_provider
                market_data = get_market_provider().get_data_for_prompt()
            except Exception as e:
                logger.warning(f"获取市场数据失败: {e}")

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

        prompt = prompt_template.format(
            source_count=len(set(n.source for n in news_items)),
            news_count=len(news_items),
            history_context=history_context,
            news_content=f"## 事件聚类结果\n\n{clusters_text}\n\n## 完整事件列表\n\n{events_text}\n\n## 实时市场数据\n\n{market_data}\n\n## 原始新闻链接\n\n{news_links}",
        )

        # 添加版本限制提示
        if premium_hint:
            prompt = prompt + premium_hint

        # 根据版本设置校验阈值
        MIN_H2 = chapter_limit  # 免费版3章，付费版10章
        result = None

        for attempt in range(3):
            try:
                sys_msg = ("You are a professional international political analyst serving a private OSINT strategic observation system. Your analysis must be professional,冷静, specific, and free of empty words."
                           if self.lang == "en"
                           else "你是一名专业的国际政治分析员，服务于私人 OSINT 战略观察系统。你的分析必须专业、冷静、具体，不说空话。")
                result = self._call_api(
                    prompt,
                    system_msg=sys_msg,
                    temperature=0.3,
                    max_tokens=384000,
                    timeout=300,
                )

                h2_count = len(re.findall(r'^##\s+', result, re.MULTILINE))
                logger.info(f"  Stage 3 第{attempt + 1}次: {len(result)} 字符, {h2_count} 个 h2 章节")

                if h2_count >= MIN_H2:
                    logger.info(f"  Stage 3 通过校验 (h2 >= {MIN_H2})")
                    return result

                if attempt < 2:
                    logger.warning(
                        f"  Stage 3 h2 章节不足 ({h2_count} < {MIN_H2})，"
                        f"第 {attempt + 2} 次重试（追加强制指令）..."
                    )
                    # 在 prompt 末尾追加更强的指令（根据版本动态调整）
                    if self.lang == "en":
                        if is_premium:
                            chapter_desc = "all 10 ## chapters (I through X)"
                        else:
                            chapter_desc = f"all {chapter_limit} ## chapters"
                        prompt = prompt + (
                            f"\n\n⚠️ Previous output was missing {MIN_H2 - h2_count} required chapters. "
                            f"Regenerate the complete briefing immediately, must include {chapter_desc}. "
                            f"Even if a chapter lacks sufficient information today, you must output the chapter heading and briefly explain. "
                            f"Do not skip chapters due to length constraints — use concise expressions instead."
                        )
                    else:
                        if is_premium:
                            chapter_desc = "全部 10 个 ## 章节（一至十）"
                        else:
                            chapter_desc = f"全部 {chapter_limit} 个 ## 章节"
                        prompt = prompt + (
                            f"\n\n⚠️ 上一条输出缺少了 {MIN_H2 - h2_count} 个必要章节。"
                            f"请立即重新生成完整日报，必须包含{chapter_desc}。"
                            f"即使某些章节当日缺乏信息，也必须输出章节标题并简述原因。"
                            f"禁止以篇幅不足为由跳过章节——用精简的表达即可。"
                        )
                else:
                    logger.warning(f"  Stage 3 3次尝试后仍不足 {MIN_H2} 个章节，使用最后一次结果")

            except Exception as e:
                logger.error(f"Stage 3 第{attempt + 1}次失败: {e}")
                if attempt >= 2:
                    return None

        return result

    # ═══════════════════════════════════════════════
    # Fallback: 单阶段模式（兼容旧版）
    # ═══════════════════════════════════════════════

    def _fallback_single_stage(self, news_items: list[NewsItem]) -> str:
        """单阶段 fallback：直接用完整 prompt 生成日报"""
        logger.info("使用单阶段 fallback 模式...")
        news_content = self._format_news_for_prompt(news_items)
        history_context = self._load_history_context()
        prompt_template = load_prompt_template(lang=self.lang)

        prompt = prompt_template.format(
            source_count=len(set(n.source for n in news_items)),
            news_count=len(news_items),
            history_context=history_context,
            news_content=news_content,
        )

        return self._call_api(prompt)

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
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    def _format_news_for_prompt(self, news_items: list[NewsItem]) -> str:
        """格式化新闻为 prompt 输入（精简版）"""
        lines = []
        by_region: dict[str, list[NewsItem]] = {}
        for news in news_items:
            by_region.setdefault(news.region, []).append(news)

        for region, items in by_region.items():
            lines.append(f"\n### 【{region}】\n")
            for i, item in enumerate(items, 1):
                lines.append(f"{i}. **[{item.source}]** {item.title}")
                if item.summary:
                    lines.append(f"   摘要: {item.summary[:400]}")
                lines.append(f"   发布: {item.published}")

        return "\n".join(lines)

    def _format_news_links(self, news_items: list[NewsItem]) -> str:
        """格式化新闻链接列表（用于日报的'原始新闻链接'章节）"""
        lines = []
        by_region: dict[str, list[NewsItem]] = {}
        for news in news_items:
            by_region.setdefault(news.region, []).append(news)

        for region, items in by_region.items():
            lines.append(f"\n**{region}：**")
            for item in items:
                lines.append(f"- {item.source} - {item.title} （{item.published}）[阅读原文]({item.url})")

        return "\n".join(lines)

    def _load_history_context(self) -> str:
        """加载最近 N 天的历史数据（精简版：每天 top 3）"""
        history_dir = get_history_dir()
        if not history_dir.exists():
            return "暂无历史数据。"

        # 检查 Premium 状态，限制历史天数
        try:
            from src.premium import is_premium_enabled, get_history_days
            is_premium = is_premium_enabled()
            history_days = get_history_days()
        except ImportError:
            is_premium = False
            history_days = 0  # 免费版无历史

        # 免费版不加载历史
        if history_days == 0:
            return "暂无历史数据。"

        from datetime import datetime, timedelta

        context_parts = []
        today = datetime.now()

        for i in range(1, min(history_days + 1, 4)):  # 最多加载3天
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            history_file = history_dir / f"history_{date_str}.json"

            if history_file.exists():
                try:
                    with open(history_file, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    signals = data.get("signals", [])
                    clusters = data.get("clusters", [])

                    if signals or clusters:
                        context_parts.append(f"\n--- {date_str} ---")
                        if clusters:
                            context_parts.append("事件聚类:")
                            for c in clusters[:3]:
                                context_parts.append(f"  - {c}")
                        if signals:
                            context_parts.append("风险信号:")
                            for s in signals[:3]:
                                context_parts.append(f"  - {s}")

                except (json.JSONDecodeError, KeyError):
                    pass

        return "\n".join(context_parts) if context_parts else "暂无历史数据。"

    def _parse_json(self, text: str) -> Optional[object]:
        """从 AI 输出中解析 JSON（支持多种格式）"""
        if not text:
            return None

        import re

        # 0. 移除 <thinking>...</thinking> 标签内容（CoT推理步骤）
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL).strip()

        # 1. 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 从 ```json ... ``` 代码块中提取
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 3. 从第一个 [ 或 { 开始解析
        for start_char, end_char in [('[', ']'), ('{', '}')]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass

        # 4. 清理常见格式问题后重试
        # 找到第一个 [ 或 { 的位置
        json_start = -1
        for i, char in enumerate(text):
            if char in '[{':
                json_start = i
                break
        if json_start > 0:
            cleaned = text[json_start:]
            # 找到最后一个 ] 或 } 的位置
            for end_char in [']', '}']:
                last_idx = cleaned.rfind(end_char)
                if last_idx != -1:
                    cleaned = cleaned[:last_idx + 1]
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        pass

        # 5. 尝试修复常见JSON格式问题
        # 移除尾随逗号
        text_fixed = re.sub(r',\s*([\]}])', r'\1', text)
        try:
            return json.loads(text_fixed)
        except json.JSONDecodeError:
            pass

        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
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
                raise ValueError("API 返回空的 choices")

            content = choices[0].get("message", {}).get("content", "")
            finish_reason = choices[0].get("finish_reason", "unknown")
            usage = data.get("usage", {})

            logger.info(f"AI 完成: finish={finish_reason}, "
                        f"in={usage.get('prompt_tokens', '?')}, "
                        f"out={usage.get('completion_tokens', '?')}")

            if not content:
                raise ValueError("API 返回空内容")

            if finish_reason == "length":
                logger.warning("AI 输出被截断（达到 max_tokens 限制）")

            return content

        except requests.HTTPError as e:
            logger.error(f"API HTTP 错误: {e.response.status_code} - {e.response.text[:500]}")
            raise
        except json.JSONDecodeError:
            logger.error(f"API 返回非 JSON 响应: {response.text[:500]}")
            raise ValueError("API 返回无效 JSON")
