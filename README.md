# PrismLens

<p align="right">
  <a href="README_EN.md">English</a> | <b>中文</b>
</p>

> **一份日报，替代 100 个 RSS 订阅。**
>
> 从 100+ 全球新闻源自动生成 AI 国际战略情报日报。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

<p align="center">
  <b>100+ 新闻源</b> → <b>AI 分析</b> → <b>10 章情报日报</b> → <b>邮件 / Telegram 推送</b>
</p>

---

## 你得到什么

每天早上，一份专业的国际战略情报日报自动推送到你的收件箱。

| 章节 | 内容 |
|------|------|
| **今日核心判断** | 30 秒读懂今天最重要的变化 |
| **风险矩阵** | 最高优先级风险的概率 × 影响评估 |
| **核心事件分析** | 深度分析 + 多阵营叙事对比 |
| **政府与智库信号** | 官方声明、制裁、军事部署 |
| **金融风险层** | 市场数据、美股/港股/A股、大宗商品、风险传导 |
| **科技竞争** | 半导体、AI 芯片、出口管制、网络战 |
| **事件聚类** | 跨区域事件关联与模式识别 |
| **长期趋势** | 30-90 天战略展望（三情景概率分布） |
| **投资研判** | 美股/港股/A股/韩股板块分析 |
| **原始新闻链接** | 全部引用来源可追溯 |

---

## 核心能力

- **100+ 全球新闻源** — 覆盖 12 个区域（北美、欧洲、中东、亚太、中国、俄罗斯、非洲、南美、大洋洲、中亚、南亚、东南亚）
- **多阵营叙事对比** — 同一事件，不同立场媒体的报道差异一目了然
- **风险传导链** — 追踪地缘事件如何一步步传导至金融市场
- **事件演化追踪** — 从外交→制裁→军事→金融传导，完整生命周期
- **实时市场数据** — 美股/港股/A股/商品/外汇/美债
- **90 天历史记忆** — AI 记得昨天发生了什么，自动关联到今天
- **邮件 + Telegram 推送** — 不打开电脑也能收到

---

## 快速开始

```bash
git clone https://github.com/vanthree31/PrismLens.git
cd PrismLens
pip install -r requirements.txt
cp .env.example .env   # 编辑填写 API Key
python run.py           # 生成今日日报
```

**环境要求：** Python 3.10+，DeepSeek API Key（或其他 OpenAI 兼容 API）。

详细配置见 [`.env.example`](.env.example)。

---

## 版本对比

| | 免费版 | Pro |
|------|------|-----|
| **日报章节** | 3 章（核心摘要） | 10 章（完整分析） |
| **新闻源** | 精选核心源 | 100+ 覆盖全部区域 |
| **阵营对比** | — | 多方叙事 |
| **历史记忆** | — | 90 天 |
| **风险传导链** | — | 9 条量化传导 |
| **市场数据** | — | 实时行情 |
| **实时预警** | — | S/A 级事件即时推送 |

**Pro 授权：** vanthree31@gmail.com

---

## 工作原理

```
定时任务触发（每天早晨）
        │
        ▼
并行抓取 100+ RSS（<15 秒）
        │
        ▼
AI 一次性读取全部新闻 + 昨日事件 + 市场数据 + 90天趋势
        │
        ▼
直接输出完整 Markdown 日报 + JSON 结构化数据
        │
        ▼
渲染 HTML → 发送邮件 → 发送 Telegram → 归档
```

AI 在单次调用中看到全部上下文——不是分段处理，没有中间截断。

---

## 项目结构

```
src/
├── summarizer_v3.py    # 单次 AI 调用生成完整日报
├── fetcher.py          # 100+ RSS 并发抓取
├── generator.py        # HTML 报告渲染
├── market_data.py      # 实时市场数据
├── risk_scorer.py      # 风险评分 + 传导链验证
├── event_graph.py      # 事件提取与去重
├── evolution_tracker.py # 事件生命周期追踪
├── mailer.py           # 邮件推送
├── context_builder.py  # 模块化上下文组装
├── prompt_builder.py   # 分层 Prompt 架构
├── telemetry.py        # 运营指标自动记录
└── premium/            # Pro 功能（需授权）
```

## 许可证

**Open Core** — 核心引擎 MIT 开源。Pro 功能需商业授权。

[LICENSE](LICENSE) · [CONTRIBUTING](CONTRIBUTING.md)
