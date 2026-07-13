# PrismLens Event Schema V1

**状态**: DRAFT — 等待评审  
**日期**: 2026-07-13  
**设计原则**: 五张核心表，AI 与代码边界清晰，为 Timeline/Watchlists/Alerts/RAG 预留扩展

---

## 架构定位

```
                RSS / API / Market
                       │
                       ▼
              Event Extraction (AI)
                       │
                       ▼
         ┌─── Event Database (SQLite) ───┐
         │     events                    │
         │     event_revisions           │
         │     event_evidence            │
         │     event_assessments         │
         │     event_relations           │
         └──────────────┬───────────────┘
                        │
        ┌───────┬───────┼───────┬──────────┐
        ▼       ▼       ▼       ▼          ▼
    Timeline  Daily   Search  Alerts   Watchlists
              Report
```

日报不再是中心。Event Database 是中心。日报是数据库的一种 View。

---

## 第一层：events（事件主表）

事件的"身份证"。一个事件从首次发现到归档，只有一条记录。

```sql
CREATE TABLE events (
    -- 主键
    id              TEXT PRIMARY KEY,        -- 全局唯一 ID，格式: evt_{hash8}
    
    -- 身份信息（一旦确定，不再修改）
    canonical_title TEXT NOT NULL,           -- 规范化标题（用于跨日匹配）
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    first_seen_date TEXT NOT NULL,           -- 首次出现的日期 YYYY-MM-DD
    
    -- 可变状态（每次 Revision 时更新）
    display_title   TEXT NOT NULL,           -- 当前最佳显示标题（可随时间变化）
    current_phase   TEXT NOT NULL DEFAULT 'diplomatic',  -- diplomatic/economic/sanction/military/financial/de-escalation
    current_status  TEXT NOT NULL DEFAULT 'active',      -- active/monitoring/cooling/resolved/archived
    last_updated    TEXT NOT NULL DEFAULT (datetime('now')),
    
    -- 分类
    region          TEXT NOT NULL,           -- 主区域（受控词汇）
    event_type      TEXT NOT NULL,           -- 事件类型（受控词汇）
    
    -- 重要性（聚合自所有 Assessment）
    peak_risk_score REAL NOT NULL DEFAULT 0,      -- 历史最高风险分
    current_risk_score REAL NOT NULL DEFAULT 0,   -- 最新风险分
    peak_signal_level TEXT NOT NULL DEFAULT 'C', -- 历史最高信号等级 S/A/B/C
    current_signal_level TEXT NOT NULL DEFAULT 'C',
    
    -- 元数据
    total_evidence_count INTEGER NOT NULL DEFAULT 0,  -- 累计源数量（冗余，加速查询）
    total_revision_count INTEGER NOT NULL DEFAULT 0,  -- 累计 Revision 数
    merged_into TEXT,                         -- 如果被合并，指向目标事件 ID
    is_archived   INTEGER NOT NULL DEFAULT 0  -- 0=活跃, 1=已归档
);

-- 索引
CREATE INDEX idx_events_status ON events(current_status);
CREATE INDEX idx_events_region ON events(region);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_risk ON events(current_risk_score DESC);
CREATE INDEX idx_events_first_seen ON events(first_seen_date);
```

### 关键设计决策

**Event ID 生成策略**：`evt_{hash8}`，hash 由 `canonical_title + primary_actor + region` 的 SHA256 前 8 位组成。这样同一个事件在不同日期提取时，只要规范化标题相同，就能自动获得相同 ID。

**canonical_title vs display_title**：
- `canonical_title`：用于跨日去重匹配，一旦确定永不改变。由 `canonicalize_event_id()` 生成。
- `display_title`：用于前端展示，每次 Revision 时可以更新为更精确的标题。

**current_status 生命周期**：
```
active → monitoring → cooling → resolved
  ↓         ↓           ↓
  └─────────┴───────────┴──→ archived (60天无更新自动归档)
```

---

## 第二层：event_revisions（每日快照）

不是覆盖。每天一条新记录。这是 Timeline 的数据源。

```sql
CREATE TABLE event_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,           -- YYYY-MM-DD
    
    -- 当日状态快照
    phase           TEXT NOT NULL,
    status          TEXT NOT NULL,
    signal_level    TEXT NOT NULL,
    display_title   TEXT NOT NULL,
    summary         TEXT NOT NULL,           -- AI 生成的当日摘要（≤200字）
    
    -- 当日定量指标
    risk_score      REAL NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 0,  -- 当日报道此事件的源数量
    source_diversity TEXT,                   -- 当日阵营分布快照 JSON
    
    -- 阶段变化检测
    phase_transition TEXT,                   -- 如有阶段变化，记录 from→to
    risk_delta      REAL NOT NULL DEFAULT 0, -- 与昨日的风险分差
    
    -- 时间戳
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    
    UNIQUE(event_id, date)                   -- 每天每个事件只有一条 Revision
);

CREATE INDEX idx_revisions_event_date ON event_revisions(event_id, date);
CREATE INDEX idx_revisions_date ON event_revisions(date);
```

### 关键设计决策

**为什么是快照不是增量**：每条 Revision 是当日完整状态。不依赖前一条。这样：
- 删除某一天的数据不影响其他天
- 重新生成某一天的 Assessment 只需更新对应 Revision
- Timeline 渲染不需要计算增量

**phase_transition 检测**：由代码计算。比较当前 Revision 与前一条的 phase。如果不同，记录 `from→to` 和 `risk_delta`。AI 不做这个判断——这是确定性逻辑。

---

## 第三层：event_evidence（新闻源）

每一篇报道过此事件的新闻。这是"地面真相"层——不可变。

```sql
CREATE TABLE event_evidence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,           -- 报道日期
    
    -- 新闻源信息
    source_name     TEXT NOT NULL,           -- Reuters / BBC / 新华社...
    source_region   TEXT NOT NULL,           -- 源所属区域
    narrative_leaning TEXT,                  -- western_liberal / chinese_state / russian_state / ...
    credibility     INTEGER,                 -- 源可信度 1-10
    article_title   TEXT NOT NULL,
    article_url     TEXT,
    article_summary TEXT,                    -- 前 200 字
    
    -- AI 提取的元数据
    key_claim       TEXT,                    -- 这篇报道的核心声明（AI 提取）
    claim_sentiment TEXT,                    -- positive / negative / neutral
    
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_evidence_event ON event_evidence(event_id);
CREATE INDEX idx_evidence_date ON event_evidence(date);
CREATE INDEX idx_evidence_source ON event_evidence(source_name);
```

### 关键设计决策

**Evidence 不可变**：一旦写入，永不修改。这保证：
- 引用可以永久追溯
- 重新跑 AI Assessment 不影响 Evidence
- 引用密度（citation density）计算有稳定基线

**key_claim**：AI 从每篇报道中提取一句核心声明（≤100字）。用于后续 Narrative 对比分析。不是人工标注——是 AI 在提取事件时一并生成的。

---

## 第四层：event_assessments（AI 分析）

AI 对事件的评估。这一层是"可重跑的"——Prompt 升级后，只重跑这一层。

```sql
CREATE TABLE event_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,
    
    -- 风险评估
    risk_score      REAL NOT NULL,           -- 0-100
    risk_trend      TEXT NOT NULL,           -- escalating / stable / cooling
    signal_level    TEXT NOT NULL,           -- S / A / B / C
    confidence      REAL NOT NULL,           -- 0.0 - 1.0
    
    -- AI 分析文本
    analysis_summary TEXT NOT NULL,          -- 2-3 句分析
    narrative_comparison TEXT,               -- 多阵营叙事对比（Markdown 片段）
    escalation_triggers TEXT,               -- 升级触发条件
    deescalation_triggers TEXT,             -- 缓和触发条件
    
    -- 传导链
    transmission_chains_triggered TEXT,     -- 被触发的传导链 JSON 数组
    
    -- 可复现性
    prompt_version  TEXT,                    -- 生成此 Assessment 的 Prompt 版本号
    model_name      TEXT,                    -- 模型名称
    generation_run_id TEXT,                 -- 遥测 run_id
    
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    
    UNIQUE(event_id, date, prompt_version)   -- 同一版本只保留最新
);

CREATE INDEX idx_assessments_event ON event_assessments(event_id, date);
CREATE INDEX idx_assessments_date ON event_assessments(date);
```

### 关键设计决策

**Assessment 与 Revision 分离**：
- Revision：事件的"客观状态"（阶段、信号等级、源数量）
- Assessment：AI 的"主观判断"（风险评分、置信度、叙事分析）

这样当 Prompt 升级时，可以：
1. 保留所有历史 Evidence（不变）
2. 保留所有 Revision（更新 signal_level 等非 AI 字段）
3. 重新生成 Assessment（新 Prompt，新 model_name）
4. 对比新旧 Assessment 的质量

**prompt_version 字段**：每次 Prompt 修改后，`PromptManager.current_version` 会变化。新的 Assessment 会带上新版本号。旧 Assessment 保留。支持"Prompt A/B 对比"。

---

## 第五层：event_relations（事件关系图谱）

事件之间的关系。这是知识图谱的边。

```sql
CREATE TABLE event_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT NOT NULL REFERENCES events(id),
    target_event_id TEXT NOT NULL REFERENCES events(id),
    relation_type   TEXT NOT NULL,           -- causes / influences / contradicts / supersedes / related_to
    
    -- 关系强度
    confidence      REAL NOT NULL DEFAULT 0.5,  -- AI 判断的置信度
    
    -- 说明
    description     TEXT,                    -- AI 解释为什么这两个事件相关
    
    -- 来源
    detected_by     TEXT NOT NULL DEFAULT 'ai', -- ai / manual
    first_detected_date TEXT NOT NULL,
    
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    
    UNIQUE(source_event_id, target_event_id, relation_type)
);

CREATE INDEX idx_relations_source ON event_relations(source_event_id);
CREATE INDEX idx_relations_target ON event_relations(target_event_id);
CREATE INDEX idx_relations_type ON event_relations(relation_type);
```

### 关系类型定义

| 类型 | 含义 | 示例 |
|------|------|------|
| `causes` | A 直接导致 B | 霍尔木兹关闭 → 油价飙升 |
| `influences` | A 间接影响 B | 美国大选 → 台海政策调整 |
| `contradicts` | A 与 B 的叙事冲突 | 美方声明 vs 伊朗声明 |
| `supersedes` | A 被 B 替代（合并事件时使用） | evt_abc → evt_def（合并后） |
| `related_to` | 弱关联 | 韩国芯片出口 → 台海半导体 |

---

## AI 与代码的职责边界

| 数据 | 谁负责 | 何时产生 |
|------|--------|---------|
| event 创建/去重 | 代码 (`canonicalize_event_id`) | Stage 1 提取后 |
| event 合并 | 代码 (merge_events) | 检测到重复时 |
| canonical_title | AI (Stage 1 提取) | 首次提取时 |
| display_title | AI (每次 Revision) | 每日 Revision |
| phase / status | 代码 (keyword detection) | 每日 Revision |
| signal_level | AI 建议, 代码校验 | 每日 Assessment |
| risk_score | AI | 每日 Assessment |
| evidence 记录 | 代码 (从 news_items 提取) | 每次 Revision |
| key_claim | AI | 每次 Evidence 提取 |
| relations | AI 建议, 代码去重 | 每日聚类时 |
| relation confidence | AI | 每日 |
| phase_transition 检测 | 代码 (确定性逻辑) | 每日 Revision |
| 归档 | 代码 (60天无更新自动触发) | 定期清理 |

---

## 去重策略（同一事件跨日识别）

### 三级匹配

```
Level 1: 精确 ID 匹配
  canonicalize_event_id(title) → evt_{hash}
  如果 hash 相同 → 同一事件（确定性，无歧义）

Level 2: 模糊匹配
  比较: display_title 相似度 + region + event_type + primary_actor
  阈值: similarity > 0.7 → 同一事件
  方法: Jaccard similarity on keyword sets
  
Level 3: 人工审核标记（预留）
  字段: events.merged_into
  手动标记 evt_abc → evt_def 合并
```

### 合并流程

```
1. 新事件 evt_new 进入
2. 查询同 region + event_type 的活跃事件
3. 计算 display_title 相似度
4. 如果 similarity > 0.7:
   a. 创建 event_relations (evt_new supersedes evt_old 或 vice versa)
   b. 合并 evidence (UNION, 去重)
   c. 保留风险分更高的事件为主
   d. 更新 events.merged_into
```

---

## 查询示例

### Timeline: 某个事件 30 天演化

```sql
SELECT date, phase, signal_level, risk_score, summary
FROM event_revisions
WHERE event_id = 'evt_a1b2c3d4'
ORDER BY date DESC
LIMIT 30;
```

### 某区域所有活跃事件

```sql
SELECT id, display_title, current_signal_level, current_risk_score
FROM events
WHERE region = '中东' AND current_status = 'active'
ORDER BY current_risk_score DESC;
```

### 最近 7 天新出现的事件

```sql
SELECT id, display_title, region, event_type
FROM events
WHERE first_seen_date >= date('now', '-7 days')
ORDER BY first_seen_date DESC;
```

### 全文搜索（需要 FTS5 扩展）

```sql
-- 建 FTS 索引
CREATE VIRTUAL TABLE events_fts USING fts5(
    display_title, 
    content='events', 
    content_rowid='rowid'
);

-- 搜索
SELECT e.id, e.display_title, e.current_risk_score
FROM events e
JOIN events_fts f ON e.rowid = f.rowid
WHERE events_fts MATCH '霍尔木兹 OR 美伊'
ORDER BY e.current_risk_score DESC;
```

---

## 迁移路径（从 JSON → SQLite）

### 第一阶段：共存
- 保留 `data/events/events_*.json` 日报 JSON（不改现有流程）
- 新增 SQLite 写入（写入两份，JSON 和 SQLite 同时存在）
- 日报生成仍读 JSON

### 第二阶段：切换读
- 新模块（Timeline/Search）读 SQLite
- 日报生成仍读 JSON（兼容）

### 第三阶段：完全切换
- 日报生成改为读 SQLite
- JSON 文件保留为备份（90天后清理）
- `data/prismlens.db` 成为唯一数据源

---

## 扩展预留

| 未来模块 | 需要的字段/表 | 当前 Schema 是否预留 |
|---------|-------------|:---:|
| Watchlists | user_watchlists, watchlist_events | 需新增 |
| Alerts | alert_rules, alert_log | 需新增 |
| Timeline | event_revisions 已满足 | ✅ |
| RAG/搜索 | events_fts (FTS5) | ✅ |
| 知识图谱可视化 | event_relations | ✅ |
| 趋势分析 | event_revisions.risk_score 时间序列 | ✅ |
| 源可信度分析 | event_evidence.credibility 聚合 | ✅ |
| 多用户 | users 表（未来） | 未预留（单用户优先） |

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| V1 DRAFT | 2026-07-13 | 初始设计，五表结构 |
