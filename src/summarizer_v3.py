"""
Summarizer v3 — 单阶段日报生成

将 500 条新闻 + 历史 + 市场数据 + 演化追踪一次性输入模型，
输出完整 10 章日报 (Markdown + Tagged Block JSON)。

核心改进（vs v2 三阶段）:
- 1 次 API 调用代替 4 次 (Stage 1/2/3/0)
- 模型拥有完整上下文，信号分级更准确
- 嵌入式 JSON 输出（无需后处理提取）
- 引用追踪（每句分析可追溯到源新闻）
- max_tokens=131072 (128K)，充分利用 384K 输出

保留 v2 作为回退：
  python run.py --pipeline v2  # 使用旧三阶段
  python run.py --pipeline v3  # 使用新单阶段（默认）
"""

import logging
import time

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.context_builder import ContextBuilder
from src.fetcher import NewsItem
from src.prompt_builder import PromptBuilder
from src.tagged_parser import parse_output

logger = logging.getLogger("global_news.summarizer_v3")


def _is_retryable_api_error(retry_state) -> bool:
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


class NewsSummarizerV3:
    """单阶段 AI 日报生成器"""

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
        self.prompt_builder = PromptBuilder(lang=lang)

        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        if not ssl_verify:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ═══════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════

    def summarize(self, news_items: list[NewsItem]) -> dict:
        """单次 API 调用生成完整日报

        Returns:
            {
                "markdown": str,           # 日报正文 (Markdown)
                "metadata": dict | None,   # REPORT_METADATA block
                "citations": dict | None,  # CITATIONS block
                "structured_data": dict | None,  # STRUCTURED_DATA block
                "raw_output": str,         # API 原始输出
                "elapsed": float,          # API 调用耗时
                "usage": dict,             # token 使用量
            }
        """
        if not news_items:
            return {"markdown": "# 今日无新闻数据\n\n未获取到有效新闻。", "error": "no_news"}
        if not self.api_key:
            raise ValueError("API_KEY 未配置")

        # 1. 使用 ContextBuilder 组装上下文
        builder = ContextBuilder(lang=self.lang)
        builder.add_news(news_items)
        builder.add_market_data()
        builder.add_history(days=90)
        builder.add_evolution()
        builder.add_source_health(news_items)
        builder.add_yesterday_events()
        dynamic_context = builder.build()
        ctx_stats = builder.stats()

        # 2. 组装 Prompt
        system_msg = self.prompt_builder.build_system()
        static_prompt = self.prompt_builder.build_user_prompt()
        user_prompt = static_prompt + "\n\n" + dynamic_context

        prompt_chars = len(user_prompt)
        logger.info(
            f"v3 单阶段: prompt={prompt_chars:,}chars (~{prompt_chars // 2:,}tokens), "
            f"news={len(news_items)}条"
        )

        # 3. API 调用
        start_time = time.time()
        raw_output = self._call_api(system_msg, user_prompt)
        elapsed = time.time() - start_time

        if not raw_output:
            logger.error("v3 API 返回空")
            return {"markdown": "", "error": "api_empty", "context_stats": ctx_stats}

        # 4. 解析输出
        parsed = parse_output(raw_output)

        logger.info(
            f"v3 完成: {elapsed:.0f}s, "
            f"events={len(parsed.get('structured_data', {}).get('events', []) or [])}, "
            f"md={len(parsed.get('markdown_body', '')):,}chars"
        )

        return {
            "markdown": parsed["markdown_body"],
            "metadata": parsed["metadata"],
            "citations": parsed["citations"],
            "structured_data": parsed["structured_data"],
            "raw_output": raw_output,
            "elapsed": elapsed,
            "context_stats": ctx_stats,
        }

    # ═══════════════════════════════════════════════
    # API 调用
    # ═══════════════════════════════════════════════

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        retry=_is_retryable_api_error,
        reraise=True,
    )
    def _call_api(self, system_msg: str, user_prompt: str) -> str:
        """调用 OpenAI 兼容 API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 131072,  # 128K — 充分利用 384K 输出
        }

        response = self.session.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=600,
            verify=self.ssl_verify,
        )
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", [])
        if not choices:
            raise ValueError("API 响应中未包含有效结果")

        content = choices[0].get("message", {}).get("content", "")
        finish_reason = choices[0].get("finish_reason", "unknown")
        usage = data.get("usage", {})

        logger.info(
            f"v3 API: finish={finish_reason}, "
            f"in={usage.get('prompt_tokens', '?')}, "
            f"out={usage.get('completion_tokens', '?')}"
        )

        if finish_reason == "length":
            logger.warning("v3 API 输出被截断！考虑增加 max_tokens")

        return content
