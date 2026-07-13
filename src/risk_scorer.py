"""
风险评分层 — 量化风险评分系统

评分模型（用户定制）：
  政府/制裁/军事信号  40%
  金融市场变化        25%
  能源/航运/供应链    20%
  媒体覆盖           10%
  智库分析            5%

事件类型权重（防止媒体噪音）：
  military=1.0, sanction=0.9, central_bank=1.0, shipping=0.8
  celebrity=0.0, sports=0.0

风险动量：追踪分数变化速度（delta/天）
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from src.utils import (
    get_data_dir,
    get_today_str,
    load_importance_config,
    load_phase_transitions_config,
    load_sources_config,
)

logger = logging.getLogger("global_news.risk_scorer")

SCHEMA_VERSION = "1.0"

# ─────────────────────────────────────────────────
# 从配置加载（带 fallback）
# ─────────────────────────────────────────────────


def _load_dimension_weights() -> dict:
    """加载评分维度权重"""
    try:
        config = load_importance_config()
        return config.get(
            "dimension_weights",
            {
                "government_sanction_military": 0.40,
                "financial_market": 0.25,
                "energy_shipping_supply": 0.20,
                "media_coverage": 0.10,
                "think_tank": 0.05,
            },
        )
    except Exception as e:
        logger.warning(f"加载评分维度权重失败，使用默认值: {e}")
        return {
            "government_sanction_military": 0.40,
            "financial_market": 0.25,
            "energy_shipping_supply": 0.20,
            "media_coverage": 0.10,
            "think_tank": 0.05,
        }


def _load_phase_transition_deltas() -> dict:
    """加载阶段转换风险增量"""
    try:
        config = load_phase_transitions_config()
        result = {}
        for _key, info in config.get("phase_transitions", {}).items():
            from_tuple = (info["from"], info["to"])
            result[from_tuple] = info["risk_delta"]
        return result
    except Exception as e:
        logger.warning(f"加载阶段转换风险增量失败，使用默认值: {e}")
        return {
            ("diplomatic", "sanction"): 10,
            ("diplomatic", "military"): 25,
            ("diplomatic", "economic"): 8,
            ("sanction", "military"): 25,
            ("sanction", "economic"): 10,
            ("economic", "military"): 25,
            ("economic", "financial"): 15,
            ("military", "financial"): 20,
            ("military", "de-escalation"): -30,
            ("financial", "de-escalation"): -20,
            ("de-escalation", "diplomatic"): -15,
        }


def _load_phase_keywords() -> dict:
    """加载阶段检测关键词"""
    try:
        config = load_phase_transitions_config()
        return config.get("phase_keywords", {})
    except Exception as e:
        logger.warning(f"加载阶段检测关键词失败，使用默认值: {e}")
        return {
            "sanction": ["制裁", "sanction", "禁运", "embargo"],
            "military": ["军演", "military", "部署", "deployment"],
            "financial": ["债券", "抛售", "收益率", "yield"],
            "economic": ["经济对抗", "贸易战", "trade war", "economic", "关税", "tariff"],
            "de-escalation": ["停火", "谈判", "ceasefire", "negotiation"],
        }


def _load_signal_thresholds() -> dict:
    """加载信号等级阈值"""
    try:
        config = load_importance_config()
        return config.get("signal_thresholds", {"S": 80, "A": 60, "B": 35})
    except Exception as e:
        logger.warning(f"加载信号等级阈值失败，使用默认值: {e}")
        return {"S": 80, "A": 60, "B": 35}


DIMENSION_WEIGHTS = _load_dimension_weights()
PHASE_TRANSITION_DELTA = _load_phase_transition_deltas()
SIGNAL_THRESHOLDS = _load_signal_thresholds()

# 理论最大 raw_score，由各维度上限和权重计算得出（见 score_theme 中各维度 cap 值）
_MAX_RAW_SCORE = (
    25 * DIMENSION_WEIGHTS.get("government_sanction_military", 0.40)
    + 25 * DIMENSION_WEIGHTS.get("financial_market", 0.25)
    + 25 * DIMENSION_WEIGHTS.get("energy_shipping_supply", 0.20)
    + 25 * DIMENSION_WEIGHTS.get("media_coverage", 0.10)
    + 5 * DIMENSION_WEIGHTS.get("think_tank", 0.05)
)


def _load_source_quality() -> dict[str, float]:
    """加载新闻源质量指标（credibility * signal_weight / 100），用于加权评分"""
    try:
        sources = load_sources_config()
        quality = {}
        for s in sources:
            name = s.get("name", "")
            cred = s.get("credibility", 5)
            sig = s.get("signal_weight", 5)
            quality[name] = (cred * sig) / 100.0  # 归一化到 0~1
        return quality
    except Exception as e:
        logger.warning(f"加载新闻源质量指标失败: {e}")
        return {}


def _find_event_first_seen(theme: str, news_items: list) -> str:
    """从历史事件图谱中查找事件首次出现日期，用于时间衰减计算"""
    try:
        from src.event_graph import load_recent_events

        recent = load_recent_events(days=30)
        theme_keywords = set()
        try:
            config = load_importance_config()
            theme_keywords = set(config.get("risk_themes", {}).get(theme, {}).get("keywords", []))
        except Exception:
            pass
        if not theme_keywords:
            return get_today_str()
        # 在历史事件中查找匹配的主题
        earliest = None
        for daily_graph in recent:
            for event in daily_graph.events:
                event_text = f"{event.title} {event.summary}".lower()
                if any(kw.lower() in event_text for kw in theme_keywords):
                    if earliest is None or daily_graph.date < earliest:
                        earliest = daily_graph.date
        return earliest or get_today_str()
    except Exception:
        return get_today_str()


def _calculate_signal_decay(first_seen_date: str, signal_level: str) -> float:
    """计算信号时间衰减乘数（48小时半衰期，按信号等级衰减）"""
    try:
        config = load_importance_config()
        decay_config = config.get("signal_decay", {})
        half_life_hours = decay_config.get("half_life_hours", 48)
        level_rates = decay_config.get("by_level", {"S": 0.95, "A": 0.90, "B": 0.80, "C": 0.60})
        rate = level_rates.get(signal_level, 0.80)

        first_seen = datetime.strptime(first_seen_date, "%Y-%m-%d")
        elapsed_hours = (datetime.now() - first_seen).total_seconds() / 3600
        if elapsed_hours <= 0:
            return 1.0
        decay = rate ** (elapsed_hours / half_life_hours)
        return max(0.1, round(decay, 3))
    except Exception:
        return 1.0


# ─────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────


@dataclass
class RiskScore:
    """单个风险主题的评分"""

    theme: str
    score: int  # 0-100
    components: dict = field(default_factory=dict)
    trend: str = "stable"  # rising/stable/cooling
    momentum: float = 0.0  # 风险动量（分数变化/天）
    acceleration: float = 0.0  # 风险加速度（动量变化率）
    signal_level: str = "C"  # S/A/B/C
    phase: str = "diplomatic"  # 当前阶段
    phase_transition: str = ""  # 最近的阶段跃迁
    last_updated: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyRiskReport:
    """每日风险报告"""

    date: str
    schema_version: str = SCHEMA_VERSION
    scores: list[RiskScore] = field(default_factory=list)
    global_stress_index: int = 0  # 全球压力指数 0-100

    def to_dict(self) -> dict:
        self.scores = [s.to_dict() if isinstance(s, RiskScore) else s for s in self.scores]
        d = asdict(self)
        return d


# ─────────────────────────────────────────────────
# 风险评分计算
# ─────────────────────────────────────────────────


class RiskScorer:
    """风险评分器"""

    def __init__(self):
        self.config = load_importance_config()
        self.risk_themes = self.config.get("risk_themes", {})
        self.risk_transmission = self.config.get("risk_transmission", {})

    def score_theme(
        self,
        theme: str,
        news_items: list,
        government_signals: list[str],
        financial_signals: list[str],
        energy_signals: list[str],
        think_tank_signals: list[str],
        source_lean: str = "中立",
        first_seen_date: str = "",
    ) -> RiskScore:
        """
        计算单个风险主题的评分

        评分公式：
        总分 = 政府/制裁/军事(40%) + 金融(25%) + 能源/航运(20%) + 媒体(10%) + 智库(5%)
        然后乘以事件类型权重
        """
        theme_keywords = self.risk_themes.get(theme, {}).get("keywords", [])
        if not theme_keywords:
            return RiskScore(theme=theme, score=0, last_updated=get_today_str())

        # 统计各维度命中
        def count_hits(text_list: list[str], keywords: list[str]) -> int:
            hits = 0
            for text in text_list:
                text_lower = text.lower()
                for kw in keywords:
                    if kw.lower() in text_lower:
                        hits += 1
                        break
            return hits

        # 媒体覆盖（按源去重 + 源质量加权）
        source_quality = _load_source_quality()
        seen_sources: set[str] = set()
        media_hits = 0
        weighted_hits = 0.0
        unique_sources_count = 0
        if news_items:
            for n in news_items:
                src = getattr(n, "source", "") or ""
                text = f"{n.title} {n.summary}".lower()
                if any(kw.lower() in text for kw in theme_keywords):
                    if src not in seen_sources:
                        seen_sources.add(src)
                        unique_sources_count += 1
                        quality = source_quality.get(src, 0.25)
                        weighted_hits += quality
                        media_hits += 1
        # 使用固定分母避免源数量稀释分数：更多源覆盖应增加而非降低分数
        # 每个源的质量贡献累加，除以基准数5（约5个源达到满分）
        media_score = min(25, int(25 * weighted_hits / 5)) if weighted_hits else 0

        # 政府/制裁/军事信号
        gov_hits = count_hits(government_signals, theme_keywords)
        gov_score = min(25, gov_hits * 5)

        # 金融信号
        fin_hits = count_hits(financial_signals, theme_keywords)
        fin_score = min(25, fin_hits * 5)

        # 能源/航运信号
        energy_hits = count_hits(energy_signals, theme_keywords)
        energy_score = min(25, energy_hits * 5)

        # 智库信号
        tt_hits = count_hits(think_tank_signals, theme_keywords)
        tt_score = min(5, tt_hits * 2)

        # 加权总分
        raw_score = (
            gov_score * DIMENSION_WEIGHTS["government_sanction_military"]
            + fin_score * DIMENSION_WEIGHTS["financial_market"]
            + energy_score * DIMENSION_WEIGHTS["energy_shipping_supply"]
            + media_score * DIMENSION_WEIGHTS["media_coverage"]
            + tt_score * DIMENSION_WEIGHTS["think_tank"]
        )

        # 归一化到 0-100（理论最大 raw_score 由各维度上限和权重动态计算）
        final_score = min(100, int(raw_score * 100 / _MAX_RAW_SCORE))

        # 确定信号等级（阈值从配置加载）
        if final_score >= SIGNAL_THRESHOLDS.get("S", 80):
            signal_level = "S"
        elif final_score >= SIGNAL_THRESHOLDS.get("A", 60):
            signal_level = "A"
        elif final_score >= SIGNAL_THRESHOLDS.get("B", 35):
            signal_level = "B"
        else:
            signal_level = "C"

        # 应用 escalation_rules（从 importance_keywords.yaml 的 categories 定义）
        all_signals_text = " ".join(
            government_signals + financial_signals + energy_signals + think_tank_signals
        ).lower()
        if all_signals_text:
            try:
                config = load_importance_config()
                for cat_info in config.get("categories", {}).values():
                    for rule in cat_info.get("escalation_rules", []):
                        triggers = rule.get("triggers", [])
                        if triggers and all(t.lower() in all_signals_text for t in triggers):
                            final_score = min(100, final_score + rule.get("bonus", 0))
                            target_level = rule.get("level", "")
                            level_order = {"C": 0, "B": 1, "A": 2, "S": 3}
                            if target_level and level_order.get(target_level, 0) > level_order.get(
                                signal_level, 0
                            ):
                                signal_level = target_level
            except Exception as e:
                logger.warning(f"应用 escalation_rules 失败: {e}")

        # escalation_rules 可能修改了 final_score，需要根据新分数重新评估 signal_level
        # （规则指定了 target_level 时已处理，这里处理 bonus 将分数推过更高等级阈值的情况）
        if final_score >= SIGNAL_THRESHOLDS.get("S", 80):
            signal_level = "S"
        elif final_score >= SIGNAL_THRESHOLDS.get("A", 60) and signal_level not in ("S",):
            signal_level = "A"
        elif final_score >= SIGNAL_THRESHOLDS.get("B", 35) and signal_level not in ("S", "A"):
            signal_level = "B"

        # 应用确认等级乘数（source_lean → narrative divergence → confirmation_multiplier）
        try:
            config = load_importance_config()
            confirmation_levels = config.get("confirmation_level", {})

            # source_lean 映射到确认等级（修复：不再全部映射到multi_source）
            lean_to_level = {
                "偏好西方叙事": "multi_source",
                "偏好中方叙事": "multi_source",
                "偏好俄方叙事": "multi_source",
                "区域本土视角": "single_source",
                "全球南方视角": "single_source",
                "多源平衡": "multi_source",
                "叙事撕裂": "divergent",  # 叙事撕裂不增加确认度
                # 兼容旧值
                "亲西方": "multi_source",
                "亲中方": "multi_source",
                "亲俄方": "multi_source",
                "混合": "divergent",
                "中立": "multi_source",
            }
            level_key = lean_to_level.get(source_lean, "single_source")

            # 叙事撕裂时降低确认度乘数（信号可能被阵营立场夸大）
            base_multiplier = confirmation_levels.get(level_key, {}).get("multiplier", 1.0)
            if level_key == "divergent":
                base_multiplier *= 0.8
            multiplier = base_multiplier
            final_score = min(100, int(final_score * multiplier))
            # 确认乘数可能改变分数，需重新评估信号等级
            if final_score >= SIGNAL_THRESHOLDS.get("S", 80):
                signal_level = "S"
            elif final_score >= SIGNAL_THRESHOLDS.get("A", 60) and signal_level not in ("S",):
                signal_level = "A"
            elif final_score >= SIGNAL_THRESHOLDS.get("B", 35) and signal_level not in ("S", "A"):
                signal_level = "B"
        except Exception as e:
            logger.warning(f"应用确认等级乘数失败: {e}")

        # 应用信号时间衰减
        if first_seen_date:
            decay = _calculate_signal_decay(first_seen_date, signal_level)
            final_score = max(0, int(final_score * decay))

        # 趋势默认为 stable，由 determine_trend() 在计算动量后更新
        return RiskScore(
            theme=theme,
            score=final_score,
            components={
                "government_sanction_military": gov_score,
                "financial_market": fin_score,
                "energy_shipping_supply": energy_score,
                "media_coverage": media_score,
                "think_tank": tt_score,
            },
            trend="stable",
            signal_level=signal_level,
            last_updated=get_today_str(),
        )

    @staticmethod
    def determine_trend(momentum: float) -> str:
        """根据动量确定趋势（基于动量方向而非仅绝对分数阈值）"""
        if momentum > 0:
            return "rising"
        elif momentum < 0:
            return "cooling"
        return "stable"

    def _get_yesterday_score(self, theme: str) -> int | None:
        """获取昨天的分数（不依赖当前分数，用于趋势判定）"""
        history = self._load_score_history(theme, 1)
        valid = [s for s in history if s is not None]
        return valid[-1] if valid else None

    def calculate_momentum(self, theme: str, current_score: int, days: int = 3) -> float:
        """
        计算风险动量（分数变化速度）

        动量 = (当前分数 - 最早有效分数) / 有效天数
        正值 = 风险加速，负值 = 风险减速

        仅使用实际存在的历史数据，缺失日期不参与计算。
        """
        history = self._load_score_history(theme, days)
        # 过滤缺失值
        valid = [(i, s) for i, s in enumerate(history) if s is not None]

        if len(valid) < 2:
            return 0.0

        oldest_idx, oldest_score = valid[0]
        span = valid[-1][0] - oldest_idx + 1  # 时间跨度（天数）
        if span == 0:
            return 0.0

        momentum = (current_score - oldest_score) / span
        return round(momentum, 1)

    def calculate_acceleration(self, theme: str, current_score: int) -> float:
        """
        计算风险加速度（动量的变化率）

        加速度 = 近期动量 - 前期动量
        正值 = 风险在加速升级，负值 = 升级放缓

        这比动量更能捕捉"突变"信号。
        """
        history = self._load_score_history(theme, 5)
        valid = [s for s in history if s is not None]

        if len(valid) < 3:
            return 0.0

        # 近期动量：最近1天（当前分数与昨天分数之差）
        recent_momentum = (current_score - valid[-1]) if len(valid) >= 2 else 0.0
        # 前期动量：之前3天
        if len(valid) >= 4:
            early_momentum = (valid[-2] - valid[0]) / (len(valid) - 2)
        else:
            early_momentum = 0.0

        acceleration = recent_momentum - early_momentum
        return round(acceleration, 1)

    def _load_score_history(self, theme: str, days: int) -> list:
        """
        加载最近 N 天的主题分数

        Returns:
            list[Optional[int]] - 缺失日期返回 None，存在但无该主题也返回 None
        """
        scores_dir = get_risk_dir()
        history = []
        today = datetime.now()

        for i in range(days, 0, -1):
            date = today - timedelta(days=i)
            file_path = scores_dir / f"scores_{date.strftime('%Y-%m-%d')}.json"
            if file_path.exists():
                try:
                    with open(file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    found = False
                    for s in data.get("scores", []):
                        if s.get("theme") == theme:
                            history.append(s.get("score", 0))
                            found = True
                            break
                    if not found:
                        history.append(None)
                except Exception as e:
                    logger.warning(f"加载历史风险报告失败 {file_path}: {e}")
                    history.append(None)
            else:
                history.append(None)

        return history

    def detect_phase_transition(
        self, theme: str, current_phase: str, new_signals: list[str]
    ) -> tuple[str, int, str]:
        """
        检测阶段跃迁

        Returns:
            (new_phase, risk_delta, transition_desc)
        """
        # 根据新信号判断是否应该跃迁（关键词从配置加载）
        signal_text = " ".join(new_signals).lower()
        phase_kw = _load_phase_keywords()

        new_phase = current_phase
        sanction_kw = phase_kw.get("sanction", ["制裁", "sanction", "禁运"])
        military_kw = phase_kw.get("military", ["军演", "military", "部署"])
        financial_kw = phase_kw.get("financial", ["债券", "抛售", "收益率"])
        economic_kw = phase_kw.get(
            "economic", ["经济对抗", "贸易战", "trade war", "economic", "关税", "tariff"]
        )
        deesc_kw = phase_kw.get("de-escalation", ["停火", "谈判", "ceasefire"])

        # 使用互斥 if/elif，按升级严重程度从高到低匹配
        if any(kw in signal_text for kw in military_kw):
            if current_phase in ("diplomatic", "sanction", "economic"):
                new_phase = "military"
        elif any(kw in signal_text for kw in sanction_kw):
            if current_phase in ("diplomatic", "economic"):
                new_phase = "sanction"
        elif any(kw in signal_text for kw in financial_kw):
            if current_phase in ("military", "sanction", "economic"):
                new_phase = "financial"
        elif any(kw in signal_text for kw in economic_kw):
            if current_phase in ("diplomatic", "sanction"):
                new_phase = "economic"
        elif any(kw in signal_text for kw in deesc_kw):
            new_phase = "de-escalation"

        # 计算跃迁风险增量
        transition_key = (current_phase, new_phase)
        risk_delta = PHASE_TRANSITION_DELTA.get(transition_key, 0)

        transition_desc = ""
        if risk_delta != 0:
            transition_desc = f"{current_phase} → {new_phase} (风险增量: {'+' if risk_delta > 0 else ''}{risk_delta})"

        return new_phase, risk_delta, transition_desc

    def validate_transmission_chains(self, market_data: dict) -> list[dict]:
        """
        验证传导链的量化锚点

        Args:
            market_data: 市场数据字典，格式为 {indicator: {"price": x, "change_pct": y}}

        Returns:
            list[dict]: 触发的传导链列表，每项包含 chain_name, triggered_nodes, total_bonux
        """
        triggered_chains = []

        # 检查 premium 状态，限制可用传导链
        try:
            from src.premium import get_transmission_chains

            allowed_chains = set(get_transmission_chains())
        except Exception:
            allowed_chains = set(self.risk_transmission.keys())

        for chain_name, chain_config in self.risk_transmission.items():
            if chain_name not in allowed_chains:
                continue
            anchors = chain_config.get("quantitative_anchors", [])
            if not anchors:
                continue

            triggered_nodes = []
            for anchor in anchors:
                indicator = anchor.get("indicator", "")
                threshold = anchor.get("threshold", 0)
                direction = anchor.get("direction", "above")

                # 特殊处理2s10s利差
                if indicator == "spread_2s10s":
                    if "us2y" in market_data and "us10y" in market_data:
                        spread = market_data["us10y"]["price"] - market_data["us2y"]["price"]
                        spread_triggered = False
                        if direction == "below" and spread < threshold:
                            spread_triggered = True
                        elif direction == "above" and spread > threshold:
                            spread_triggered = True
                        if spread_triggered:
                            triggered_nodes.append(
                                {
                                    "node": anchor["node"],
                                    "indicator": indicator,
                                    "value": round(spread, 2),
                                    "threshold": threshold,
                                    "signal": anchor.get("signal", ""),
                                }
                            )
                    continue

                # 常规指标验证
                if indicator in market_data:
                    value = market_data[indicator]["price"]
                    change = market_data[indicator].get("change_pct", 0)

                    triggered = False
                    if direction == "above" and value > threshold:
                        triggered = True
                    elif direction == "below":
                        # 当 threshold < 0 时，意图是检测百分比跌幅（如 -2 表示跌幅超2%）
                        # 应使用 change_pct 而非绝对价格值
                        if threshold < 0:
                            if change < threshold:
                                triggered = True
                        elif value < threshold:
                            triggered = True
                    elif direction == "change" and abs(change) > threshold:
                        triggered = True

                    if triggered:
                        triggered_nodes.append(
                            {
                                "node": anchor["node"],
                                "indicator": indicator,
                                "value": value,
                                "threshold": threshold,
                                "signal": anchor.get("signal", ""),
                            }
                        )

            if triggered_nodes:
                # 计算加成：每触发一个节点加5分
                bonus = len(triggered_nodes) * 5
                triggered_chains.append(
                    {"chain_name": chain_name, "triggered_nodes": triggered_nodes, "bonus": bonus}
                )

        return triggered_chains

    def calculate_global_stress_with_chains(
        self, scores: list[RiskScore], market_data: dict
    ) -> tuple[int, list[dict]]:
        """
        计算全球压力指数（含传导链验证）

        Returns:
            (global_stress_index, chain_validations)
        """
        # 基础全球压力指数
        if not scores:
            return 0, []

        base_score = sum(s.score for s in scores) / len(scores)

        # 验证传导链
        chain_validations = self.validate_transmission_chains(market_data)

        # 计算传导链加成
        chain_bonus = sum(c["bonus"] for c in chain_validations)

        # 多链共振加成：2条+10, 3条+20
        if len(chain_validations) >= 3:
            chain_bonus += 20
        elif len(chain_validations) >= 2:
            chain_bonus += 10

        # 最终分数
        final_score = min(100, int(base_score + chain_bonus))

        return final_score, chain_validations


# ─────────────────────────────────────────────────
# 存储
# ─────────────────────────────────────────────────


def get_risk_dir() -> Path:
    d = get_data_dir() / "risk"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_risk_report(report: DailyRiskReport) -> Path:
    """保存每日风险报告"""
    file_path = get_risk_dir() / f"scores_{report.date}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    logger.info(f"风险报告已保存: {file_path}")
    return file_path


def load_risk_report(date_str: str) -> DailyRiskReport | None:
    """加载指定日期的风险报告"""
    file_path = get_risk_dir() / f"scores_{date_str}.json"
    if not file_path.exists():
        return None
    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        scores = []
        for s in data.get("scores", []):
            try:
                # 修复 components 中的 None 值，防止下游计算 AttributeError
                if "components" in s and isinstance(s["components"], dict):
                    s["components"] = {
                        k: (v if v is not None else 0) for k, v in s["components"].items()
                    }
                scores.append(RiskScore(**s))
            except (TypeError, KeyError) as e:
                logger.warning(f"风险报告 {date_str} 跳过损坏记录: {e}")
        return DailyRiskReport(
            date=data["date"],
            schema_version=data.get("schema_version", "1.0"),
            scores=scores,
            global_stress_index=data.get("global_stress_index", 0),
        )
    except Exception as e:
        logger.warning(f"加载风险报告失败 {date_str}: {e}")
        return None
