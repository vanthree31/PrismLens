"""
Production Metrics — 运营指标自动记录

每次日报生成自动写入 data/runs.db (SQLite)。
14 天连续达标 → Pipeline Freeze。

指标:
- 生成成功率 (API调用成功 + HTML生成成功)
- JSON 解析成功率
- 平均延迟
- Token 消耗 + 成本估算
- 源可用率
- 邮件推送成功率
- Finish Reason (stop/length/error)
- Quality Score
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("global_news.metrics")


DB_PATH: Path | None = None


def _get_db() -> Path:
    global DB_PATH
    if DB_PATH is None:
        from src.utils import get_data_dir
        DB_PATH = get_data_dir() / "runs.db"
    return DB_PATH


def init_db() -> None:
    """初始化 runs 数据库"""
    db = _get_db()
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT NOT NULL,
            pipeline        TEXT NOT NULL DEFAULT 'v3',
            date            TEXT NOT NULL,
            started_at      TEXT NOT NULL,
            finished_at     TEXT NOT NULL,

            -- 延迟
            fetch_seconds   REAL NOT NULL DEFAULT 0,
            ai_seconds      REAL NOT NULL DEFAULT 0,
            total_seconds   REAL NOT NULL DEFAULT 0,

            -- Token + 成本
            input_tokens    INTEGER NOT NULL DEFAULT 0,
            output_tokens   INTEGER NOT NULL DEFAULT 0,
            estimated_cost  REAL NOT NULL DEFAULT 0,

            -- 数据
            news_count      INTEGER NOT NULL DEFAULT 0,
            source_count    INTEGER NOT NULL DEFAULT 0,
            source_success_rate REAL NOT NULL DEFAULT 0,

            -- 输出质量
            output_chars    INTEGER NOT NULL DEFAULT 0,
            h2_chapters     INTEGER NOT NULL DEFAULT 0,
            events_count    INTEGER NOT NULL DEFAULT 0,
            regions_covered INTEGER NOT NULL DEFAULT 0,
            json_valid      INTEGER NOT NULL DEFAULT 0,
            html_generated  INTEGER NOT NULL DEFAULT 0,
            finish_reason   TEXT NOT NULL DEFAULT '',
            quality_score   INTEGER NOT NULL DEFAULT 0,
            placeholder_count INTEGER NOT NULL DEFAULT 0,

            -- 推送
            email_sent      INTEGER NOT NULL DEFAULT 0,
            email_success   INTEGER NOT NULL DEFAULT 0,

            -- 标记
            had_fallback    INTEGER NOT NULL DEFAULT 0,
            had_error       INTEGER NOT NULL DEFAULT 0,
            error_message   TEXT NOT NULL DEFAULT '',

            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_runs_pipeline ON runs(pipeline)
    """)
    conn.commit()
    conn.close()


@dataclass
class RunRecord:
    """单次运行记录"""
    run_id: str = ""
    pipeline: str = "v3"
    date: str = ""
    started_at: str = ""
    finished_at: str = ""

    fetch_seconds: float = 0
    ai_seconds: float = 0
    total_seconds: float = 0

    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0

    news_count: int = 0
    source_count: int = 0
    source_success_rate: float = 0

    output_chars: int = 0
    h2_chapters: int = 0
    events_count: int = 0
    regions_covered: int = 0
    json_valid: bool = False
    html_generated: bool = False
    finish_reason: str = ""
    quality_score: int = 0
    placeholder_count: int = 0

    email_sent: bool = False
    email_success: bool = False

    had_fallback: bool = False
    had_error: bool = False
    error_message: str = ""


def record(r: RunRecord) -> None:
    """写入一条运行记录"""
    init_db()
    conn = sqlite3.connect(str(_get_db()))
    conn.execute("""
        INSERT INTO runs (
            run_id, pipeline, date, started_at, finished_at,
            fetch_seconds, ai_seconds, total_seconds,
            input_tokens, output_tokens, estimated_cost,
            news_count, source_count, source_success_rate,
            output_chars, h2_chapters, events_count, regions_covered,
            json_valid, html_generated, finish_reason, quality_score, placeholder_count,
            email_sent, email_success,
            had_fallback, had_error, error_message
        ) VALUES (?,?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?, ?,?,?)
    """, (
        r.run_id, r.pipeline, r.date, r.started_at, r.finished_at,
        r.fetch_seconds, r.ai_seconds, r.total_seconds,
        r.input_tokens, r.output_tokens, r.estimated_cost,
        r.news_count, r.source_count, r.source_success_rate,
        r.output_chars, r.h2_chapters, r.events_count, r.regions_covered,
        int(r.json_valid), int(r.html_generated), r.finish_reason, r.quality_score, r.placeholder_count,
        int(r.email_sent), int(r.email_success),
        int(r.had_fallback), int(r.had_error), r.error_message,
    ))
    conn.commit()
    conn.close()


def get_recent(days: int = 14, pipeline: str | None = None) -> list[dict]:
    """获取最近 N 天的运行记录"""
    init_db()
    conn = sqlite3.connect(str(_get_db()))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    query = "SELECT * FROM runs WHERE date >= ?"
    params = [since]
    if pipeline:
        query += " AND pipeline = ?"
        params.append(pipeline)
    query += " ORDER BY date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    cols = [d[0] for d in conn.execute("PRAGMA table_info(runs)").fetchall()]
    return [dict(zip(cols, row, strict=True)) for row in rows]


def release_gate(days: int = 14) -> dict:
    """检查是否满足 Release Gate 条件

    Returns:
        {
            "passed": bool,
            "days_checked": int,
            "checks": { "api_success_rate": {...}, ... },
            "summary": str
        }
    """
    records = get_recent(days=days)
    if len(records) < days:
        return {
            "passed": False,
            "days_checked": len(records),
            "checks": {},
            "summary": f"数据不足: {len(records)}/{days} 天",
        }

    n = len(records)

    # 各项检查
    checks = {}

    # API 成功率（非 error + finish_reason != 'error'）
    api_ok = sum(1 for r in records if not r["had_error"] and r["finish_reason"] != "error")
    api_rate = api_ok / n * 100
    checks["api_success_rate"] = {
        "value": round(api_rate, 1),
        "threshold": 99.0,
        "passed": api_rate >= 99.0,
    }

    # JSON 解析成功率
    json_ok = sum(1 for r in records if r["json_valid"])
    json_rate = json_ok / n * 100
    checks["json_parse_rate"] = {
        "value": round(json_rate, 1),
        "threshold": 100.0,
        "passed": json_rate >= 100.0,
    }

    # HTML 生成成功率
    html_ok = sum(1 for r in records if r["html_generated"])
    html_rate = html_ok / n * 100
    checks["html_success_rate"] = {
        "value": round(html_rate, 1),
        "threshold": 100.0,
        "passed": html_rate >= 100.0,
    }

    # 质量评分均值
    scores = [r["quality_score"] for r in records]
    avg_score = sum(scores) / n
    checks["quality_score_avg"] = {
        "value": round(avg_score, 1),
        "threshold": 85,
        "passed": avg_score >= 85,
    }

    # 延迟稳定性（标准差 < 均值的 30%）
    latencies = [r["total_seconds"] for r in records]
    avg_latency = sum(latencies) / n
    if avg_latency > 0:
        variance = sum((val - avg_latency) ** 2 for val in latencies) / n
        std_dev = variance ** 0.5
        cv = std_dev / avg_latency * 100
    else:
        cv = 0
    checks["latency_stability"] = {
        "value": round(cv, 1),
        "threshold": 30.0,
        "passed": cv < 30.0,
    }

    # Token 稳定性
    tokens = [r["input_tokens"] + r["output_tokens"] for r in records]
    avg_tokens = sum(tokens) / n
    if avg_tokens > 0:
        token_variance = sum((val - avg_tokens) ** 2 for val in tokens) / n
        token_cv = token_variance ** 0.5 / avg_tokens * 100
    else:
        token_cv = 0
    checks["token_stability"] = {
        "value": round(token_cv, 1),
        "threshold": 30.0,
        "passed": token_cv < 30.0,
    }

    all_passed = all(c["passed"] for c in checks.values())

    return {
        "passed": all_passed,
        "days_checked": n,
        "checks": checks,
        "summary": "PASS" if all_passed else "FAIL",
    }


def get_trend(days: int = 14) -> dict:
    """获取运营趋势"""
    records = get_recent(days=days)
    if not records:
        return {"status": "no_data"}

    n = len(records)

    costs = [r["estimated_cost"] for r in records]
    latencies = [r["total_seconds"] for r in records]
    sources = [r["source_success_rate"] for r in records]
    qualities = [r["quality_score"] for r in records]

    return {
        "days": n,
        "total_cost": round(sum(costs), 4),
        "avg_cost_per_run": round(sum(costs) / n, 4),
        "monthly_cost_est": round(sum(costs) / n * 30, 2),
        "avg_latency": round(sum(latencies) / n, 0),
        "avg_source_rate": round(sum(sources) / n * 100, 1),
        "avg_quality": round(sum(qualities) / n, 0),
    }
