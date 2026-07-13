"""
Prompt Builder v3 — 模块化 Prompt 组装

从 prompts/v3/{lang}/ 加载分层 Prompt，按语言和配置组装。
支持消融实验（通过 active_layers 控制启用层）。

Layer 结构:
  00_system.txt    — Layer 0: 角色定义 (system message)
  01_editorial.txt — Layer 1: 编辑策略
  02_schema.txt    — Layer 2: 输出结构（含 Tagged Block 定义）
  03_domain.txt    — Layer 3: 领域知识（受控词汇表、传导链）
  04_quality.txt   — Layer 4: 质量规则（自检清单）
  06_style.txt     — Layer 6: 写作风格
"""

import logging

from src.utils import get_prompts_dir

logger = logging.getLogger("global_news.prompt_builder")

# Layer 编号 → 文件名映射
LAYER_FILES: dict[str, str] = {
    "system": "00_system.txt",
    "editorial": "01_editorial.txt",
    "schema": "02_schema.txt",
    "domain": "03_domain.txt",
    "quality": "04_quality.txt",
    "style": "06_style.txt",
}

# 默认启用的层（全部）
DEFAULT_LAYERS: set[str] = {"editorial", "schema", "domain", "quality", "style"}

# 消融实验配置
ABLATION_CONFIGS: dict[str, set[str]] = {
    "full": {"editorial", "schema", "domain", "quality", "style"},
    "no_editorial": {"schema", "domain", "quality", "style"},
    "no_quality": {"editorial", "schema", "domain", "style"},
    "no_domain": {"editorial", "schema", "quality", "style"},
    "no_style": {"editorial", "schema", "domain", "quality"},
    "core_only": {"editorial", "schema", "domain"},
    "minimal": {"editorial", "schema"},
}


class PromptBuilder:
    """模块化 Prompt 组装器"""

    def __init__(self, lang: str = "zh"):
        self.lang = lang
        self.base_dir = get_prompts_dir() / "v3" / lang
        self._cache: dict[str, str] = {}

    def _load_layer(self, layer_name: str) -> str:
        """加载单个 prompt layer，带缓存"""
        if layer_name in self._cache:
            return self._cache[layer_name]

        filename = LAYER_FILES.get(layer_name)
        if not filename:
            logger.warning(f"未知 layer: {layer_name}")
            return ""

        file_path = self.base_dir / filename
        if not file_path.exists():
            logger.warning(f"Prompt layer 文件不存在: {file_path}")
            return ""

        content = file_path.read_text(encoding="utf-8")
        self._cache[layer_name] = content
        return content

    def build_system(self) -> str:
        """构建 system message (Layer 0)"""
        return self._load_layer("system").strip()

    def build_user_prompt(
        self,
        active_layers: set[str] | None = None,
    ) -> str:
        """构建 user prompt（Layer 1-6）

        Args:
            active_layers: 启用的层，None=全部启用
        """
        if active_layers is None:
            active_layers = DEFAULT_LAYERS

        parts = []
        for layer_name in ["editorial", "schema", "domain", "quality", "style"]:
            if layer_name in active_layers:
                content = self._load_layer(layer_name)
                if content:
                    parts.append(content)

        return "\n\n".join(parts)

    def build_dynamic_context(
        self,
        news_formatted: str,
        history_context: str = "",
        market_data: str = "",
        evolution_context: str = "",
        source_health: str = "",
        yesterday_events: str = "",
    ) -> str:
        """构建动态上下文（Layer 5: 每日变化的数据）

        Args:
            news_formatted: 格式化后的新闻列表
            history_context: 历史趋势仪表盘
            market_data: 实时市场数据
            evolution_context: 演化追踪数据
            source_health: 新闻源健康状态
            yesterday_events: 昨日事件参考
        """
        parts = ["## 输入数据 (Dynamic Context)\n"]
        parts.append(news_formatted)

        if history_context and "暂无" not in history_context:
            parts.append(f"\n### 历史趋势仪表盘\n{history_context}\n")

        if market_data and "暂未接入" not in market_data:
            parts.append(f"\n### 实时市场数据\n{market_data}\n")

        if evolution_context:
            parts.append(f"\n### 活跃事件演化追踪\n{evolution_context}\n")

        if source_health:
            parts.append(f"\n### 新闻源健康状态\n{source_health}\n")

        if yesterday_events and "无历史数据" not in yesterday_events:
            parts.append(f"\n### 昨日事件参考\n{yesterday_events}\n")

        return "\n".join(parts)

    def get_layer_stats(self, active_layers: set[str] | None = None) -> dict:
        """获取各层字符统计（用于 Prompt 审计）"""
        if active_layers is None:
            active_layers = DEFAULT_LAYERS

        stats = {"system": len(self._load_layer("system"))}
        for name in ["editorial", "schema", "domain", "quality", "style"]:
            stats[name] = len(self._load_layer(name)) if name in active_layers else 0

        stats["total_static"] = sum(stats.values())
        return stats
