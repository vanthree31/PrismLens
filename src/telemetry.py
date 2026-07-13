"""
Telemetry v1 — 结构化运行遥测

每次日报生成自动记录结构化指标，支持长期趋势分析。
数据存储: data/telemetry/{date}_{pipeline}.json

设计原则:
- 零依赖：纯标准库，不引入外部时序数据库
- 即插即用：导入即开始记录，不改变现有调用链
- 可查询：提供累计统计和趋势计算
"""

import json
import logging
import os
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("global_news.telemetry")


# ═══════════════════════════════════════════════════
# Data Model
# ═══════════════════════════════════════════════════

@dataclass
class PipelineTelemetry:
    """单次 Pipeline 运行的完整遥测数据"""

    # ── 元数据 ──
    run_id: str = ""
    date: str = ""
    pipeline_version: str = "v3"
    started_at: str = ""
    finished_at: str = ""

    # ── 延迟 ──
    fetch_seconds: float = 0.0
    ai_seconds: float = 0.0
    html_seconds: float = 0.0
    extraction_seconds: float = 0.0
    total_seconds: float = 0.0

    # ── Token ──
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    api_calls: int = 0
    estimated_cost_usd: float = 0.0

    # ── 数据 ──
    news_count: int = 0
    source_count: int = 0
    source_success_rate: float = 0.0
    failed_sources: list[str] = field(default_factory=list)

    # ── 输出质量 ──
    output_chars: int = 0
    h2_chapters: int = 0
    events_count: int = 0
    regions_covered: int = 0
    region_names: list[str] = field(default_factory=list)
    s_level_count: int = 0
    a_level_count: int = 0
    placeholder_count: int = 0
    vague_count: int = 0
    json_parse_success: bool = False
    citation_count: int = 0
    citation_density: float = 0.0

    # ── 市场数据 ──
    market_indicators: int = 0
    market_quality: str = ""

    # ── 质量自评 ──
    quality_self_assessment: str = ""
    hallucination_risk: str = ""

    # ── 异常标记 ──
    had_fallback: bool = False
    had_truncation: bool = False
    had_parse_error: bool = False
    error_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def quality_score(self) -> int:
        """综合质量评分 (0-100)"""
        score = 50  # baseline

        # 正面指标
        if self.regions_covered >= 10:
            score += 10
        elif self.regions_covered >= 8:
            score += 5
        if self.h2_chapters >= 10:
            score += 5
        if self.placeholder_count == 0:
            score += 10
        if self.vague_count == 0:
            score += 5
        if self.json_parse_success:
            score += 10
        if self.citation_density > 0.8:
            score += 10
        elif self.citation_density > 0.5:
            score += 5
        if self.s_level_count <= 1:
            score += 5
        if self.a_level_count <= 5:
            score += 5

        # 负面指标
        if self.had_fallback:
            score -= 15
        if self.had_truncation:
            score -= 10
        if self.had_parse_error:
            score -= 10
        if self.source_success_rate < 0.7:
            score -= 10

        return max(0, min(100, score))


# ═══════════════════════════════════════════════════
# Telemetry Engine
# ═══════════════════════════════════════════════════

class Telemetry:
    """运行遥测记录器"""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            from src.utils import get_data_dir
            self.base_dir = get_data_dir() / "telemetry"
        else:
            self.base_dir = Path(data_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, t: PipelineTelemetry) -> Path:
        """保存单次遥测"""
        t.finished_at = datetime.now().isoformat()

        filename = f"{t.date}_{t.pipeline_version}_{t.run_id[:8]}.json"
        path = self.base_dir / filename

        path.write_text(
            json.dumps(t.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def load_recent(self, days: int = 30, pipeline: str | None = None) -> list[PipelineTelemetry]:
        """加载最近 N 天的遥测数据"""
        results = []
        cutoff = datetime.now().timestamp() - days * 86400

        for f in sorted(self.base_dir.glob("*.json"), reverse=True):
            if f.stat().st_mtime < cutoff:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if pipeline and data.get("pipeline_version") != pipeline:
                    continue
                t = PipelineTelemetry(**{k: v for k, v in data.items() if k in PipelineTelemetry.__dataclass_fields__})
                results.append(t)
            except Exception:
                continue

        return results

    def get_trend(self, metric: str, days: int = 30, pipeline: str | None = None) -> dict:
        """获取某个指标的 N 天趋势

        Returns:
            {"values": [v1, v2, ...], "mean": m, "trend": "improving|stable|degrading", "pct_change": p}
        """
        records = self.load_recent(days=days, pipeline=pipeline)
        if len(records) < 3:
            return {"values": [], "mean": 0, "trend": "insufficient_data", "pct_change": 0}

        values = [getattr(r, metric, 0) for r in records if hasattr(r, metric)]
        if len(values) < 3:
            return {"values": values, "mean": 0, "trend": "insufficient_data", "pct_change": 0}

        mean = statistics.mean(values)
        half = len(values) // 2
        first_half_avg = statistics.mean(values[:half]) if values[:half] else 0
        second_half_avg = statistics.mean(values[half:]) if values[half:] else 0

        if second_half_avg == 0 and first_half_avg == 0:
            trend = "stable"
        elif second_half_avg == 0:
            trend = "degrading"
        else:
            pct = (second_half_avg / max(first_half_avg, 0.01) - 1) * 100
            if pct > 5:
                trend = "improving"
            elif pct < -5:
                trend = "degrading"
            else:
                trend = "stable"

        return {
            "values": values,
            "mean": round(mean, 2),
            "trend": trend,
            "pct_change": round(
                (second_half_avg / max(first_half_avg, 0.01) - 1) * 100, 1
            ),
            "n": len(values),
        }

    def compare_pipelines(self, date: str) -> dict | None:
        """对比同一天 v2 和 v3 的运行结果"""
        v3_files = list(self.base_dir.glob(f"{date}_v3_*.json"))
        v2_files = list(self.base_dir.glob(f"{date}_v2_*.json"))

        if not v3_files or not v2_files:
            return None

        v3_data = json.loads(v3_files[0].read_text(encoding="utf-8"))
        v2_data = json.loads(v2_files[0].read_text(encoding="utf-8"))

        v3_score = PipelineTelemetry(**{k: v for k, v in v3_data.items()
                                        if k in PipelineTelemetry.__dataclass_fields__}).quality_score()
        v2_score = PipelineTelemetry(**{k: v for k, v in v2_data.items()
                                        if k in PipelineTelemetry.__dataclass_fields__}).quality_score()

        return {
            "date": date,
            "v3": {
                "total_seconds": v3_data.get("total_seconds", 0),
                "total_tokens": v3_data.get("total_tokens", 0),
                "api_calls": v3_data.get("api_calls", 0),
                "events_count": v3_data.get("events_count", 0),
                "regions_covered": v3_data.get("regions_covered", 0),
                "quality_score": v3_score,
                "json_parse_success": v3_data.get("json_parse_success", False),
            },
            "v2": {
                "total_seconds": v2_data.get("total_seconds", 0),
                "total_tokens": v2_data.get("total_tokens", 0),
                "api_calls": v2_data.get("api_calls", 0),
                "events_count": v2_data.get("events_count", 0),
                "regions_covered": v2_data.get("regions_covered", 0),
                "quality_score": v2_score,
                "json_parse_success": v2_data.get("json_parse_success", False),
            },
            "regression": v3_score < v2_score - 5,
            "recommendation": "switch_to_v3" if v3_score >= v2_score else "keep_v2",
        }


# ═══════════════════════════════════════════════════
# Cost Estimation
# ═══════════════════════════════════════════════════

# DeepSeek API pricing (USD per 1M tokens, estimated)
# Adjust based on actual pricing
COST_PER_1M_INPUT = float(os.getenv("TELEMETRY_COST_INPUT", "0.27"))   # $0.27/1M input
COST_PER_1M_OUTPUT = float(os.getenv("TELEMETRY_COST_OUTPUT", "1.10"))  # $1.10/1M output


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """估算 API 调用成本 (USD)"""
    input_cost = (input_tokens / 1_000_000) * COST_PER_1M_INPUT
    output_cost = (output_tokens / 1_000_000) * COST_PER_1M_OUTPUT
    return round(input_cost + output_cost, 6)


def get_cost_trend(days: int = 30, pipeline: str | None = None) -> dict:
    """获取成本趋势"""
    t = Telemetry()
    records = t.load_recent(days=days, pipeline=pipeline)
    if not records:
        return {"daily_avg_cost": 0, "monthly_est": 0, "n": 0}

    costs = [(r.estimated_cost_usd or estimate_cost(r.input_tokens, r.output_tokens))
             for r in records]
    daily_avg = statistics.mean(costs) if costs else 0
    return {
        "daily_avg_cost": round(daily_avg, 4),
        "monthly_est": round(daily_avg * 30, 2),
        "n": len(costs),
    }
