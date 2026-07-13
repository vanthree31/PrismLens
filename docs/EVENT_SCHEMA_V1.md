# PrismLens Event Schema V1

**状态**: REVIEWED — 已通过评审  
**日期**: 2026-07-13  
**设计原则**: 五张核心表，Identity 与 Expression 彻底分离，Event 永不删除

---

## 核心铁律

> **Event 的身份（Identity）必须稳定；AI 生成的一切内容（标题、摘要、风险评估、叙事分析）都只是这个身份在某一时刻的解释，而不是身份本身。**

### 四条铁律

1. **Identity ≠ Expression** — identity_key 决定 Event ID，AI 生成的标题只是表现
2. **Event 永不删除** — 只允许 ACTIVE → MERGED → ARCHIVED
3. **Evidence 不可变** — 新闻源一旦写入，永久保留（含内容快照，防 404）
4. **Assessment 完全版本化** — 每次 Prompt/模型变更都产生新版本，旧版本保留

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

事件的"身份证"。一个事件从首次发现到归档，只有一条记录。永不删除。

```sql
CREATE TABLE events (
    -- ══ 身份层（Identity）—— 一旦确定，永不修改 ══
    id              TEXT PRIMARY KEY,           -- 全局唯一 ID: evt_{identity_hash}
    identity_key    TEXT NOT NULL UNIQUE,       -- 身份键: TYPE|ACTORS|LOCATION|OBJECT
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    first_seen_date TEXT NOT NULL,              -- 首次出现日期 YYYY-MM-DD
    language        TEXT NOT NULL DEFAULT 'zh', -- 首次提取时的语言

    -- ══ 表现层（Expression）—— AI 生成，可随时间更新 ══
    canonical_title TEXT NOT NULL,              -- AI 初始规范化标题
    display_title   TEXT NOT NULL,              -- 最新显示标题（Revision 时更新）
    aliases         TEXT,                       -- JSON 数组: ["俄乌战争","Ukraine War",...]
    
    -- ══ 状态层 —— 代码管理 ══
    current_phase   TEXT NOT NULL DEFAULT 'diplomatic',
    current_status  TEXT NOT NULL DEFAULT 'active',  -- active/monitoring/cooling/resolved/archived
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
    merged_into   TEXT,                        -- 被合并到目标事件
    archived_at   TEXT,                        -- 归档时间
    archive_reason TEXT                        -- 归档原因
);

-- 索引
CREATE UNIQUE INDEX idx_events_identity ON events(identity_key);
CREATE INDEX idx_events_status ON events(current_status);
CREATE INDEX idx_events_region ON events(region);
CREATE INDEX idx_events_type ON events(event_type);
CREATE INDEX idx_events_risk ON events(current_risk_score DESC);
CREATE INDEX idx_events_first_seen ON events(first_seen_date);
```

### Identity Key 设计

Identity Key 由四个维度组成，决定 Event ID：

```
identity_key = "{event_type}|{actors}|{location}|{object}"

示例:
  "MILITARY|IRAN,ISRAEL|NUCLEAR_FACILITY|AIRSTRIKE"
  "ECONOMIC|US,CHINA|SEMICONDUCTOR|EXPORT_CONTROL"
  "DIPLOMATIC|EU,UKRAINE|BRUSSELS|ACCESSION_TALKS"
```

- **actors**: 按字母排序，逗号分隔，最多 3 个主要参与方
- **location**: 事件发生地或主要影响区域
- **object**: 事件的核心对象（设施/政策/资源/协议名称）
- 所有值使用英文大写 + 下划线（跨语言稳定）

### Event ID 生成

```
identity_key → SHA256 → 前 8 位 → evt_{hash8}
```

标题可以每天变，Identity Key 不变 → Event ID 不变 → 跨日自动匹配。

### aliases 字段

```
["俄乌战争", "Ukraine War", "Russia-Ukraine Conflict", "Special Military Operation"]
```

AI 每天提取时可以追加新的别名。搜索时匹配任意 alias 即命中。JSON 数组存储，不单独建表。

### 合并流程

```
1. evt_new 的 identity_key 与 evt_old 相同 → 同一事件
2. 如果 identity_key 不同但 AI 判断为同一事件:
   a. 比较两个事件的 actor/location/object 相似度
   b. 如果 similarity > 0.8: 更新 identity_key（保留更精确的版本）
   c. 否则: 创建 event_relations (supersedes)
3. 合并时: evidence UNION, aliases MERGE, 保留更早的 first_seen_date
4. 被合并的事件: merged_into → 目标事件 ID
```

### 归档策略

```
60 天无 Revision → current_status = 'cooling'
90 天无 Revision + risk_score < 20 → current_status = 'resolved'
180 天 resolved → archived
```

归档不是删除。Timeline 仍然可以查询。

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

## 第三层：event_evidence（新闻源 — 地面真相）

每一篇报道过此事件的新闻。不可变。包含内容快照以防源 URL 失效。

```sql
CREATE TABLE event_evidence (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,              -- 报道日期

    -- ══ 源信息 ══
    source_name     TEXT NOT NULL,
    source_region   TEXT NOT NULL,
    narrative_leaning TEXT,
    credibility     INTEGER,                   -- 1-10

    -- ══ 文章信息 ══
    article_title   TEXT NOT NULL,
    article_url     TEXT,
    publish_time    TEXT,                       -- 原文发布时间
    author          TEXT,                       -- 作者/通讯社
    language        TEXT DEFAULT 'en',

    -- ══ 内容快照 —— 防 404 ══
    content_snippet TEXT NOT NULL,              -- 前 500 字摘要（永久保存）
    content_hash    TEXT NOT NULL,              -- SHA256 of content_snippet（去重用）

    -- ══ AI 提取 ══
    key_claim       TEXT,                       -- 核心声明（≤100字）
    claim_sentiment TEXT,                       -- positive/negative/neutral

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_evidence_event ON event_evidence(event_id);
CREATE INDEX idx_evidence_date ON event_evidence(date);
CREATE INDEX idx_evidence_source ON event_evidence(source_name);
CREATE UNIQUE INDEX idx_evidence_dedup ON event_evidence(event_id, date, content_hash);
```

### 为什么需要 content_snippet

URL 可能在半年后 404。content_snippet 保证：
- 重跑 Prompt 时，即使 URL 失效，仍有原文摘要可用
- content_hash 用于同一天同一事件的去重
- 500 字足够 AI 做 Narrative 分析

---

## 第四层：event_assessments（AI 分析 — 完全版本化）

每次 AI 分析都是一条新记录。Prompt 升级 → 新版本。旧版本保留用于对比。

```sql
CREATE TABLE event_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL REFERENCES events(id),
    date            TEXT NOT NULL,

    -- ══ 版本信息 ══
    prompt_version  TEXT NOT NULL,              -- PromptManager.current_version
    model_name      TEXT NOT NULL,              -- 模型名
    model_temperature REAL NOT NULL DEFAULT 0.3,
    generation_run_id TEXT,                    -- 遥测 run_id

    -- ══ AI 风险评估 ══
    risk_score      REAL NOT NULL,              -- 0-100
    risk_explanation TEXT,                      -- AI 解释为什么给这个分
    risk_trend      TEXT NOT NULL,              -- escalating/stable/cooling
    signal_level    TEXT NOT NULL,              -- S/A/B/C
    confidence      REAL NOT NULL,              -- 0.0-1.0

    -- ══ AI 分析文本 ══
    analysis_summary TEXT NOT NULL,
    narrative_comparison TEXT,
    escalation_triggers TEXT,
    deescalation_triggers TEXT,

    -- ══ 传导链 ══
    transmission_chains_triggered TEXT,        -- JSON 数组

    -- ══ AI 建议的关系 ══
    suggested_relations TEXT,                   -- JSON: [{target, type, confidence}]

    -- ══ 代码侧校验 ══
    rule_risk_score REAL,                       -- 规则引擎独立计算的分数（用于对比）
    human_reviewed  INTEGER NOT NULL DEFAULT 0,

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_assessments_event ON event_assessments(event_id, date DESC);
CREATE INDEX idx_assessments_version ON event_assessments(prompt_version);
CREATE INDEX idx_assessments_date ON event_assessments(date);
```

### 双轨 Risk 设计

```
AI Risk:   8  (AI 判断)
Rule Risk: 6  (代码规则引擎)
Final:     7  (取平均或加权)
```

当 AI Risk 与 Rule Risk 偏差 > 3 时，标记 `human_reviewed = 0` 待审核。

### 版本化对比

```sql
-- 查看同一事件在不同 Prompt 版本下的风险评分变化
SELECT prompt_version, risk_score, risk_explanation, created_at
FROM event_assessments
WHERE event_id = 'evt_a1b2c3d4' AND date = '2026-07-13'
ORDER BY created_at DESC;
```

这使 Prompt A/B 测试有了数据基础——不是"感觉 Prompt B 更好"，而是"Prompt B 对同一事件的风险判断更接近规则引擎"。

---

## 第五层：event_relations（事件关系图谱 — 版本化）

关系可以随时间变化。今天成立的因果关系，明天可能被新证据推翻。不删除，只标记过期。

```sql
CREATE TABLE event_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_event_id TEXT NOT NULL REFERENCES events(id),
    target_event_id TEXT NOT NULL REFERENCES events(id),
    relation_type   TEXT NOT NULL,              -- causes/influences/contradicts/supersedes/related_to

    -- ══ 版本化 ══
    confidence      REAL NOT NULL DEFAULT 0.5,  -- AI 当前置信度
    confidence_history TEXT,                     -- JSON: [{date, confidence, prompt_version}]
    source          TEXT NOT NULL DEFAULT 'ai',  -- ai/manual/rule
    description     TEXT,                        -- AI 解释为什么这两个事件相关

    -- ══ 生命周期 ══
    first_detected  TEXT NOT NULL,               -- 首次检测到关系的日期
    last_confirmed  TEXT NOT NULL,               -- 最后一次确认此关系仍有效的日期
    expired_at      TEXT,                        -- 如果关系被推翻，记录过期时间
    expire_reason   TEXT,                        -- 过期原因

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_relations_source ON event_relations(source_event_id);
CREATE INDEX idx_relations_target ON event_relations(target_event_id);
CREATE INDEX idx_relations_type ON event_relations(relation_type);
CREATE INDEX idx_relations_active ON event_relations(expired_at) WHERE expired_at IS NULL;
```

### 关系生命周期

```
first_detected: 2026-07-10  ← AI 首次发现
last_confirmed: 2026-07-13  ← 今天 AI 再次确认此关系仍存在
expired_at:     NULL         ← 关系仍有效

--- 三天后 ---

first_detected: 2026-07-10
last_confirmed: 2026-07-13
expired_at:     2026-07-16  ← AI 判断此关系不再成立
expire_reason:  "伊朗宣布停火，霍尔木兹威胁解除"
```

过期的关系不删除。知识图谱可视化时默认隐藏 `expired_at IS NOT NULL` 的边，但历史查询可以看到完整的边演化。

### 关系类型

| 类型 | 含义 | 示例 |
|------|------|------|
| `causes` | A 直接导致 B | 霍尔木兹关闭 → 油价飙升 |
| `influences` | A 间接影响 B | 美国大选 → 台海政策调整 |
| `contradicts` | A 与 B 的叙事冲突 | 美方声明 vs 伊朗声明 |
| `supersedes` | A 被 B 替代 | evt_abc 合并入 evt_def |
| `related_to` | 弱关联 | 韩国芯片出口 → 台海半导体 |

---

## AI 与代码的职责边界

| 职责 | AI | 代码 | 备注 |
|------|:--:|:--:|------|
| identity_key 生成 | | ✅ | 确定性规则 |
| Event ID 生成 | | ✅ | SHA256(identity_key) |
| canonical_title | ✅ | | AI 初始生成，代码永不修改 |
| display_title | ✅ | | 每次 Revision 可更新 |
| aliases 追加 | ✅ | | 追加到 JSON 数组 |
| 去重 | | ✅ | identity_key 精确匹配 + 模糊相似度 |
| 合并 | | ✅ | Evidence UNION, Aliases MERGE |
| Summary / Key Claims | ✅ | | |
| Narrative Comparison | ✅ | | |
| Risk Score | ✅ | ⚠️ | AI 给分 + 规则引擎独立计算（双轨） |
| Risk Explanation | ✅ | | |
| Confidence | ✅ | | |
| Signal Level | ✅ | ⚠️ | AI 建议，代码校验上限 |
| Phase 检测 | | ✅ | 关键词规则引擎 |
| Phase Transition | | ✅ | 确定性比较 |
| Possible Relations | ✅ | | AI 建议关系 |
| Graph Validation | | ✅ | 去重、去环、置信度过滤 |
| Importance | ✅ | ⚠️ | AI 建议，代码取峰值 |
| Tags | ✅ | | |
| Timeline | | ✅ | 从 Revisions 聚合 |
| Archive | | ✅ | 60/90/180 天规则 |
| Human Review 标记 | | ✅ | |AI-Rule Risk 偏差 > 3 时|

---

## 去重策略（同一事件跨日识别）

### 三级匹配

```
Level 1: identity_key 精确匹配
  identity_key 相同 → 同一事件（确定性，无歧义）

Level 2: identity_key 模糊匹配
  比较: event_type + actors 重叠度 + location 相似度
  阈值: actor 重叠 > 0.6 且 location 相同 → 可能同一事件
  方法: Jaccard similarity on actors, exact match on location

Level 3: AI 建议合并 + 人工确认
  AI 在 suggested_relations 中标记 supersedes
  代码检测: 两个事件在同一日 Revision 中的 display_title 相似度 > 0.8
  自动合并（无需人工），但记录到 event_relations
```

### 合并流程

```
1. evt_new 进入，计算 identity_key
2. 查询 identity_key 相同的已有事件 → 命中则直接匹配 (Level 1)
3. 查询同 event_type + location 的活跃事件 (Level 2)
4. actor 重叠度 > 0.6 → 候选合并
5. AI 在 suggested_relations 中标记 supersedes → 确认合并
6. 合并: Evidence UNION, Aliases MERGE, peak_risk_score MAX
7. 被合并事件: merged_into → 目标事件 ID, current_status → 'archived'

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
| V1 REVIEWED | 2026-07-13 | 5 处关键修改：identity_key、Evidence 快照、Assessment 全版本化、Relation 版本化+过期、Event Alias；四条铁律 |
