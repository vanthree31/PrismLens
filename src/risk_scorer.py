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
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.utils import get_data_dir, get_today_str, load_importance_config, load_phase_transitions_config

logger = logging.getLogger("global_news.risk_scorer")

SCHEMA_VERSION = "1.0"

# ─────────────────────────────────────────────────
# 从配置加载（带 fallback）
# ─────────────────────────────────────────────────

def _load_dimension_weights() -> dict:
    """加载评分维度权重"""
    try:
        config = load_importance_config()
        return config.get("dimension_weights", {
            "government_sanction_military": 0.40,
            "financial_market": 0.25,
            "energy_shipping_supply": 0.20,
            "media_coverage": 0.10,
            "think_tank": 0.05,
        })
    except Exception:
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
        for key, info in config.get("phase_transitions", {}).items():
            from_tuple = (info["from"], info["to"])
            result[from_tuple] = info["risk_delta"]
        return result
    except Exception:
        return {
            ("diplomatic", "sanction"): 10,
            ("diplomatic", "military"): 25,
            ("sanction", "military"): 25,
            ("sanction", "economic"): 10,
            ("military", "financial"): 20,
            ("military", "de-escalation"): -30,
            ("economic", "military"): 25,
            ("financial", "military"): 20,
            ("de-escalation", "diplomatic"): -15,
        }


def _load_phase_keywords() -> dict:
    """加载阶段检测关键词"""
    try:
        config = load_phase_transitions_config()
        return config.get("phase_keywords", {})
    except Exception:
        return {
            "sanction": ["制裁", "sanction", "禁运", "embargo"],
            "military": ["军演", "military", "部署", "deployment"],
            "financial": ["债券", "抛售", "收益率", "yield"],
            "de-escalation": ["停火", "谈判", "ceasefire", "negotiation"],
        }


def _load_signal_thresholds() -> dict:
    """加载信号等级阈值"""
    try:
        config = load_importance_config()
        return config.get("signal_thresholds", {"S": 80, "A": 60, "B": 35})
    except Exception:
        return {"S": 80, "A": 60, "B": 35}


DIMENSION_WEIGHTS = _load_dimension_weights()
PHASE_TRANSITION_DELTA = _load_phase_transition_deltas()
SIGNAL_THRESHOLDS = _load_signal_thresholds()


# ─────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────

@dataclass
class RiskScore:
    """单个风险主题的评分"""
    theme: str
    score: int                          # 0-100
    components: dict = field(default_factory=dict)
    trend: str = "stable"               # rising/stable/cooling
    momentum: float = 0.0               # 风险动量（分数变化/天）
    acceleration: float = 0.0           # 风险加速度（动量变化率）
    signal_level: str = "C"             # S/A/B/C
    phase: str = "diplomatic"           # 当前阶段
    phase_transition: str = ""          # 最近的阶段跃迁
    last_updated: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailyRiskReport:
    """每日风险报告"""
    date: str
    schema_version: str = SCHEMA_VERSION
    scores: list[RiskScore] = field(default_factory=list)
    global_stress_index: int = 0        # 全球压力指数 0-100

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scores"] = [s.to_dict() if isinstance(s, RiskScore) else s for s in self.scores]
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

        # 媒体覆盖（所有新闻）
        news_texts = [f"{n.title} {n.summary}" for n in news_items] if news_items else []
        media_hits = count_hits(news_texts, theme_keywords)
        media_count = len(news_texts) if news_texts else 1
        media_score = min(25, int(25 * media_hits / max(1, media_count) * 5))

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
            gov_score * DIMENSION_WEIGHTS["government_sanction_military"] +
            fin_score * DIMENSION_WEIGHTS["financial_market"] +
            energy_score * DIMENSION_WEIGHTS["energy_shipping_supply"] +
            media_score * DIMENSION_WEIGHTS["media_coverage"] +
            tt_score * DIMENSION_WEIGHTS["think_tank"]
        )

        # 归一化到 0-100
        final_score = min(100, int(raw_score * 100 / 25))

        # 确定信号等级（阈值从配置加载）
        if final_score >= SIGNAL_THRESHOLDS.get("S", 80):
            signal_level = "S"
        elif final_score >= SIGNAL_THRESHOLDS.get("A", 60):
            signal_level = "A"
        elif final_score >= SIGNAL_THRESHOLDS.get("B", 35):
            signal_level = "B"
        else:
            signal_level = "C"

        # 确定趋势
        trend = "stable"
        if final_score >= 60:
            trend = "rising"
        elif final_score < 25:
            trend = "cooling"

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
            trend=trend,
            signal_level=signal_level,
            last_updated=get_today_str(),
        )

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

        # 近期动量：最近2天
        recent_momentum = (current_score - valid[-1]) / 2 if len(valid) >= 2 else 0.0
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
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    found = False
                    for s in data.get("scores", []):
                        if s.get("theme") == theme:
                            history.append(s.get("score", 0))
                            found = True
                            break
                    if not found:
                        history.append(None)
                except Exception:
                    history.append(None)
            else:
                history.append(None)

        return history

    def detect_phase_transition(self, theme: str, current_phase: str, new_signals: list[str]) -> tuple[str, int, str]:
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
        deesc_kw = phase_kw.get("de-escalation", ["停火", "谈判", "ceasefire"])

        if any(kw in signal_text for kw in sanction_kw):
            if current_phase == "diplomatic":
                new_phase = "sanction"
        if any(kw in signal_text for kw in military_kw):
            if current_phase in ("diplomatic", "sanction"):
                new_phase = "military"
        if any(kw in signal_text for kw in financial_kw):
            if current_phase in ("military", "sanction"):
                new_phase = "financial"
        if any(kw in signal_text for kw in deesc_kw):
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

        for chain_name, chain_config in self.risk_transmission.items():
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
                        if direction == "below" and spread < threshold:
                            triggered_nodes.append({
                                "node": anchor["node"],
                                "indicator": indicator,
                                "value": round(spread, 2),
                                "threshold": threshold,
                                "signal": anchor.get("signal", "")
                            })
                    continue

                # 常规指标验证
                if indicator in market_data:
                    value = market_data[indicator]["price"]
                    change = market_data[indicator].get("change_pct", 0)

                    triggered = False
                    if direction == "above" and value > threshold:
                        triggered = True
                    elif direction == "below" and value < threshold:
                        triggered = True
                    elif direction == "change" and abs(change) > threshold:
                        triggered = True

                    if triggered:
                        triggered_nodes.append({
                            "node": anchor["node"],
                            "indicator": indicator,
                            "value": value,
                            "threshold": threshold,
                            "signal": anchor.get("signal", "")
                        })

            if triggered_nodes:
                # 计算加成：每触发一个节点加5分
                bonus = len(triggered_nodes) * 5
                triggered_chains.append({
                    "chain_name": chain_name,
                    "triggered_nodes": triggered_nodes,
                    "bonus": bonus
                })

        return triggered_chains

    def calculate_global_stress_with_chains(
        self,
        scores: list[RiskScore],
        market_data: dict
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


def load_risk_report(date_str: str) -> Optional[DailyRiskReport]:
    """加载指定日期的风险报告"""
    file_path = get_risk_dir() / f"scores_{date_str}.json"
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        scores = [RiskScore(**s) for s in data.get("scores", [])]
        return DailyRiskReport(
            date=data["date"],
            schema_version=data.get("schema_version", "1.0"),
            scores=scores,
            global_stress_index=data.get("global_stress_index", 0),
        )
    except Exception as e:
        logger.warning(f"加载风险报告失败 {date_str}: {e}")
        return None
