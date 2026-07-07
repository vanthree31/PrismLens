# PrismLens - 全球战略情报系统

<p align="right">
  <a href="README_EN.md">🇺🇸 English</a> | <b>🇨🇳 中文</b>
</p>

> 多棱视角，穿透迷雾

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

PrismLens 是一个基于 AI 的全球战略情报系统，每日自动聚合 101+ 个全球新闻源，生成结构化的战略情报日报。

---

## ✨ 核心特性

- 🌐 **101+ 全球新闻源** - 覆盖北美、欧洲、中国、俄罗斯、中东、亚洲、非洲等 10+ 区域
- 🤖 **三阶段 AI Pipeline** - 事件提取 → 主题聚类 → 日报生成
- 📊 **风险传导链验证** - 9 条量化传导链，验证地缘事件是否已传导至金融市场
- 🔮 **多阵营叙事对比** - 对比西方、中方、俄方、中东等多方叙事差异
- 📈 **实时市场数据** - 接入 yfinance，覆盖美股/港股/A股/美债/大宗商品
- 📧 **邮件推送** - 定时任务 + SMTP 邮件推送

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- 代理软件（Clash/v2rayN 等）

### 安装

```bash
# 克隆仓库
git clone https://github.com/yourusername/prism-lens.git
cd prism-lens

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/macOS

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填写你的配置
```

### 运行

```bash
python run.py
```

---

## 📦 版本对比

| 功能 | 免费版 | 付费版 |
|------|--------|--------|
| 新闻源 | 10 个 | 101+（持续更新） |
| 日报章节 | 3 章 | 10 章完整分析 |
| 阵营对比 | 单阵营 | 多阵营对比 |
| 历史回溯 | 无 | 90 天 |
| 实时预警 | 无 | S/A 级即时推送 |
| 传导链验证 | 无 | 9 条量化传导链 |
| 市场数据 | 无 | 实时行情 |

### 获取付费版

📧 Email: vanthree31@gmail.com
💬 微信: Sany1ovo
🎮 小黑盒: [个人主页](https://www.xiaoheihe.cn/bbs/user_profile_share?user_id=ad29685205e7&h_src=heyboxapp)
💬 QQ: 1448840796

---

## 📁 项目结构

```
prism-lens/
├── src/                    # 源代码
│   ├── fetcher.py         # RSS 新闻抓取器
│   ├── summarizer.py      # AI 三阶段 Pipeline
│   ├── generator.py       # HTML 报告生成
│   ├── market_data.py     # 市场数据获取
│   ├── risk_scorer.py     # 风险评分
│   ├── event_graph.py     # 事件图谱
│   ├── premium/           # 高级功能（付费）
│   └── utils.py           # 工具函数
├── config/                 # 配置文件
│   ├── sources.yaml       # 新闻源配置
│   └── importance_keywords.yaml  # 评分规则
├── prompts/                # AI 提示词
├── output/                 # 生成的报告
├── data/                   # 运行时数据
└── run.py                  # 主入口
```

---

## ⚙️ 配置说明

### 环境变量 (.env)

```env
# AI API 配置
API_URL=https://api.deepseek.com
API_KEY=your_api_key_here
MODEL_NAME=deepseek-v4-pro

# 代理配置
HTTPS_PROXY=http://127.0.0.1:7890

# 邮件推送（可选）
SMTP_ENABLED=false
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=your_email@qq.com
SMTP_PASS=your_smtp_code
SMTP_TO=recipient@example.com
```

### 命令行参数

```bash
python run.py [OPTIONS]

--no-open       # 不自动打开浏览器
--skip-fetch    # 跳过新闻抓取（使用缓存）
--skip-ai       # 跳过 AI 生成（使用 mock 数据）
--max-news N    # 最大新闻数量（默认 150）
```

---

## 🏗️ 技术架构

```
RSS (101+源) → fetcher (并发抓取) → ImportanceScorer (评分)
    ↓
summarizer (3阶段AI: 事件提取→聚类→日报)
    ↓
generator (HTML幻灯片) → mailer (邮件推送)
    ↓
event_graph + risk_scorer + evolution_tracker
```

---

## 🤝 贡献

欢迎贡献新闻源！请在 `config/sources.yaml` 中添加：

```yaml
- name: "源名称"
  url: "RSS URL"
  type: rss
  region: "区域"
  media_type: mainstream_media
  narrative_alignment: neutral
  credibility: 7
  signal_weight: 7
  bias_score: 3
  weight: 7
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📄 许可证

本项目采用 **Open Core** 开源模式：

- **核心功能**：MIT License - 可自由使用、修改、分发
- **高级功能**：商业授权 - 需购买授权

详见 [LICENSE](LICENSE)（MIT License）和 [COMMERCIAL.md](COMMERCIAL.md)（商业授权说明）。

---

## Contributors

- PrismLens Team - Creator & Maintainer
- Claude - AI Assistant (Code Review & Architecture)

---

## 📞 联系方式

- 📧 Email: vanthree31@gmail.com
- 💬 微信: Sany1ovo
- 🎮 小黑盒: [个人主页](https://www.xiaoheihe.cn/bbs/user_profile_share?user_id=ad29685205e7&h_src=heyboxapp)
- 💬 QQ: 1448840796
