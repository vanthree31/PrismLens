# PrismLens - Global Strategic Intelligence System

<p align="right">
  <b>🇺🇸 English</b> | <a href="README.md">🇨🇳 中文</a>
</p>

> Multiple Perspectives, See Through the Noise

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

PrismLens is an AI-powered global strategic intelligence system that automatically aggregates 101+ global news sources daily to generate structured strategic intelligence briefings.

---

## ✨ Core Features

- 🌐 **101+ Global News Sources** - Covering North America, Europe, China, Russia, Middle East, Asia, Africa across 10+ regions
- 🤖 **Three-Stage AI Pipeline** - Event Extraction → Topic Clustering → Report Generation
- 📊 **Risk Transmission Chain Validation** - 9 quantitative transmission chains validating geopolitical events' market impact
- 🔮 **Multi-Camp Narrative Comparison** - Comparing Western, Chinese, Russian, Middle Eastern narrative differences
- 📈 **Real-time Market Data** - yfinance integration, covering US/HK/A-shares/Treasury/Commodities
- 📧 **Email Push** - Scheduled tasks + SMTP email delivery

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Proxy software (Clash/v2rayN etc.)

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/prism-lens.git
cd prism-lens

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your configuration
```

### Run

```bash
python run.py
```

---

## 📦 Edition Comparison

| Feature | Free Edition | Premium Edition |
|---------|--------------|-----------------|
| News Sources | 10 | 101+ (continuously updated) |
| Report Chapters | 3 | 10 full analysis |
| Camp Comparison | Single camp | Multi-camp comparison |
| History Lookback | None | 90 days |
| Real-time Alerts | None | S/A level instant push |
| Transmission Chain | None | 9 quantitative chains |
| Market Data | None | Real-time quotes |

### Get Premium Edition

📧 Email: vanthree31@gmail.com
💬 WeChat: Sany1ovo
🎮 HeyBox: [Profile](https://www.xiaoheihe.cn/bbs/user_profile_share?user_id=ad29685205e7&h_src=heyboxapp)
💬 QQ: 1448840796

---

## 📁 Project Structure

```
prism-lens/
├── src/                    # Source code
│   ├── fetcher.py         # RSS news fetcher
│   ├── summarizer.py      # AI three-stage pipeline
│   ├── generator.py       # HTML report generator
│   ├── market_data.py     # Market data fetcher
│   ├── risk_scorer.py     # Risk scoring
│   ├── event_graph.py     # Event graph
│   ├── premium/           # Premium features (paid)
│   └── utils.py           # Utility functions
├── config/                 # Configuration files
│   ├── sources.yaml       # News sources config
│   └── importance_keywords.yaml  # Scoring rules
├── prompts/                # AI prompts
├── output/                 # Generated reports
├── data/                   # Runtime data
└── run.py                  # Entry point
```

---

## ⚙️ Configuration

### Environment Variables (.env)

```env
# AI API Configuration
API_URL=https://api.deepseek.com
API_KEY=your_api_key_here
MODEL_NAME=deepseek-v4-pro

# Proxy Configuration
HTTPS_PROXY=http://127.0.0.1:7890

# Email Push (Optional)
SMTP_ENABLED=false
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=your_email@qq.com
SMTP_PASS=your_smtp_code
SMTP_TO=recipient@example.com
```

### Command Line Arguments

```bash
python run.py [OPTIONS]

--no-open       # Don't auto-open browser
--skip-fetch    # Skip news fetching (use cache)
--skip-ai       # Skip AI generation (use mock data)
--max-news N    # Maximum news count (default 150)
```

---

## 🏗️ Technical Architecture

```
RSS (101+ sources) → fetcher (concurrent) → ImportanceScorer (scoring)
    ↓
summarizer (3-stage AI: extract → cluster → report)
    ↓
generator (HTML slides) → mailer (email push)
    ↓
event_graph + risk_scorer + evolution_tracker
```

---

## 🤝 Contributing

Welcome to contribute news sources! Add to `config/sources.yaml`:

```yaml
- name: "Source Name"
  url: "RSS URL"
  type: rss
  region: "Region"
  media_type: mainstream_media
  narrative_alignment: neutral
  credibility: 7
  signal_weight: 7
  bias_score: 3
  weight: 7
```

See [CONTRIBUTING.md](CONTRIBUTING.md)

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

Premium features (`src/premium/`) are commercially licensed. See [COMMERCIAL.md](COMMERCIAL.md) for details.

---

## Contributors

- PrismLens Team - Creator & Maintainer
- Claude - AI Assistant (Code Review & Architecture)

---

## 📞 Contact

- 📧 Email: vanthree31@gmail.com
- 💬 WeChat: Sany1ovo
- 🎮 HeyBox: [Profile](https://www.xiaoheihe.cn/bbs/user_profile_share?user_id=ad29685205e7&h_src=heyboxapp)
- 💬 QQ: 1448840796
