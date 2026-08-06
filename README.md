<p align="center">
  <h1 align="center">🔭 PrismLens</h1>
  <p align="center"><strong>每天早晨，五分钟理解世界。</strong></p>
  <p align="center">
    <a href="README_EN.md">English</a> | <b>中文</b>
  </p>
  <p align="center">
    <img src="https://github.com/vanthree31/PrismLens/actions/workflows/ci.yml/badge.svg" alt="CI">
    <img src="https://img.shields.io/github/license/vanthree31/PrismLens" alt="License">
    <img src="https://img.shields.io/badge/AI-1M%20Context-blue" alt="1M Context">
  </p>
</p>

> PrismLens 每天自动抓取 **100+ 全球新闻源**，用 AI 生成一份专业的国际战略情报日报。
> 不是新闻摘要，是情报分析：**多方立场对比 · 风险传导链 · 90 天事件记忆**。

<img src="screenshots/README-01-hero.png" alt="日报首屏" width="100%">

---

## 为什么是 PrismLens

| | ChatGPT | RSS 阅读器 | PrismLens |
|---|:---:|:---:|:---:|
| **自动推送** | ✗ 每次手动提问 | ✓ 但需要自己筛选 | ✓ 每天定时到达 |
| **长期记忆** | ✗ 每次对话独立 | ✗ | ✓ 90 天连续追踪 |
| **多源综合** | ✗ 单次搜索 | ✗ 逐篇阅读 | ✓ 100+ 新闻源综合分析 |
| **阵营对比** | ✗ 需要手动引导 | ✗ | ✓ 自动多立场叙事对比 |
| **风险传导** | ✗ 需要自己分析 | ✗ | ✓ 自动计算金融影响链 |
| **历史趋势** | ✗ | ✗ | ✓ 事件从 Day 1 到现在的完整演化 |

ChatGPT 是你去问它。**PrismLens 是它每天来找你。**

---

## 日报长什么样

一份 10 章完整情报日报，核心部分长这样：

**未来 48 小时关键观察哨** —— 每个风险点一张卡片：当前状态、升级/缓和触发阈值、监控方式

<img src="screenshots/README-02-watch.png" alt="观察哨卡片" width="100%">

**核心风险矩阵** —— 概率 × 影响 × 量化传导链

<img src="screenshots/README-03-risk.png" alt="风险矩阵" width="100%">

**[📰 查看完整日报样例 →](https://htmlpreview.github.io/?https://github.com/vanthree31/PrismLens/blob/main/samples/%E6%AF%8F%E6%97%A5%E7%AE%80%E6%8A%A5-2026-08-04.html)** *(2026-08-04)*

---

## 核心能力

### 多棱视角

同一事件，五方报道。CNN 怎么写？RT 怎么写？新华社怎么写？半岛电视台怎么写？

不只告诉你"发生了什么"，还告诉你"各方怎么看"。

### 事件记忆

AI 不只是总结今天。它知道昨天发生了什么，上周发生了什么，90 天趋势是什么。

今天的美伊冲突不是"一条新闻"——它是事件的 Day 47，AI 记得从 Day 1 到今天的每一步变化。

### 风险传导链

不告诉你"油价涨了"。告诉你：

```
中东冲突升级 → 霍尔木兹海峡航运中断 → 油价↑ → 通胀↑ → 美债收益率↑ → 科技股估值承压 → 黄金↑
```

这不是新闻摘要。这是情报分析。

### 为什么重要

不写"今天美国宣布对华新关税"。

写"这是过去 90 天内的第 4 轮升级，覆盖范围从前 3 轮的芯片扩大到 AI 基础设施。可能影响：英伟达 Q3 中国区收入预估下调 15-20%……"

---

## 架构

```
100+ RSS 源（并行抓取）
        │
        ▼
  Context Builder（新闻 + 昨日事件 + 市场数据 + 90天趋势）
        │
        ▼
  1M Context LLM（单次推理）
        │
        ▼
  日报（HTML）＋ JSON 结构化输出
        │
   ┌────┼────┬──────────┬──────────┐
   ▼    ▼    ▼          ▼          ▼
 日报  JSON  Email  Telegram  Event DB
                              (SQLite 五表)
```

- **单阶段长上下文 Pipeline** — AI 一次性看到全部信息，不是分段拼接
- **事件知识图谱** — 跨日追踪同一事件，自动识别升级/降温/合并
- **Event Database (SQLite)** — 五表架构，AI 长期记忆，事件永不删除，分析完全版本化
- **风险传导引擎** — 9 条量化传导链，地缘事件 → 金融市场

---

## 开源说明

⚠️ **重要**：本仓库是 PrismLens 核心引擎的开源展示。完整 101 新闻源配置、评分算法与 Pro 能力（完整 10 章日报、阵营对比、90 天记忆、S/A 级预警推送）属于**商业授权部分**，不在仓库中。

## 快速开始

```bash
git clone https://github.com/vanthree31/PrismLens.git
cd PrismLens
pip install -r requirements.txt
cp .env.example .env   # 填写 DeepSeek API Key（或任意 OpenAI 兼容 API）
```

> 开源版需自行提供新闻源配置（`config/sources.yaml`，参考 `sources.yaml.example`）。完整源配置请联系授权：vanthree31@gmail.com

**环境要求：** Python 3.10+ · DeepSeek API Key

---

## 版本

| | 免费版 | Pro |
|---|:---:|:---:|
| **日报** | 3 章核心摘要 | 10 章完整分析 |
| **源** | 精选源 | 100+ 全覆盖 |
| **阵营对比** | — | 多立场 |
| **历史记忆** | — | 90 天 |
| **传导链** | — | 9 条量化 |
| **预警** | — | S/A 级推送 |

**Pro 授权：** vanthree31@gmail.com

---

## 许可证

Open Core — 核心引擎 MIT 开源。Pro 功能需商业授权。

[LICENSE](LICENSE)
