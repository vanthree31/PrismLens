# PrismLens

<p align="right">
  <b>English</b> | <a href="README.md">中文</a>
</p>

> **Every morning, understand the world in five minutes.**

PrismLens transforms 100+ global news sources into a professional geopolitical intelligence briefing, delivered to your inbox daily.

---

## Why not just use ChatGPT?

| | ChatGPT | RSS Readers | PrismLens |
|---|:---:|:---:|:---:|
| **Auto-delivery** | ✗ Must ask | ✓ But you filter | ✓ Arrives daily |
| **Long-term memory** | ✗ Stateless | ✗ | ✓ 90-day event tracking |
| **Multi-source synthesis** | ✗ Single query | ✗ One at a time | ✓ 100+ sources analyzed together |
| **Narrative comparison** | ✗ Must prompt | ✗ | ✓ Multi-perspective by default |
| **Risk transmission** | ✗ DIY analysis | ✗ | ✓ Automatic financial impact chains |
| **Historical trends** | ✗ | ✗ | ✓ Full event lifecycle |

ChatGPT waits for your questions. PrismLens comes to you every morning.

---

## What you receive

A complete 10-chapter intelligence briefing, delivered automatically.

```
═══════════════════════════════════════
  Core Judgment
  Today's key changes in 30 seconds
───────────────────────────────────
  Risk Matrix
  Probability × Impact × Transmission chains
───────────────────────────────────
  Multi-Perspective Analysis
  CNN, BBC, RT, Xinhua, Al Jazeera...
  Same event, different lenses
───────────────────────────────────
  Financial Layer
  Market data + Equities + Commodities
───────────────────────────────────
  Tech Competition
  Semiconductors, AI chips, Export controls
───────────────────────────────────
  Event Clusters & Long-Term Trends
  30-90 day outlook with probability scenarios
───────────────────────────────────
  Investment Assessment + Resource Security
───────────────────────────────────
  Source Links (all traceable)
═══════════════════════════════════════
```

**[View today's report →](output/)**

---

## What makes it different

### Multi-Perspective Intelligence

One event, five narratives.

What does CNN report? What does RT say? Xinhua? Al Jazeera?

You don't just learn what happened — you see how each side frames it.

### Event Memory

The AI doesn't just summarize today.

It remembers yesterday. Last week. The 90-day trend.

Today's US-Iran escalation isn't "a news story." It's Day 47 of an event. The AI tracks every phase change from Day 1.

### Risk Transmission Chains

Not "oil prices went up." This:

```
Middle East escalation
    │
    ▼
Strait of Hormuz disrupted
    │
    ▼
Oil ↑ → Inflation expectations ↑ → Bond yields ↑
    │
    ▼
Tech valuations pressured → NASDAQ ↓
    │
    ▼
Capital flows to safe havens → Gold ↑
```

This is intelligence analysis, not news aggregation.

### Explains Why, Not Just What

Not "US announced new China tariffs."

"4th escalation in 90 days. Scope expanded from chips to AI infrastructure. Potential impact: NVIDIA China Q3 revenue estimate cut 15-20%, Korean HBM supply chain indirectly exposed."

---

## Architecture

```
100+ RSS Sources (parallel fetch)
        │
        ▼
  Context Builder
  News + Yesterday's events + Markets + 90-day trends
        │
        ▼
  1M Context LLM (single pass)
        │
        ▼
  Structured Intelligence
        │
   ┌────┼────┬──────────┐
   ▼    ▼    ▼          ▼
 Report JSON Email  Telegram
```

- **Single-pass 1M context pipeline** — AI sees everything at once, no fragmented stages
- **Event Knowledge Graph** — Cross-day event tracking with automatic dedup and merging
- **Layered Prompt System** — 6 modular layers, single version maintained
- **AI Memory** — Event Database as long-term memory for the AI
- **Risk Propagation Engine** — 9 quantified transmission chains

---

## Quick Start

```bash
git clone https://github.com/vanthree31/PrismLens.git
cd PrismLens
pip install -r requirements.txt
cp .env.example .env   # Add your DeepSeek API key
python run.py           # Generate today's briefing
```

**Requirements:** Python 3.10+ · DeepSeek API key (or any OpenAI-compatible API)

---

## Pricing

| | Free | Pro |
|---|:---:|:---:|
| **Briefing** | 3 chapters | 10 chapters |
| **Sources** | Curated | 100+ |
| **Narrative comparison** | — | Multi-perspective |
| **Memory** | — | 90 days |
| **Transmission chains** | — | 9 quantified |
| **Alerts** | — | S/A level events |

**Pro license:** vanthree31@gmail.com

---

## License

Open Core — MIT for the engine. Pro features require a commercial license.

[LICENSE](LICENSE)
