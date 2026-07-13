"""
Tagged Block Parser v3 — 鲁棒的 XML 式 Tagged Block 解析器

支持从 LLM 输出中提取 <TAG_NAME>...</TAG_NAME> 格式的结构化数据。
相比 ```json ``` fence 方法，具有更强的容错能力：

特性:
- 状态机解析，不依赖正则
- 自动恢复：JSON 轻微损坏时尝试修复
- 支持嵌套 JSON 中的 < > 字符（在字符串字面量内）
- 支持多个同名 block（取第一个）
- Fallback: 无 Tagged Block 时尝试 ```json ``` fence
"""

import json
import logging
import re

logger = logging.getLogger("global_news.tagged_parser")

# 支持的 Tagged Block 名称
KNOWN_BLOCKS = {"REPORT_METADATA", "CITATIONS", "STRUCTURED_DATA"}


class TaggedParser:
    """Tagged Block 解析器 — 状态机 + 自动修复"""

    @staticmethod
    def parse(content: str) -> dict[str, dict | None]:
        """从内容中提取所有 Tagged Block

        Returns:
            {"report_metadata": {...}, "citations": {...}, "structured_data": {...}}
            未找到的 block 值为 None
        """
        result: dict[str, dict | None] = {}
        for block_name in KNOWN_BLOCKS:
            result[block_name.lower()] = TaggedParser._extract_block(content, block_name)

        # Fallback: 如果没有找到任何 Tagged Block，尝试 ```json ``` fence
        if all(v is None for v in result.values()):
            result = TaggedParser._fallback_json_fence(content)

        return result

    @staticmethod
    def _extract_block(content: str, tag: str) -> dict | None:
        """提取单个 tagged block"""
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"

        start = content.find(open_tag)
        if start == -1:
            return None

        start += len(open_tag)
        end = content.find(close_tag, start)
        if end == -1:
            # 尝试找到下一个 < 或文件末尾
            next_tag = content.find("<", start)
            if next_tag != -1:
                end = next_tag
            else:
                end = len(content)

        raw = content[start:end].strip()

        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 尝试修复常见 JSON 问题
        fixed = TaggedParser._repair_json(raw)
        if fixed:
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass

        logger.warning(f"Tagged block <{tag}> JSON 解析失败，原始长度: {len(raw)}")
        return None

    @staticmethod
    def _repair_json(raw: str) -> str | None:
        """尝试修复常见的 JSON 格式问题"""
        fixed = raw

        # 1. 移除尾随逗号
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

        # 2. 修复单引号
        in_string = False
        chars = list(fixed)
        for i, ch in enumerate(chars):
            if ch == '"' and (i == 0 or chars[i - 1] != "\\"):
                in_string = not in_string
            elif ch == "'" and not in_string:
                chars[i] = '"'
        fixed = "".join(chars)

        # 3. 补齐截断的 JSON — 统计开闭括号
        stack: list[str] = []
        in_str = False
        for ch in fixed:
            if ch == '"' and not in_str:
                in_str = True
                continue
            if ch == '"' and in_str:
                in_str = False
                continue
            if in_str:
                continue
            if ch in "[{":
                stack.append("]" if ch == "[" else "}")
            elif ch in "]}":
                if stack and stack[-1] == ch:
                    stack.pop()

        if stack:
            fixed += "".join(reversed(stack))

        # 4. 尝试移除标签前的多余文本（有时模型会在 block 内输出说明文字）
        # 找到第一个 { 或 [
        json_start = -1
        for i, ch in enumerate(fixed):
            if ch in "[{":
                json_start = i
                break
        if json_start > 0:
            fixed = fixed[json_start:]

        # 找到最后一个 } 或 ]
        json_end = -1
        for i in range(len(fixed) - 1, -1, -1):
            if fixed[i] in "]}":
                json_end = i
                break
        if json_end != -1 and json_end < len(fixed) - 1:
            fixed = fixed[: json_end + 1]

        if fixed != raw:
            return fixed
        return None

    @staticmethod
    def _fallback_json_fence(content: str) -> dict[str, dict | None]:
        """Fallback: 从 ```json ``` fence 中提取 JSON"""
        result: dict[str, dict | None] = {
            "report_metadata": None,
            "citations": None,
            "structured_data": None,
        }

        json_blocks = re.findall(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue

            # 根据内容判断属于哪个 block
            if "events" in data:
                result["structured_data"] = data
            elif "citation_density" in data or "claims" in data:
                result["citations"] = data
            elif "quality_self_assessment" in data or "regions_covered" in data:
                result["report_metadata"] = data

        return result


def parse_output(content: str) -> dict:
    """便捷函数：解析 LLM 输出

    Returns:
        {
            "markdown_body": str,         # 日报正文（去掉 Tagged Blocks）
            "metadata": dict | None,
            "citations": dict | None,
            "structured_data": dict | None,
        }
    """
    parser = TaggedParser()

    # 提取 Tagged Blocks
    blocks = parser.parse(content)

    # 提取纯 Markdown 正文（去掉所有 Tagged Blocks）
    markdown_body = content
    for tag in KNOWN_BLOCKS:
        markdown_body = re.sub(rf"<{tag}>.*?</{tag}>", "", markdown_body, flags=re.DOTALL)
    # 也去掉 ```json ``` fence（如果有）
    markdown_body = re.sub(r"```json\s*.*?\s*```", "", markdown_body, flags=re.DOTALL)
    markdown_body = markdown_body.strip()

    return {
        "markdown_body": markdown_body,
        "metadata": blocks.get("report_metadata"),
        "citations": blocks.get("citations"),
        "structured_data": blocks.get("structured_data"),
    }
