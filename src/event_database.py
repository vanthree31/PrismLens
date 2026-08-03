"""
Event Database — SQLite 实现 Event Schema V1

五表架构:
  events              — 事件主表（Identity + Expression）
  event_revisions     — 每日快照（Timeline 数据源）
  event_evidence      — 新闻源引用（不可变，含内容快照）
  event_assessments   — AI 分析（完全版本化）
  event_relations     — 事件关系图谱（可过期）

设计文档: docs/EVENT_SCHEMA_V1.md
Phase 1: SQLite + JSON 共存。写入 SQLite 的同时保留 JSON 写入。
"""

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from src.utils import get_data_dir

logger = logging.getLogger("global_news.event_db")

SCHEMA_VERSION = "1.0"

# ── 路径 ──────────────────────────────────────────

_db_path: Path | None = None
_init_lock = threading.Lock()


def get_db_path() -> Path:
    global _db_path
    if _db_path is None:
        _db_path = get_data_dir() / "prismlens.db"
    return _db_path


# ═══════════════════════════════════════════════════
# DDL
# ═══════════════════════════════════════════════════

DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    -- ══ 身份层（Identity）—— 一旦确定，永不修改 ══
    id              TEXT PRIMARY KEY,
    identity_key    TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    first_seen_date TEXT NOT NULL,
    language        TEXT NOT NULL DEFAULT 'zh',

    -- ══ 表现层（Expression）—— AI 生成，可随时间更新 ══
    canonical_title TEXT NOT NULL,
    display_title   TEXT NOT NULL,
    aliases         TEXT,

    -- ══ 状态层 —— 代码管理 ══
    current_phase   TEXT NOT NULL DEFAULT 'diplomatic',
    current_status  TEXT NOT NULL DEFAULT 'active',
    last_updated    TEXT NOT NULL DEFAULT (datetime('now')),

    -- ══ 分类 ══
    region          TEXT NOT NULL,
    event_type      TEXT NOT NULL,

    -- ══ 重要性 —— 聚合自所有 Assessment ══
    peak_risk_score     REAL NOT NULL DEFAULT 0,
    current_risk_score  REAL NOT NULL DEFAULT 0,
    peak_signal_level   TEXT NOT NULL DEFAULT 'C',
    current_signal_level TEXT NOT NULL DEFAULT 'C',

    -- ══ 冗余计数 —— 加速查询 ══
    total_evidence_count  INTEGER NOT NULL DEFAULT 0,
    total_revision_count  INTEGER NOT NULL DEFAULT 0,
    total_assessment_count INTEGER NOT NULL DEFAULT 0,

    -- ══ 生命周期 —— 永不 DELETE ══
    merged_into   TEXT,
    archived_at   TEXT,
    archive_reason TEXT
);
"""

DDL_REVISIONS = """
CREATE TABLE IF NOT EXISTS event_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,

    -- 当日状态快照
    phase           TEXT NOT NULL,
    status          TEXT NOT NULL,
    signal_level    TEXT NOT NULL,
    display_title   TEXT NOT NULL,
    summary         TEXT NOT NULL,

    -- 当日定量指标
    risk_score      REAL NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,
    source_diversity TEXT,

    -- 阶段变化检测
    phase_transition TEXT,
    risk_delta      REAL NOT NULL DEFAULT 0,

    created_at      TEXT NOT NULL DEFAULT (datetime('now')),

    UNIQUE(event_id, date)
);
"""

DDL_EVIDENCE = """
CREATE TABLE IF NOT EXISTS event_evidence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,

    -- ══ 源信息 ══
    source_name     TEXT NOT NULL,
    source_region   TEXT NOT NULL,
    narrative_leaning TEXT,
    credibility     INTEGER,

    -- ══ 文章信息 ══
    article_title   TEXT NOT NULL,
    article_url     TEXT,
    publish_time    TEXT,
    author          TEXT,
    language        TEXT DEFAULT 'en',

    -- ══ 内容快照 —— 防 404 ══
    content_snippet TEXT NOT NULL,
    content_hash    TEXT NOT NULL,

    -- ══ AI 提取 ══
    key_claim       TEXT,
    claim_sentiment TEXT,

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_ASSESSMENTS = """
CREATE TABLE IF NOT EXISTS event_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,

    -- ══ 版本信息 ══
    prompt_version  TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    model_temperature REAL NOT NULL DEFAULT 0.3,
    generation_run_id TEXT,

    -- ══ AI 风险评估 ══
    risk_score      REAL NOT NULL,
    risk_explanation TEXT,
    risk_trend      TEXT NOT NULL,
    signal_level    TEXT NOT NULL,
    confidence      REAL NOT NULL,

    -- ══ AI 分析文本 ══
    analysis_summary TEXT NOT NULL,
    narrative_comparison TEXT,
    escalation_triggers TEXT,
    deescalation_triggers TEXT,

    -- ══ 传导链 ══
    transmission_chains_triggered TEXT,

    -- ══ AI 建议的关系 ══
    suggested_relations TEXT,

    -- ══ 代码侧校验 ══
    rule_risk_score REAL,
    human_reviewed  INTEGER NOT NULL DEFAULT 0,

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DDL_RELATIONS = """
CREATE TABLE IF NOT EXISTS event_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT NOT NULL REFERENCES events(id),
    target_event_id TEXT NOT NULL REFERENCES events(id),
    relation_type   TEXT NOT NULL,

    -- ══ 版本化 ══
    confidence      REAL NOT NULL DEFAULT 0.5,
    confidence_history TEXT,
    source          TEXT NOT NULL DEFAULT 'ai',
    description     TEXT,

    -- ══ 生命周期 ══
    first_detected  TEXT NOT NULL,
    last_confirmed  TEXT NOT NULL,
    expired_at      TEXT,
    expire_reason   TEXT,

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# ── 索引 ──────────────────────────────────────────

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_identity ON events(identity_key);",
    "CREATE INDEX IF NOT EXISTS idx_events_status ON events(current_status);",
    "CREATE INDEX IF NOT EXISTS idx_events_region ON events(region);",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_risk ON events(current_risk_score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_events_first_seen ON events(first_seen_date);",
    "CREATE INDEX IF NOT EXISTS idx_revisions_event_date ON event_revisions(event_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_revisions_date ON event_revisions(date);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_event ON event_evidence(event_id);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_date ON event_evidence(date);",
    "CREATE INDEX IF NOT EXISTS idx_evidence_source ON event_evidence(source_name);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_dedup ON event_evidence(event_id, date, content_hash);",
    "CREATE INDEX IF NOT EXISTS idx_assessments_event ON event_assessments(event_id, date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_assessments_version ON event_assessments(prompt_version);",
    "CREATE INDEX IF NOT EXISTS idx_assessments_date ON event_assessments(date);",
    "CREATE INDEX IF NOT EXISTS idx_relations_source ON event_relations(source_event_id);",
    "CREATE INDEX IF NOT EXISTS idx_relations_target ON event_relations(target_event_id);",
    "CREATE INDEX IF NOT EXISTS idx_relations_type ON event_relations(relation_type);",
    "CREATE INDEX IF NOT EXISTS idx_relations_active ON event_relations(expired_at) WHERE expired_at IS NULL;",
]

# FTS5 — 全文搜索
DDL_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    display_title,
    content='events',
    content_rowid='rowid'
);
"""


def init_db() -> None:
    """初始化 Event Database — 建表 + 索引 + FTS5"""
    with _init_lock:
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")

            conn.executescript(DDL_EVENTS)
            conn.executescript(DDL_REVISIONS)
            conn.executescript(DDL_EVIDENCE)
            conn.executescript(DDL_ASSESSMENTS)
            conn.executescript(DDL_RELATIONS)

            for idx_sql in INDEXES:
                conn.execute(idx_sql)

            # FTS5 — 忽略已存在报错
            try:
                conn.executescript(DDL_FTS)
            except sqlite3.OperationalError:
                pass

            conn.commit()
            logger.info(f"Event DB 初始化完成: {db_path} (journal=WAL, foreign_keys=ON)")
        finally:
            conn.close()


# ═══════════════════════════════════════════════════
# identity_key 生成器
# ═══════════════════════════════════════════════════

# 事件类型 → 代码映射
EVENT_TYPE_MAP = {
    "军事冲突": "MILITARY",
    "军事": "MILITARY",
    "外交": "DIPLOMATIC",
    "外交谈判": "DIPLOMATIC",
    "经济": "ECONOMIC",
    "经济制裁": "ECONOMIC",
    "制裁": "ECONOMIC",
    "金融": "FINANCIAL",
    "金融市场": "FINANCIAL",
    "科技": "TECH",
    "科技竞争": "TECH",
    "能源": "ENERGY",
    "资源": "ENERGY",
    "政治": "POLITICAL",
    "选举": "POLITICAL",
    "社会": "SOCIAL",
    "自然灾害": "NATURAL",
    "公共卫生": "HEALTH",
}

# 区域 → 代码
REGION_CODE_MAP = {
    "北美": "NA",
    "欧洲": "EU",
    "亚洲": "ASIA",
    "中东": "ME",
    "非洲": "AF",
    "南美": "SA",
    "大洋洲": "OC",
    "全球": "GLOBAL",
}


def make_identity_key(
    event_type: str,
    actors: list[str],
    location: str = "",
    object_: str = "",
) -> str:
    """生成稳定的事件 identity_key。

    格式: EVENT_TYPE|ACTORS|LOCATION|OBJECT

    - actors: 字母排序，逗号分隔，最多 3 个
    - 所有值使用英文大写 + 下划线

    Args:
        event_type: 事件类型（中文或英文）
        actors: 参与方列表
        location: 事件发生地
        object_: 事件核心对象

    Returns:
        identity_key，例如 "MILITARY|IRAN,ISRAEL|NUCLEAR_FACILITY|AIRSTRIKE"
    """
    # 类型代码
    type_code = EVENT_TYPE_MAP.get(event_type, event_type.upper().replace(" ", "_"))

    # actors: 排序、去重、最多 3 个
    sorted_actors = sorted({a.strip().upper() for a in actors if a.strip()})
    actors_str = ",".join(sorted_actors[:3])

    # location 和 object: 清理格式化
    loc = location.strip().upper().replace(" ", "_") if location else ""
    obj = object_.strip().upper().replace(" ", "_") if object_ else ""

    return f"{type_code}|{actors_str}|{loc}|{obj}"


def make_event_id(identity_key: str) -> str:
    """从 identity_key 生成全局唯一 Event ID。

    identity_key → SHA256 → 前 8 位 → evt_{hash8}
    """
    h = hashlib.sha256(identity_key.encode()).hexdigest()[:8]
    return f"evt_{h}"


def _derive_identity_from_event(event) -> tuple[str, str]:
    """从 Event 对象推导 identity_key 和 event_id。

    使用 Event 的 domains, actors, phase 字段推导 event_type 和 location。
    如果 Event 没有 object 信息，使用 title 作为 fallback。

    Returns:
        (identity_key, event_id)
    """
    # event_type: 从 domains 取第一个，fallback 到 phase
    if event.domains:
        event_type = event.domains[0]
    else:
        event_type = event.phase if hasattr(event, "phase") else "DIPLOMATIC"

    # actors
    actors = event.actors if hasattr(event, "actors") and event.actors else []

    # location: 从 domains 或 region 推导
    location = ""
    if hasattr(event, "domains") and len(event.domains) > 1:
        location = event.domains[1]

    # object_: 从 title 提取核心名词
    object_ = event.title if hasattr(event, "title") else ""

    identity_key = make_identity_key(event_type, actors, location, object_)
    event_id = make_event_id(identity_key)
    return identity_key, event_id


def _get_db_connection() -> sqlite3.Connection:
    """获取数据库连接（自动初始化）。"""
    db_path = get_db_path()
    if not db_path.exists():
        init_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════
# EventWriter
# ═══════════════════════════════════════════════════


class EventWriter:
    """将每日事件数据写入 Event Database。

    用法:
        writer = EventWriter()
        writer.write_daily_events(event_graph.events, extracted_data, today)
    """

    def __init__(self):
        self._conn: sqlite3.Connection | None = None
        self._prompt_version = "v3"
        self._model_name = ""

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _get_db_connection()
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── 主入口 ────────────────────────────────────

    def write_daily_events(
        self,
        events: list,
        extracted_data: dict | None,
        date_str: str,
        prompt_version: str = "v3",
        model_name: str = "",
    ) -> int:
        """每日事件写入主入口。

        Args:
            events: Event 对象列表 (来自 event_graph.py)
            extracted_data: 结构化提取的原始 dict（含 top_risks/watchpoints 等）
            date_str: 日期 YYYY-MM-DD
            prompt_version: Prompt 版本标识
            model_name: 模型名

        Returns:
            写入的事件数量
        """
        self._prompt_version = prompt_version
        self._model_name = model_name

        count = 0
        try:
            for event in events:
                try:
                    self._write_one_event(event, date_str)
                    count += 1
                except Exception as e:
                    logger.debug(f"写入事件失败 [{getattr(event, 'event_id', '?')}]: {e}")

            # 写入事件关系（来自 extracted_data 的 top_risks 和事件间的关联）
            if extracted_data and count >= 2:
                try:
                    self._write_relations_from_extraction(extracted_data, date_str)
                except Exception as e:
                    logger.debug(f"写入事件关系失败: {e}")

            self.conn.commit()
            if count > 0:
                logger.info(f"Event DB 写入完成: {count} 个事件 ({date_str})")
        except Exception as e:
            logger.warning(f"Event DB 批量写入异常: {e}")
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self.close()

        return count

    # ── 单事件写入 ────────────────────────────────

    def _write_one_event(self, event, date_str: str) -> str:
        """写入单个事件（含 revision + evidence + assessment）。

        Returns:
            event_id
        """
        identity_key, event_id = _derive_identity_from_event(event)
        region = _infer_region(event, date_str)
        event_type = _infer_event_type(event)

        # 1. Upsert events 主表
        self._upsert_event(
            event_id=event_id,
            identity_key=identity_key,
            date_str=date_str,
            title=event.title if hasattr(event, "title") else "",
            region=region,
            event_type=event_type,
            signal_level=event.signal_level if hasattr(event, "signal_level") else "C",
            phase=event.phase if hasattr(event, "phase") else "diplomatic",
            risk_score=_estimate_risk(event),
        )

        # 2. Revision
        self._add_revision(
            event_id=event_id,
            date_str=date_str,
            phase=event.phase if hasattr(event, "phase") else "diplomatic",
            signal_level=event.signal_level if hasattr(event, "signal_level") else "C",
            title=event.title if hasattr(event, "title") else "",
            summary=event.summary if hasattr(event, "summary") else "",
            risk_score=_estimate_risk(event),
            evidence_count=len(event.sources) if hasattr(event, "sources") else 0,
        )

        # 3. Evidence（每个 source 一条）
        sources = event.sources if hasattr(event, "sources") and event.sources else []
        for src_name in sources[:10]:  # 最多10条
            self._add_evidence(
                event_id=event_id,
                date_str=date_str,
                source_name=src_name,
                source_region=region,
                article_title=event.title if hasattr(event, "title") else src_name,
                snippet=event.summary[:500] if hasattr(event, "summary") else "",
            )

        # 4. Assessment
        self._add_assessment(
            event_id=event_id,
            date_str=date_str,
            risk_score=_estimate_risk(event),
            signal_level=event.signal_level if hasattr(event, "signal_level") else "C",
            confidence=_estimate_confidence(event),
            summary=event.summary if hasattr(event, "summary") else "",
            trend=event.trend if hasattr(event, "trend") else "stable",
        )

        return event_id

    # ── SQL 方法 ──────────────────────────────────

    def _upsert_event(
        self,
        event_id: str,
        identity_key: str,
        date_str: str,
        title: str,
        region: str,
        event_type: str,
        signal_level: str,
        phase: str,
        risk_score: float,
    ):
        """INSERT OR REPLACE 事件主表。保留已有 peak 值。"""
        existing = self.conn.execute(
            "SELECT peak_risk_score, peak_signal_level, first_seen_date FROM events WHERE id=?",
            (event_id,),
        ).fetchone()

        if existing:
            peak_risk = max(existing["peak_risk_score"], risk_score)
            peak_signal = _max_signal(existing["peak_signal_level"], signal_level)
            first_seen = existing["first_seen_date"]
        else:
            peak_risk = risk_score
            peak_signal = signal_level
            first_seen = date_str

        self.conn.execute(
            """
            INSERT OR REPLACE INTO events (
                id, identity_key, first_seen_date, language,
                canonical_title, display_title, current_phase,
                current_status, region, event_type,
                peak_risk_score, current_risk_score,
                peak_signal_level, current_signal_level,
                total_evidence_count, total_revision_count,
                total_assessment_count, last_updated
            ) VALUES (?, ?, ?, 'zh', ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, 0, 0, 0, datetime('now'))
            """,
            (
                event_id,
                identity_key,
                first_seen,
                title,
                title,
                phase,
                region,
                event_type,
                peak_risk,
                risk_score,
                peak_signal,
                signal_level,
            ),
        )

    def _add_revision(
        self,
        event_id: str,
        date_str: str,
        phase: str,
        signal_level: str,
        title: str,
        summary: str,
        risk_score: float,
        evidence_count: int,
    ):
        """INSERT OR REPLACE 每日快照（UNIQUE(event_id, date)）。"""
        # 计算 risk_delta：比较昨日
        prev = self.conn.execute(
            "SELECT risk_score FROM event_revisions WHERE event_id=? AND date<? ORDER BY date DESC LIMIT 1",
            (event_id, date_str),
        ).fetchone()
        risk_delta = risk_score - prev["risk_score"] if prev else 0.0

        # 检测 phase transition
        prev_phase_row = self.conn.execute(
            "SELECT phase FROM event_revisions WHERE event_id=? AND date<? ORDER BY date DESC LIMIT 1",
            (event_id, date_str),
        ).fetchone()
        phase_transition = None
        if prev_phase_row and prev_phase_row["phase"] != phase:
            phase_transition = f"{prev_phase_row['phase']}→{phase}"

        self.conn.execute(
            """
            INSERT OR REPLACE INTO event_revisions (
                event_id, date, phase, status, signal_level,
                display_title, summary, risk_score, evidence_count,
                risk_delta, phase_transition
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                date_str,
                phase,
                signal_level,
                title,
                summary[:200] if summary else "",
                risk_score,
                evidence_count,
                risk_delta,
                phase_transition,
            ),
        )

    def _add_evidence(
        self,
        event_id: str,
        date_str: str,
        source_name: str,
        source_region: str,
        article_title: str,
        snippet: str = "",
    ):
        """INSERT 新闻源引用。content_hash 去重。"""
        snippet_clean = snippet[:500] if snippet else article_title[:200]
        content_hash = hashlib.sha256(snippet_clean.encode()).hexdigest()

        # 去重检查
        dup = self.conn.execute(
            "SELECT id FROM event_evidence WHERE event_id=? AND date=? AND content_hash=?",
            (event_id, date_str, content_hash),
        ).fetchone()
        if dup:
            return

        self.conn.execute(
            """
            INSERT INTO event_evidence (
                event_id, date, source_name, source_region,
                article_title, content_snippet, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                date_str,
                source_name,
                source_region,
                article_title,
                snippet_clean,
                content_hash,
            ),
        )

    def _add_assessment(
        self,
        event_id: str,
        date_str: str,
        risk_score: float,
        signal_level: str,
        confidence: float,
        summary: str,
        trend: str = "stable",
    ):
        """INSERT AI 分析记录。完全版本化。"""
        self.conn.execute(
            """
            INSERT INTO event_assessments (
                event_id, date, prompt_version, model_name,
                model_temperature, risk_score, risk_trend,
                signal_level, confidence, analysis_summary
            ) VALUES (?, ?, ?, ?, 0.3, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                date_str,
                self._prompt_version,
                self._model_name,
                risk_score,
                trend,
                signal_level,
                confidence,
                summary[:500] if summary else "",
            ),
        )

    # ── 关系写入 ──────────────────────────────────

    def _write_relations_from_extraction(self, extracted_data: dict, date_str: str):
        """从 extracted_data 中提取事件关系并写入。"""
        events_list = extracted_data.get("events", [])

        if len(events_list) < 2:
            return

        # 为同日事件创建 related_to 关系
        event_ids = []
        for evt_dict in events_list:
            if isinstance(evt_dict, dict):
                eid = evt_dict.get("event_id", "")
            else:
                eid = getattr(evt_dict, "event_id", "")
            if eid:
                event_ids.append(eid)

        # 简单的共现关系：同日事件两两之间创建 related_to
        for i in range(len(event_ids)):
            for j in range(i + 1, min(i + 3, len(event_ids))):
                self._add_relation(
                    source_id=event_ids[i],
                    target_id=event_ids[j],
                    rel_type="related_to",
                    confidence=0.3,
                    date_str=date_str,
                    description="同日出现",
                )

    def _add_relation(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        confidence: float,
        date_str: str,
        description: str = "",
    ):
        """INSERT OR UPDATE 事件关系。"""
        existing = self.conn.execute(
            """SELECT id, confidence_history FROM event_relations
               WHERE source_event_id=? AND target_event_id=? AND relation_type=? AND expired_at IS NULL""",
            (source_id, target_id, rel_type),
        ).fetchone()

        if existing:
            # 更新 last_confirmed + confidence_history
            hist = json.loads(existing["confidence_history"] or "[]")
            hist.append(
                {"date": date_str, "confidence": confidence, "prompt_version": self._prompt_version}
            )
            self.conn.execute(
                "UPDATE event_relations SET last_confirmed=?, confidence=?, confidence_history=? WHERE id=?",
                (date_str, confidence, json.dumps(hist, ensure_ascii=False), existing["id"]),
            )
        else:
            hist = [
                {"date": date_str, "confidence": confidence, "prompt_version": self._prompt_version}
            ]
            self.conn.execute(
                """
                INSERT INTO event_relations (
                    source_event_id, target_event_id, relation_type,
                    confidence, confidence_history, source, description,
                    first_detected, last_confirmed
                ) VALUES (?, ?, ?, ?, ?, 'ai', ?, ?, ?)
                """,
                (
                    source_id,
                    target_id,
                    rel_type,
                    confidence,
                    json.dumps(hist, ensure_ascii=False),
                    description,
                    date_str,
                    date_str,
                ),
            )


# ── 辅助函数 ──────────────────────────────────────

SIGNAL_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}


def _max_signal(a: str, b: str) -> str:
    """返回较高的信��级别。"""
    return a if SIGNAL_RANK.get(a, 0) >= SIGNAL_RANK.get(b, 0) else b


def _estimate_risk(event) -> float:
    """从 Event 估算风险分数（0-100）。

    基于 signal_level 的基线 + phase 加成。
    待规则引擎集成后可替换为精确计算。
    """
    base = {"S": 85, "A": 65, "B": 45, "C": 25}.get(
        event.signal_level if hasattr(event, "signal_level") else "C", 25
    )
    phase_bonus = {
        "military": 15,
        "financial": 10,
        "sanction": 8,
        "economic": 5,
        "diplomatic": 0,
        "de-escalation": -10,
    }.get(event.phase if hasattr(event, "phase") else "diplomatic", 0)
    return max(0, min(100, base + phase_bonus))


def _estimate_confidence(event) -> float:
    """从 Event 估算置信度（0.0-1.0）。"""
    conf_map = {"高": 0.9, "中": 0.6, "低": 0.3}
    if hasattr(event, "confidence") and event.confidence in conf_map:
        return conf_map[event.confidence]
    return 0.6


def _infer_region(event, date_str: str = "") -> str:
    """从 Event 推导区域。"""
    if hasattr(event, "domains") and event.domains:
        # 检查 domains 是否包含区域名
        for d in event.domains:
            if d in REGION_CODE_MAP:
                return d
    return "全球"


def _infer_event_type(event) -> str:
    """从 Event 推导事件类型。"""
    if hasattr(event, "domains") and event.domains:
        for d in event.domains:
            if d in EVENT_TYPE_MAP:
                return d
    if hasattr(event, "phase") and event.phase:
        # phase → type 映射
        ptype = {
            "military": "军事",
            "diplomatic": "外交",
            "economic": "经济",
            "financial": "金融",
            "sanction": "经济",
        }.get(event.phase, "政治")
        return ptype
    return "政治"


# ═══════════════════════════════════════════════════
# EventReader
# ═══════════════════════════════════════════════════


class EventReader:
    """从 Event Database 查询事件数据。

    用法:
        reader = EventReader()
        events = reader.get_active_events(region="中东")
        timeline = reader.get_timeline("evt_a1b2c3d4")
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or get_db_path()
        if not self._db_path.exists():
            init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _to_dict(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def _to_list(self, rows: list[sqlite3.Row]) -> list[dict]:
        return [dict(r) for r in rows]

    # ── 单事件查询 ────────────────────────────────

    def get_event_by_id(self, event_id: str) -> dict | None:
        """按 ID 查询单个事件。"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return self._to_dict(row)
        finally:
            conn.close()

    def get_event_by_identity_key(self, identity_key: str) -> dict | None:
        """按 identity_key 查询单个事件。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM events WHERE identity_key=?", (identity_key,)
            ).fetchone()
            return self._to_dict(row)
        finally:
            conn.close()

    # ── 集合查询 ──────────────────────────────────

    def get_events_by_date(self, date_str: str, limit: int = 100) -> list[dict]:
        """某日所有事件（通过 revisions 关联）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT DISTINCT e.* FROM events e
                   INNER JOIN event_revisions r ON e.id = r.event_id
                   WHERE r.date = ?
                   ORDER BY e.current_risk_score DESC
                   LIMIT ?""",
                (date_str, limit),
            ).fetchall()
            return self._to_list(rows)
        finally:
            conn.close()

    def get_active_events(self, region: str | None = None, min_risk: float = 0) -> list[dict]:
        """活跃事件，按风险降序。可选按区域过滤。"""
        conn = self._connect()
        try:
            if region:
                rows = conn.execute(
                    """SELECT * FROM events
                       WHERE current_status='active' AND region=? AND current_risk_score >= ?
                       ORDER BY current_risk_score DESC""",
                    (region, min_risk),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM events
                       WHERE current_status='active' AND current_risk_score >= ?
                       ORDER BY current_risk_score DESC""",
                    (min_risk,),
                ).fetchall()
            return self._to_list(rows)
        finally:
            conn.close()

    def get_events_by_region(self, region: str, status: str = "active") -> list[dict]:
        """按区域获取事件。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE region=? AND current_status=? ORDER BY current_risk_score DESC",
                (region, status),
            ).fetchall()
            return self._to_list(rows)
        finally:
            conn.close()

    def get_recent_events(self, days: int = 7) -> list[dict]:
        """最近 N 天首次出现的事件。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE first_seen_date >= date('now', ?) ORDER BY first_seen_date DESC",
                (f"-{days} days",),
            ).fetchall()
            return self._to_list(rows)
        finally:
            conn.close()

    # ── Timeline ───────────────────────────────────

    def get_timeline(self, event_id: str, days: int = 30) -> list[dict]:
        """单事件 N 天演化时间线（按日期降序）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT date, phase, signal_level, risk_score, summary,
                          phase_transition, risk_delta, evidence_count
                   FROM event_revisions
                   WHERE event_id = ?
                   ORDER BY date DESC
                   LIMIT ?""",
                (event_id, days),
            ).fetchall()
            return self._to_list(rows)
        finally:
            conn.close()

    # ── 搜索 ───────────────────────────────────────

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """文本搜索（LIKE over display_title + canonical_title + aliases）。"""
        conn = self._connect()
        try:
            pattern = f"%{query}%"
            rows = conn.execute(
                """SELECT * FROM events
                   WHERE display_title LIKE ? OR canonical_title LIKE ? OR aliases LIKE ?
                   ORDER BY current_risk_score DESC
                   LIMIT ?""",
                (pattern, pattern, pattern, limit),
            ).fetchall()
            return self._to_list(rows)
        finally:
            conn.close()

    # ── 统计 ───────────────────────────────────────

    def counts(self) -> dict[str, int]:
        """各表行数统计（用于测试/运维）。"""
        conn = self._connect()
        try:
            tables = [
                "events",
                "event_revisions",
                "event_evidence",
                "event_assessments",
                "event_relations",
            ]
            result = {}
            for t in tables:
                row = conn.execute(f"SELECT COUNT(*) as c FROM {t}").fetchone()
                result[t] = row["c"] if row else 0
            return result
        finally:
            conn.close()


# ═══════════════════════════════════════════════════
# 历史数据回填
# ═══════════════════════════════════════════════════


@dataclass
class BackfillResult:
    files_processed: int = 0
    files_skipped: int = 0
    events_created: int = 0
    revisions_inserted: int = 0
    evidence_inserted: int = 0
    assessments_inserted: int = 0
    dry_run: bool = False


def backfill_from_json(
    db_path: Path | None = None,
    days: int | None = None,
    dry_run: bool = False,
) -> BackfillResult:
    """从 data/events/*.json 回填历史数据到 SQLite。

    Args:
        db_path: 数据库路径（默认 data/prismlens.db）
        days: 回填最近 N 天（None = 全部）
        dry_run: True 则只统计不写入

    Returns:
        BackfillResult
    """
    from src.event_graph import load_event_graph

    db_path = db_path or get_db_path()
    result = BackfillResult(dry_run=dry_run)

    events_dir = get_data_dir() / "events"
    if not events_dir.exists():
        logger.warning(f"事件目录不存在: {events_dir}")
        return result

    # 按时间顺序排列（升序）—— 保证 phase_transition 正确
    json_files = sorted(
        [f for f in events_dir.glob("events_*.json") if not f.name.endswith("_v3.json")],
        key=lambda f: f.stem.replace("events_", ""),
    )

    if days is not None and len(json_files) > days:
        json_files = json_files[-days:]

    logger.info(f"开始回填: {len(json_files)} 个文件" + (" (dry-run)" if dry_run else ""))

    writer = None if dry_run else EventWriter()

    try:
        for fpath in json_files:
            try:
                date_str = fpath.stem.replace("events_", "")
                graph = load_event_graph(date_str)

                if not graph or not graph.events:
                    result.files_skipped += 1
                    continue

                if dry_run:
                    result.events_created += len(graph.events)
                    result.revisions_inserted += len(graph.events)
                    result.evidence_inserted += sum(
                        len(e.sources) if hasattr(e, "sources") and e.sources else 0
                        for e in graph.events
                    )
                    result.assessments_inserted += len(graph.events)
                else:
                    n = writer.write_daily_events(
                        graph.events,
                        None,
                        date_str,
                        prompt_version="backfill",
                        model_name="backfill",
                    )
                    result.events_created += n
                    result.revisions_inserted += n
                    result.assessments_inserted += n
                    result.evidence_inserted += sum(
                        len(e.sources) if hasattr(e, "sources") and e.sources else 0
                        for e in graph.events
                    )

                result.files_processed += 1

                if result.files_processed % 10 == 0:
                    logger.info(f"  回填进度: {result.files_processed}/{len(json_files)}")

            except Exception as e:
                logger.warning(f"回填文件失败 [{fpath.name}]: {e}")
                result.files_skipped += 1
                # 单文件失败不影响后续
                if writer and writer._conn:
                    try:
                        writer.conn.rollback()
                    except Exception:
                        pass
    finally:
        if writer:
            writer.close()

    logger.info(
        f"回填完成: {result.files_processed} 文件, "
        f"{result.events_created} 事件, "
        f"{result.files_skipped} 跳过" + (" (dry-run, 未实际写入)" if dry_run else "")
    )

    return result


# ═══════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PrismLens Event Database 工具")
    parser.add_argument("--backfill", action="store_true", help="从 JSON 文件回填历史数据")
    parser.add_argument("--days", type=int, default=None, help="回填最近 N 天")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入")
    parser.add_argument("--init", action="store_true", help="仅初始化数据库")
    parser.add_argument("--stats", action="store_true", help="显示数据库统计")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )

    if args.init:
        init_db()
        print("数据库初始化完成:", get_db_path())

    elif args.backfill:
        result = backfill_from_json(days=args.days, dry_run=args.dry_run)
        print(f"\n回填结果: {result}")

    elif args.stats:
        reader = EventReader()
        stats = reader.counts()
        print(f"数据库: {get_db_path()}")
        for table, count in stats.items():
            print(f"  {table}: {count} 行")

    else:
        parser.print_help()
