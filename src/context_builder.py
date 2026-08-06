"""
Context Builder v1 — 模块化上下文组装

将新闻、历史、市场、事件、风险等数据源统一组装为 LLM 输入上下文。
这是后续 Prompt 优化之外的第二大优化方向。

设计原则:
- 数据源独立：每个数据源是一个可插拔的 Provider
- Token 预算：按优先级分配 token 配额
- 可配置：通过 sections 控制哪些数据源参与组装
- 可审计：输出每个 section 的 token 估算

用法:
    builder = ContextBuilder(lang="zh")
    builder.add_news(news_items, max_items=500)
    builder.add_market_data()
    builder.add_history(days=90)
    builder.add_evolution()
    builder.add_source_health(news_items)
    context = builder.build()
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.fetcher import NewsItem
from src.utils import get_history_dir

logger = logging.getLogger("global_news.context_builder")


@dataclass
class ContextSection:
    """上下文的一个数据段"""

    name: str
    content: str
    priority: int = 5  # 1-10, 越高越优先保留
    estimated_tokens: int = 0

    def __repr__(self) -> str:
        return f"Section({self.name}, {len(self.content):,}chars, ~{self.estimated_tokens}tokens)"


class ContextBuilder:
    """模块化上下文组装器

    按优先级 -> Token 预算 -> 截断策略 组装最终上下文。
    """

    # Token 预算 (可根据模型调整)
    MAX_TOKENS = 200_000  # 安全上限 (1M context 的 20%)

    def __init__(self, lang: str = "zh", max_tokens: int | None = None):
        self.lang = lang
        self.max_tokens = max_tokens or self.MAX_TOKENS
        self.sections: list[ContextSection] = []

    # ═══════════════════════════════════════════════
    # Data Providers
    # ═══════════════════════════════════════════════

    def add_news(self, news_items: list[NewsItem], max_items: int = 500) -> "ContextBuilder":
        """添加新闻数据（最高优先级）"""
        if not news_items:
            return self

        lines = [f"### 新闻数据（共 {len(news_items)} 条）\n"]
        by_region: dict[str, list[NewsItem]] = {}
        for n in news_items:
            by_region.setdefault(n.region, []).append(n)

        counter = 0
        queues = {r: list(items) for r, items in by_region.items()}
        while queues and counter < max_items:
            for region in sorted(queues.keys()):
                items = queues[region]
                if not items:
                    continue
                item = items.pop(0)
                counter += 1
                score = getattr(item, "importance_score", 0)
                score_str = f" [imp:{score:.0f}]" if score else ""
                lines.append(f"{counter}. [{item.source}] ({region}){score_str} {item.title}")
                if item.summary:
                    lines.append(f"   {item.summary[:200]}")
            queues = {r: q for r, q in queues.items() if q}

        content = "\n".join(lines)
        est_tokens = len(content) // 2  # 中文约 2 chars/token
        self.sections.append(
            ContextSection("news", content, priority=10, estimated_tokens=est_tokens)
        )
        return self

    def add_market_data(self) -> "ContextBuilder":
        """添加实时市场数据"""
        try:
            from src.market_data import get_market_provider

            market_text = get_market_provider().get_data_for_prompt()
        except Exception as e:
            logger.debug(f"市场数据加载失败: {e}")
            market_text = ""

        if market_text and "暂未接入" not in market_text:
            content = f"### 实时市场数据\n{market_text}\n"
            est_tokens = len(content) // 3
            self.sections.append(
                ContextSection("market", content, priority=9, estimated_tokens=est_tokens)
            )
        return self

    def add_history(self, days: int = 90) -> "ContextBuilder":
        """添加历史趋势仪表盘"""
        try:
            from src.history_analyzer import HistoryAnalyzer

            history_text = HistoryAnalyzer(max_days=days).render_for_prompt(lang=self.lang)
        except Exception as e:
            logger.debug(f"历史分析加载失败: {e}")
            history_text = ""

        if history_text and "暂无" not in history_text:
            content = f"### 历史趋势仪表盘\n{history_text}\n"
            est_tokens = len(content) // 3
            self.sections.append(
                ContextSection("history", content, priority=7, estimated_tokens=est_tokens)
            )
        return self

    def add_evolution(self, max_events: int = 10) -> "ContextBuilder":
        """添加活跃事件演化追踪（Phase 2: SQLite 优先，JSON 回退）"""
        content = ""
        try:
            # Phase 2: 从 SQLite Event DB 读取活跃事件
            from src.event_database import EventReader

            reader = EventReader()
            active = reader.get_active_events(min_risk=0)
            if active:
                lines = ["**活跃演化事件** (Event DB):"]
                for evt in active[:max_events]:
                    title = evt.get("display_title", "")
                    phase = evt.get("current_phase", "")
                    risk = evt.get("current_risk_score", "")
                    signal = evt.get("current_signal_level", "")
                    region = evt.get("region", "")
                    lines.append(f"- [{signal}] {title} (阶段:{phase}, 风险:{risk}, 区域:{region})")
                content = "\n".join(lines)
        except Exception as e:
            logger.debug(f"Event DB 演化数据读取失败，回退 JSON: {e}")

        # JSON fallback
        if not content:
            try:
                from src.evolution_tracker import EvolutionTracker

                tracker = EvolutionTracker()
                master = tracker._load_master() if hasattr(tracker, "_load_master") else {}
                if master:
                    active = [
                        v
                        for v in master.values()
                        if isinstance(v, dict) and v.get("current_risk", 0) > 0
                    ]
                    if active:
                        lines = ["**活跃演化事件** (JSON):"]
                        for evt in sorted(
                            active, key=lambda x: x.get("current_risk", 0), reverse=True
                        )[:max_events]:
                            title = evt.get("title", evt.get("event_id", ""))
                            phase = evt.get("current_phase", "")
                            risk = evt.get("current_risk", "")
                            lines.append(f"- {title} (阶段:{phase}, 风险:{risk})")
                        content = "\n".join(lines)
            except Exception as e:
                logger.debug(f"演化追踪 JSON fallback 也失败: {e}")

        if content:
            est_tokens = len(content) // 3
            self.sections.append(
                ContextSection("evolution", content, priority=6, estimated_tokens=est_tokens)
            )
        return self

    def add_source_health(self, news_items: list[NewsItem]) -> "ContextBuilder":
        """添加新闻源健康状态"""
        try:
            source_count = len({n.source for n in news_items})
            region_counts = Counter()
            for n in news_items:
                region_counts[getattr(n, "region", "未知")] += 1
            content = (
                f"新闻源: {source_count}个, 新闻: {len(news_items)}条\n"
                f"区域分布: " + ", ".join(f"{r}:{c}" for r, c in region_counts.most_common())
            )
            est_tokens = len(content) // 3
            self.sections.append(
                ContextSection("source_health", content, priority=8, estimated_tokens=est_tokens)
            )
        except Exception:
            pass
        return self

    def add_yesterday_events(self) -> "ContextBuilder":
        """添加昨日事件参考（Phase 2: SQLite 优先，JSON 回退）"""
        content = ""
        try:
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            # Phase 2: 从 SQLite Event DB 读取昨日事件
            from src.event_database import EventReader

            reader = EventReader()
            events = reader.get_events_by_date(yesterday, limit=30)
            if events:
                titles = [e.get("display_title", "") for e in events[:20] if e.get("display_title")]
                if titles:
                    content = (
                        "昨日已提取事件（如当前事件在此列表中大量出现，"
                        "则该聚类不是新兴信号 | Event DB）:\n" + "\n".join(f"- {t}" for t in titles)
                    )
        except Exception as e:
            logger.debug(f"Event DB 昨日事件读取失败，回退 JSON: {e}")

        # JSON fallback
        if not content:
            try:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                events_file = get_history_dir().parent / "events" / f"events_{yesterday}.json"
                if events_file.exists():
                    data = json.loads(events_file.read_text(encoding="utf-8"))
                    events = data.get("events", [])
                    if events:
                        titles = [e.get("title", "") for e in events[:20] if e.get("title")]
                        content = (
                            "昨日已提取事件（如当前事件在此列表中大量出现，"
                            "则该聚类不是新兴信号 | JSON）:\n" + "\n".join(f"- {t}" for t in titles)
                        )
            except Exception:
                pass

        if content:
            est_tokens = len(content) // 3
            self.sections.append(
                ContextSection("yesterday_events", content, priority=5, estimated_tokens=est_tokens)
            )
        return self

    def add_event_timeline(self, days: int = 7, top_n: int = 5) -> "ContextBuilder":
        """添加活跃事件的近期演化时间线（Phase 2: SQLite 专有功能）

        从 event_revisions 表读取最近 N 天的阶段变化和风险趋势，
        帮助 LLM 理解事件是升级中、降温中还是稳定。

        Args:
            days: 回溯天数
            top_n: 取前 N 个最活跃事件
        """
        content = ""
        try:
            from src.event_database import EventReader

            reader = EventReader()
            active = reader.get_active_events(min_risk=20)

            if active:
                lines = [f"**事件演化时间线（最近{days}天）**:"]
                for evt in active[:top_n]:
                    eid = evt["id"]
                    title = evt.get("display_title", eid)
                    signal = evt.get("current_signal_level", "C")
                    timeline = reader.get_timeline(eid, days=days)

                    if not timeline:
                        continue

                    # 提取阶段变化和风险走势
                    phases = [r.get("phase", "") for r in timeline]
                    risks = [r.get("risk_score", 0) for r in timeline]
                    transitions = [
                        r.get("phase_transition") for r in timeline if r.get("phase_transition")
                    ]

                    # 去重连续相同阶段
                    unique_phases = []
                    for p in phases:
                        if not unique_phases or unique_phases[-1] != p:
                            unique_phases.append(p)

                    phase_path = " → ".join(unique_phases[:5])
                    risk_trend = (
                        "↑"
                        if len(risks) >= 2 and risks[0] > risks[-1]
                        else "↓"
                        if len(risks) >= 2 and risks[0] < risks[-1]
                        else "→"
                    )

                    lines.append(
                        f"- [{signal}] {title}\n"
                        f"  阶段: {phase_path}\n"
                        f"  风险: {risks[0] if risks else '?'} {risk_trend} "
                        f"(变动: {len(transitions)}次)"
                    )

                content = "\n".join(lines)
        except Exception as e:
            logger.debug(f"Event DB 时间线读取失败: {e}")

        if content:
            est_tokens = len(content) // 3
            self.sections.append(
                ContextSection("event_timeline", content, priority=7, estimated_tokens=est_tokens)
            )
        return self

    def add_section(self, name: str, content: str, priority: int = 5) -> "ContextBuilder":
        """手动添加自定义数据段"""
        est_tokens = len(content) // 2
        self.sections.append(
            ContextSection(name, content, priority=priority, estimated_tokens=est_tokens)
        )
        return self

    # ═══════════════════════════════════════════════
    # Build
    # ═══════════════════════════════════════════════

    def build(self, max_tokens: int | None = None) -> str:
        """组装最终上下文，按优先级 + Token 预算截断

        Args:
            max_tokens: 覆盖实例的 max_tokens

        Returns:
            组装后的上下文文本
        """
        budget = max_tokens or self.max_tokens

        # 按优先级降序排列
        sorted_sections = sorted(self.sections, key=lambda s: s.priority, reverse=True)

        parts = []
        used_tokens = 0

        for section in sorted_sections:
            if used_tokens + section.estimated_tokens > budget:
                # 高优先级允许超预算（如 news），低优先级截断
                if section.priority >= 8:
                    parts.append(section.content)
                    used_tokens += section.estimated_tokens
                    logger.warning(
                        f"ContextBuilder: 超 token 预算但保留高优 section '{section.name}' "
                        f"(used={used_tokens}/{budget})"
                    )
                else:
                    logger.info(
                        f"ContextBuilder: 跳过 '{section.name}' "
                        f"(token预算耗尽: {used_tokens}/{budget})"
                    )
            else:
                parts.append(section.content)
                used_tokens += section.estimated_tokens

        result = "\n\n".join(parts)

        logger.info(
            f"ContextBuilder: {len(self.sections)} sections → "
            f"{len(result):,}chars (~{used_tokens:,}tokens/{budget:,}budget)"
        )

        return result

    def stats(self) -> dict:
        """返回上下文组装统计"""
        return {
            "total_sections": len(self.sections),
            "total_chars": sum(len(s.content) for s in self.sections),
            "total_estimated_tokens": sum(s.estimated_tokens for s in self.sections),
            "sections": [
                {
                    "name": s.name,
                    "chars": len(s.content),
                    "estimated_tokens": s.estimated_tokens,
                    "priority": s.priority,
                }
                for s in sorted(self.sections, key=lambda x: x.priority, reverse=True)
            ],
        }
