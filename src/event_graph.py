"""
事件图谱层 — 结构化事件数据提取与存储

职责：
- 定义事件数据结构 (Event, DailyEventGraph)
- 从 AI 日报中提取结构化事件
- 持久化存储事件图谱
- 事件归一化（去重/合并）
- Actor Registry
"""

import json
import hashlib
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from src.utils import get_data_dir, get_today_str

logger = logging.getLogger("global_news.event_graph")

SCHEMA_VERSION = "1.0"

# ─────────────────────────────────────────────────
# Actor Registry（实体注册表）
# ─────────────────────────────────────────────────

ACTOR_REGISTRY: dict[str, dict] = {
    "美国": {"type": "country", "alliances": ["NATO", "五眼联盟"], "risk_domains": ["金融", "科技", "军事"]},
    "中国": {"type": "country", "alliances": ["金砖"], "risk_domains": ["科技", "贸易", "台海"]},
    "俄罗斯": {"type": "country", "alliances": ["CSTO"], "risk_domains": ["军事", "能源", "核"]},
    "伊朗": {"type": "country", "alliances": ["抵抗轴心"], "risk_domains": ["能源", "核", "中东"]},
    "以色列": {"type": "country", "alliances": ["美国盟友"], "risk_domains": ["中东", "军事"]},
    "日本": {"type": "country", "alliances": ["美日同盟"], "risk_domains": ["科技", "台海", "供应链"]},
    "韩国": {"type": "country", "alliances": ["美韩同盟"], "risk_domains": ["半导体", "朝鲜"]},
    "印度": {"type": "country", "alliances": ["金砖", "QUAD"], "risk_domains": ["能源", "贸易"]},
    "欧盟": {"type": "organization", "alliances": ["NATO"], "risk_domains": ["金融", "能源", "防务"]},
    "北约": {"type": "organization", "alliances": [], "risk_domains": ["军事", "防务"]},
    "OPEC": {"type": "organization", "alliances": [], "risk_domains": ["能源", "石油"]},
    "IMF": {"type": "organization", "alliances": [], "risk_domains": ["金融", "债务"]},
    "胡塞武装": {"type": "non-state", "alliances": ["伊朗"], "risk_domains": ["航运", "红海"]},
    "真主党": {"type": "non-state", "alliances": ["伊朗"], "risk_domains": ["中东", "军事"]},
    "台积电": {"type": "corporation", "alliances": [], "risk_domains": ["半导体", "科技战"]},
    "英伟达": {"type": "corporation", "alliances": [], "risk_domains": ["AI", "芯片"]},
}


# ─────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────

@dataclass
class Event:
    """单个国际事件"""
    event_id: str
    title: str
    signal_level: str           # S/A/B/C
    confidence: str             # 高/中/低
    actors: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    trend: str = "stable"       # up/down/stable
    summary: str = ""
    sources: list[str] = field(default_factory=list)
    related_events: list[str] = field(default_factory=list)
    phase: str = "diplomatic"   # diplomatic/economic/military/financial/de-escalation
    source_lean: str = "中立"   # 亲西方/亲中方/亲俄方/中立/混合
    lean_reasoning: str = ""    # 判断依据

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyEventGraph:
    """每日事件图谱"""
    date: str
    schema_version: str = SCHEMA_VERSION
    events: list[Event] = field(default_factory=list)
    top_risks: list[dict] = field(default_factory=list)
    watchpoints: list[dict] = field(default_factory=list)
    actor_mentions: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["events"] = [e.to_dict() if isinstance(e, Event) else e for e in self.events]
        return d


# ─────────────────────────────────────────────────
# 事件归一化（Event Canonicalization）
# ─────────────────────────────────────────────────

def _load_aliases_from_config() -> list[dict]:
    """
    从 config/event_aliases.yaml 加载别名配置

    Returns:
        按 priority 降序排列的别名列表，每项含:
        - canonical: 主题名
        - keywords: 关键词列表
        - exclude: 排除关键词列表
        - priority: 优先级
    """
    try:
        from src.utils import load_event_aliases_config
        config = load_event_aliases_config()
        raw = config.get("event_aliases", {})
    except Exception:
        # Fallback: 硬编码的基本别名
        raw = {
            "中东航运风险": {"keywords": ["霍尔木兹", "红海", "胡塞"], "priority": 10, "exclude": []},
            "台海局势": {"keywords": ["台湾", "台海"], "priority": 10, "exclude": []},
            "AI芯片出口管制": {"keywords": ["芯片禁令", "出口管制", "NVIDIA"], "priority": 9, "exclude": []},
            "俄乌冲突": {"keywords": ["乌克兰", "俄乌"], "priority": 8, "exclude": []},
            "中东冲突": {"keywords": ["加沙", "以色列", "伊朗"], "priority": 8, "exclude": []},
        }

    aliases = []
    for canonical, cfg in raw.items():
        if isinstance(cfg, dict):
            aliases.append({
                "canonical": canonical,
                "keywords": cfg.get("keywords", []),
                "exclude": cfg.get("exclude", []),
                "priority": cfg.get("priority", 5),
            })
        elif isinstance(cfg, list):
            # 兼容旧格式（纯列表）
            aliases.append({
                "canonical": canonical,
                "keywords": cfg,
                "exclude": [],
                "priority": 5,
            })

    # 按 priority 降序排列（高优先级先匹配）
    aliases.sort(key=lambda x: x["priority"], reverse=True)
    return aliases


# ─────────────────────────────────────────────────
# 实体+动作+主题 识别表（从配置文件加载）
# ─────────────────────────────────────────────────

def _load_entities_from_config() -> dict[str, str]:
    """从 config/entities.yaml 加载实体名称→代码映射"""
    try:
        from src.utils import load_entities_config
        config = load_entities_config()
        result = {}
        for code, info in config.get("entities", {}).items():
            for name in info.get("names", []):
                result[name] = code
        return result
    except Exception:
        # Fallback: 最小化硬编码
        return {
            "美国": "US", "US": "US", "中国": "CN", "China": "CN",
            "俄罗斯": "RU", "Russia": "RU", "伊朗": "IR", "Iran": "IR",
            "以色列": "IL", "日本": "JP", "韩国": "KR", "印度": "IN",
            "欧盟": "EU", "NATO": "NATO", "北约": "NATO",
            "NVIDIA": "NVDA", "TSMC": "TSMC", "华为": "Huawei",
        }


def _load_actions_from_config() -> dict[str, str]:
    """从 config/actions.yaml 加载动作名称→代码映射"""
    try:
        from src.utils import load_actions_config
        config = load_actions_config()
        result = {}
        for code, info in config.get("actions", {}).items():
            for name in info.get("names", []):
                result[name] = code
        return result
    except Exception:
        return {
            "制裁": "sanction", "sanction": "sanction",
            "军演": "drill", "封锁": "blockade",
            "停火": "ceasefire", "峰会": "summit",
        }


def _load_topics_from_config() -> dict[str, str]:
    """从 config/topics.yaml 加载主题名称→代码映射"""
    try:
        from src.utils import load_topics_config
        config = load_topics_config()
        result = {}
        for code, info in config.get("topics", {}).items():
            for name in info.get("names", []):
                result[name] = code
        return result
    except Exception:
        return {
            "台海": "taiwan-strait", "台湾": "taiwan-strait",
            "霍尔木兹": "hormuz", "红海": "red-sea",
            "AI芯片": "ai-chip", "半导体": "semiconductor",
        }


# 模块级缓存
_entities_cache = None
_actions_cache = None
_topics_cache = None


def _get_entities() -> dict[str, str]:
    global _entities_cache
    if _entities_cache is None:
        _entities_cache = _load_entities_from_config()
    return _entities_cache


def _get_actions() -> dict[str, str]:
    global _actions_cache
    if _actions_cache is None:
        _actions_cache = _load_actions_from_config()
    return _actions_cache


def _get_topics() -> dict[str, str]:
    global _topics_cache
    if _topics_cache is None:
        _topics_cache = _load_topics_from_config()
    return _topics_cache


def _extract_entities(title: str) -> list[str]:
    """从标题中提取实体（国家/组织/公司），使用词边界匹配避免误匹配"""
    entities = _get_entities()
    found = []
    for name, code in sorted(entities.items(), key=lambda x: len(x[0]), reverse=True):
        if code in found:
            continue

        # 检查是否包含中文字符
        has_cjk = any('一' <= c <= '鿿' for c in name)

        if has_cjk:
            # 中文实体：直接子串匹配
            if name in title:
                found.append(code)
        elif len(name) >= 3:
            # 英文长实体：直接子串匹配（误匹配率低）
            if name in title or name.lower() in title.lower():
                found.append(code)
        else:
            # 英文短实体（<3字符）：自定义词边界检测
            idx = title.lower().find(name.lower())
            while idx != -1:
                # 检查前面的字符
                before_ok = (idx == 0) or (not title[idx-1].isalpha())
                # 检查后面的字符
                after_pos = idx + len(name)
                after_ok = (after_pos >= len(title)) or (not title[after_pos].isalpha())
                if before_ok and after_ok:
                    found.append(code)
                    break
                idx = title.lower().find(name.lower(), idx + 1)

    return found[:3]


def _extract_actions(title: str) -> list[str]:
    """从标题中提取动作"""
    actions = _get_actions()
    found = []
    title_lower = title.lower()
    for name, code in sorted(actions.items(), key=lambda x: len(x[0]), reverse=True):
        if name.lower() in title_lower:
            if code not in found:
                found.append(code)
    return found[:2]


def _extract_topics(title: str, exclude_codes: list[str] = None,
                     entity_codes: list[str] = None,
                     action_names: list[str] = None) -> list[str]:
    """从标题中提取主题（排除已匹配的动作代码、实体名称子串、动作名称子串）"""
    topics = _get_topics()
    entities = _get_entities()
    found = []
    exclude = set(exclude_codes or [])
    action_name_set = set(action_names or [])

    entity_names = set()
    for name, code in entities.items():
        if code in (entity_codes or []):
            entity_names.add(name.lower())

    for name, code in sorted(topics.items(), key=lambda x: len(x[0]), reverse=True):
        if code in exclude:
            continue
        # 排除主题名称中包含已匹配实体名称的情况（如 topic="russia-ukraine", entity="Russia"）
        name_lower = name.lower()
        if any(en in name_lower or name_lower in en for en in entity_names):
            continue
        # 排除主题名称是已匹配动作名称子串的情况
        if any(name in an or an in name for an in action_name_set):
            continue
        if name in title or name_lower in title.lower():
            if code not in found:
                found.append(code)
    return found[:2]


def canonicalize_event_id(title: str) -> str:
    """
    将事件标题归一化为稳定的事件 ID。

    优先级:
    1. 实体+动作+主题 组合生成 ID（最稳定）
    2. 配置别名匹配（fallback）
    3. 标题哈希（最后手段）

    示例:
    - "美国制裁华为" → "US-sanction-Huawei"
    - "霍尔木兹海峡封锁" → "hormuz-blockade"
    - "NVIDIA芯片禁令扩大" → "NVDA-chip-ban"
    """
    # 1. 提取实体、动作、主题
    entities = _extract_entities(title)
    actions = _extract_actions(title)

    # 获取已匹配动作的原始名称（用于排除主题子串匹配）
    matched_action_names = []
    title_lower = title.lower()
    for name, code in _get_actions().items():
        if code in actions and name.lower() in title_lower:
            matched_action_names.append(name)

    topics = _extract_topics(title, exclude_codes=actions, entity_codes=entities,
                              action_names=matched_action_names)

    # 如果能组合出有意义的 ID，直接返回
    parts = []
    if entities:
        parts.extend(entities[:2])  # 最多2个实体
    if actions:
        parts.append(actions[0])    # 1个动作
    if topics:
        parts.append(topics[0])     # 1个主题

    if len(parts) >= 2:
        return "-".join(parts).lower()

    # 2. Fallback: 配置别名匹配
    aliases = _load_aliases_from_config()
    title_lower = title.lower()

    for entry in aliases:
        canonical = entry["canonical"]
        keywords = entry["keywords"]
        excludes = entry["exclude"]

        if any(ex.lower() in title_lower for ex in excludes):
            continue

        matched = False
        for kw in sorted(keywords, key=len, reverse=True):
            if kw.lower() in title_lower:
                matched = True
                break

        if matched:
            slug = re.sub(r'[^\w]+', '-', canonical).strip('-').lower()
            return slug

    # 3. 最后手段: 标题哈希
    h = hashlib.md5(title.encode()).hexdigest()[:8]
    slug = re.sub(r'[^\w一-鿿]+', '-', title).strip('-')[:30].lower()
    return f"{slug}-{h}"


def merge_events(events: list[Event]) -> list[Event]:
    """
    合并重复事件：相同 event_id 的事件合并。

    合并策略:
    - actors/sources: 并集
    - signal_level: 保留最高
    - summary: 保留最长
    - phase/trend/domains/related_events: 保留最新（后出现的覆盖先前的）
    """
    merged: dict[str, Event] = {}
    for event in events:
        eid = event.event_id
        if eid in merged:
            existing = merged[eid]
            # 合并 actors（并集）
            for actor in event.actors:
                if actor not in existing.actors:
                    existing.actors.append(actor)
            # 合并 sources（并集）
            for src in event.sources:
                if src not in existing.sources:
                    existing.sources.append(src)
            # 保留更高的信号等级
            level_order = {"S": 4, "A": 3, "B": 2, "C": 1}
            if level_order.get(event.signal_level, 0) > level_order.get(existing.signal_level, 0):
                existing.signal_level = event.signal_level
            # 保留更新的 summary
            if len(event.summary) > len(existing.summary):
                existing.summary = event.summary
            # 保留最新的 phase、trend、confidence（后来的覆盖先前的）
            existing.phase = event.phase
            existing.trend = event.trend
            existing.confidence = event.confidence
            # 合并 domains 和 related_events（并集）
            for d in event.domains:
                if d not in existing.domains:
                    existing.domains.append(d)
            for r in event.related_events:
                if r not in existing.related_events:
                    existing.related_events.append(r)
        else:
            merged[eid] = Event(
                event_id=event.event_id,
                title=event.title,
                signal_level=event.signal_level,
                confidence=event.confidence,
                actors=list(event.actors),
                domains=list(event.domains),
                trend=event.trend,
                summary=event.summary,
                sources=list(event.sources),
                related_events=list(event.related_events),
                phase=event.phase,
            )
    return list(merged.values())


# ─────────────────────────────────────────────────
# 存储
# ─────────────────────────────────────────────────

def get_events_dir() -> Path:
    d = get_data_dir() / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_event_graph(graph: DailyEventGraph) -> Path:
    """保存每日事件图谱"""
    file_path = get_events_dir() / f"events_{graph.date}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(graph.to_dict(), f, ensure_ascii=False, indent=2)
    logger.info(f"事件图谱已保存: {file_path} ({len(graph.events)} 个事件)")
    return file_path


def load_event_graph(date_str: str) -> Optional[DailyEventGraph]:
    """加载指定日期的事件图谱"""
    file_path = get_events_dir() / f"events_{date_str}.json"
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = [Event(**e) for e in data.get("events", [])]
        return DailyEventGraph(
            date=data["date"],
            schema_version=data.get("schema_version", "1.0"),
            events=events,
            top_risks=data.get("top_risks", []),
            watchpoints=data.get("watchpoints", []),
            actor_mentions=data.get("actor_mentions", {}),
        )
    except Exception as e:
        logger.warning(f"加载事件图谱失败 {date_str}: {e}")
        return None


def load_recent_events(days: int = 7) -> list[DailyEventGraph]:
    """加载最近 N 天的事件图谱"""
    from datetime import timedelta
    graphs = []
    today = datetime.now()
    for i in range(days):
        date = today - timedelta(days=i)
        graph = load_event_graph(date.strftime("%Y-%m-%d"))
        if graph:
            graphs.append(graph)
    return graphs
