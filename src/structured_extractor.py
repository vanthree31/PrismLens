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
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from src.event_graph import Event, DailyEventGraph, canonicalize_event_id, merge_events
from src.utils import load_prompt_template, get_today_str, get_prompts_dir

logger = logging.getLogger("global_news.extractor")


def _load_extraction_prompt(lang: str = "zh") -> str:
    """从 prompts/stage0_extraction.txt 加载提取 prompt（根据语言选择）"""
    if lang == "en":
        en_path = get_prompts_dir() / "stage0_extraction_en.txt"
        if en_path.exists():
            with open(en_path, "r", encoding="utf-8") as f:
                return f.read()
    prompt_path = get_prompts_dir() / "stage0_extraction.txt"
    if prompt_path.exists():
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    # Fallback: 最小化 prompt
    fallback = "从以下日报中提取结构化 JSON 数据。只输出合法 JSON。" if lang == "zh" else "Extract structured JSON data from the following briefing. Output valid JSON only."
    return fallback + "\n\n{report_content}"


EXTRACTION_PROMPT = _load_extraction_prompt()


class StructuredExtractor:
    """从日报中提取结构化数据"""

    def __init__(self, api_url: str, api_key: str, model_name: str, proxy: str = "", ssl_verify: bool = True, lang: str = "zh"):
        self.api_url = api_url
        self.api_key = api_key
        self.model_name = model_name
        self.ssl_verify = ssl_verify
        self.lang = lang

        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def extract(self, report_content: str) -> Optional[dict]:
        """
        从日报内容中提取结构化数据

        Args:
            report_content: AI 生成的 Markdown 日报

        Returns:
            结构化数据字典，失败返回 None
        """
        logger.info("正在提取结构化数据（第二次 API 调用）...")

        prompt_template = _load_extraction_prompt(lang=self.lang)
        prompt = prompt_template.format(report_content=report_content[:15000])  # 限制长度避免超 token

        try:
            result = self._call_api(prompt)
            if result:
                # 尝试从响应中提取 JSON
                data = self._parse_json(result)
                if data:
                    logger.info(f"结构化提取成功: {len(data.get('events', []))} 个事件, "
                                f"{len(data.get('top_risks', []))} 个风险, "
                                f"{len(data.get('watchpoints', []))} 个观察哨")
                    return data
                else:
                    logger.warning("结构化提取: AI 返回的内容无法解析为 JSON")
                    logger.warning(f"原始响应(前500字符): {result[:500]}")
                    logger.warning(f"原始响应(后200字符): {result[-200:]}")
                    return None
        except Exception as e:
            logger.error(f"结构化提取失败: {type(e).__name__}: {e}")
            return None

    def _parse_json(self, text: str) -> Optional[dict]:
        """从 AI 响应中提取 JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 从 markdown code block 中提取
        start_marker = re.search(r'```(?:json)?\s*\{', text)
        if start_marker:
            pos = start_marker.end() - 1  # 指向 {
            end = self._find_matching_brace(text, pos)
            if end is not None:
                try:
                    return json.loads(text[pos:end+1])
                except json.JSONDecodeError:
                    pass
            # 回退到 ``` 结束标记
            end_marker = re.search(r'\}\s*```', text[pos:])
            if end_marker:
                try:
                    return json.loads(text[pos:pos+end_marker.start()+1])
                except json.JSONDecodeError:
                    pass

        # 尝试找到第一个 { 到最后一个 }
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end+1])
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _find_matching_brace(text: str, start: int) -> Optional[int]:
        """从 start 位置找到匹配的 } ，正确处理字符串字面量"""
        depth = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
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
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i
        return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=5, max=30), reraise=True)
    def _call_api(self, prompt: str) -> Optional[str]:
        """调用 API"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "你是一个精确的数据提取器。只输出合法 JSON，不要输出任何其他文字或解释。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 384000,
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


def build_event_graph_from_extraction(extracted: dict, date_str: str) -> DailyEventGraph:
    """从提取的结构化数据构建事件图谱"""
    events = []
    for e in extracted.get("events", []):
        event = Event(
            event_id=canonicalize_event_id(e.get("title", "")),
            title=e.get("title", ""),
            signal_level=e.get("signal_level", "C"),
            confidence=e.get("confidence", "中"),
            actors=e.get("actors", []),
            domains=e.get("domains", []),
            trend=e.get("trend", "stable"),
            summary=e.get("summary", ""),
            sources=e.get("sources", []),
            phase=e.get("phase", "diplomatic"),
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
