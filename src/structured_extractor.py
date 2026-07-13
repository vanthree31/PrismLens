"""
结构化提取器 — 第二次 API 调用

从 AI 日报中提取结构化 JSON 数据：
- 事件列表（含 actors, domains, signal_level, trend）
- 风险表（top_risks）
- 观察哨（watchpoints）
- 区域信号分类（政府/金融/能源/智库）
"""

import json
import logging
import re

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.event_graph import DailyEventGraph, Event, canonicalize_event_id, merge_events
from src.utils import get_prompts_dir

logger = logging.getLogger("global_news.extractor")


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


_prompt_cache: dict[str, str] = {}


def _load_extraction_prompt(lang: str = "zh") -> str:
    """从 prompts/stage0_extraction.txt 加载提取 prompt（根据语言选择，带缓存）"""
    if lang in _prompt_cache:
        return _prompt_cache[lang]
    if lang == "en":
        en_path = get_prompts_dir() / "stage0_extraction_en.txt"
        if en_path.exists():
            with open(en_path, encoding="utf-8") as f:
                content = f.read()
                _prompt_cache[lang] = content
                return content
    prompt_path = get_prompts_dir() / "stage0_extraction.txt"
    if prompt_path.exists():
        with open(prompt_path, encoding="utf-8") as f:
            content = f.read()
            _prompt_cache[lang] = content
            return content
    # Fallback: 最小化 prompt
    fallback = (
        "从以下日报中提取结构化 JSON 数据。只输出合法 JSON。"
        if lang == "zh"
        else "Extract structured JSON data from the following briefing. Output valid JSON only."
    )
    content = fallback + "\n\n{report_content}"
    _prompt_cache[lang] = content
    return content


class StructuredExtractor:
    """从日报中提取结构化数据"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model_name: str,
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

    def extract(self, report_content: str) -> dict | None:
        """
        从日报内容中提取结构化数据

        Args:
            report_content: AI 生成的 Markdown 日报

        Returns:
            结构化数据字典，失败返回 None
        """
        logger.info("正在提取结构化数据（第二次 API 调用）...")

        prompt_template = _load_extraction_prompt(lang=self.lang)
        prompt = prompt_template.format(
            report_content=report_content[:15000]
        )  # 限制长度避免超 token

        try:
            result = self._call_api(prompt)
            if result:
                # 尝试从响应中提取 JSON
                data = self._parse_json(result)
                if data:
                    logger.info(
                        f"结构化提取成功: {len(data.get('events', []))} 个事件, "
                        f"{len(data.get('top_risks', []))} 个风险, "
                        f"{len(data.get('watchpoints', []))} 个观察哨"
                    )
                    return data
                else:
                    logger.warning("结构化提取: AI 返回的内容无法解析为 JSON")
                    logger.debug(f"原始响应(前500字符): {result[:500]}")
                    logger.debug(f"原始响应(后200字符): {result[-200:]}")
                    return None
        except Exception as e:
            logger.error(f"结构化提取失败: {e}", exc_info=True)
            return None

    def _parse_json(self, text: str) -> dict | None:
        """从 AI 响应中提取 JSON"""
        # 0. 移除 <thinking>...</thinking> 标签（DeepSeek CoT 推理步骤）
        text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()

        # 1. 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 从 markdown code block 中提取
        start_marker = re.search(r"```(?:json)?\s*\{", text)
        if start_marker:
            pos = start_marker.end() - 1  # 指向 {
            end = self._find_matching_brace(text, pos)
            if end is not None:
                try:
                    return json.loads(text[pos : end + 1])
                except json.JSONDecodeError:
                    pass
            # 回退到 ``` 结束标记
            end_marker = re.search(r"\}\s*```", text[pos:])
            if end_marker:
                try:
                    return json.loads(text[pos : pos + end_marker.start() + 1])
                except json.JSONDecodeError:
                    pass

        # 尝试找到第一个 { 到最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _find_matching_brace(text: str, start: int) -> int | None:
        """从 start 位置找到匹配的 } ，正确处理字符串字面量"""
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                if ch == "\\":
                    escape_next = True  # keep escaping for the char after backslash
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"' and not in_string:
                in_string = True
                continue
            if ch == '"' and in_string:
                in_string = False
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return None

    @retry(
        retry=_is_retryable_api_error,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=30),
        reraise=True,
    )
    def _call_api(self, prompt: str) -> str | None:
        """调用 API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.lang == "en":
            system_content = "You are a precise data extractor. Output only valid JSON, no other text or explanation."
        else:
            system_content = (
                "你是一个精确的数据提取器。只输出合法 JSON，不要输出任何其他文字或解释。"
            )
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": system_content,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 8192,
        }

        response = self.session.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=120,
            verify=self.ssl_verify,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "")
        return None


def _ensure_list(value) -> list:
    """确保值为 list 类型，处理 AI 返回字符串的情况"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        # 处理 "美国,中国" 或 "美国、中国" 格式的逗号分隔字符串
        if "," in value or "、" in value:
            return [item.strip() for item in re.split(r"[,、]", value) if item.strip()]
        return [value] if value else []
    return []


def build_event_graph_from_extraction(extracted: dict, date_str: str) -> DailyEventGraph:
    """从提取的结构化数据构建事件图谱"""
    events = []
    for e in extracted.get("events", []):
        # signal_level 归一化为大写
        raw_level = e.get("signal_level", "C")
        signal_level = raw_level.upper() if isinstance(raw_level, str) else "C"
        if signal_level not in ("S", "A", "B", "C"):
            signal_level = "C"

        event = Event(
            event_id=canonicalize_event_id(e.get("title", "")),
            title=e.get("title", ""),
            signal_level=signal_level,
            confidence=e.get("confidence", "中"),
            actors=_ensure_list(e.get("actors", [])),
            domains=_ensure_list(e.get("domains", [])),
            trend=e.get("trend", "stable"),
            summary=e.get("summary", ""),
            sources=_ensure_list(e.get("sources", [])),
            related_events=_ensure_list(e.get("related_events", [])),
            phase=e.get("phase", "diplomatic"),
            source_lean=e.get("source_lean", "中立"),
            lean_reasoning=e.get("lean_reasoning", ""),
        )
        events.append(event)

    # 合并重复事件
    events = merge_events(events)

    return DailyEventGraph(
        date=date_str,
        events=events,
        top_risks=extracted.get("top_risks", []),
        watchpoints=extracted.get("watchpoints", []),
        actor_mentions=extracted.get("actor_mentions", {}),
    )
