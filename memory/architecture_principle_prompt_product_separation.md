---
name: architecture-principle-prompt-product-separation
description: LLM与产品层分离的铁律 — Prompt永远生成完整版，所有付费分层在Python后处理
metadata:
  type: project
  status: hard_constraint
---

# PrismLens Architecture Principle (HARD CONSTRAINT)

> **LLM 永远只负责生成最高质量的完整 Intelligence Report。所有产品分层（Free / Pro / Enterprise）全部在 Python Product Layer 实现。任何 Premium / Free / Enterprise 逻辑不得进入 Prompt。**

**Why:** 
- Prompt 永远只有一套（zh/en），不按订阅层分裂（避免 zh_free/zh_pro/en_free/en_pro 组合爆炸）
- 免费用户得到"更少的分析"而非"更差的分析"
- 改动付费策略只需改 Python，不重测 Prompt / Benchmark / A/B
- Benchmark 永远比较完整输出，一致性强
- AI 成本不会增加（字符串切片 vs 维护多套 Prompt）

**How to apply:**
- ❌ 不要创建 `summary_prompt_free.txt` 的新版本
- ❌ 不要在 Prompt 里写"如果是免费版，只输出3章"
- ❌ 不要在 Prompt Layer 中根据 `is_premium` 切换内容
- ❌ 不要为不同订阅层创建不同的 Prompt 变体
- ✅ 所有产品差异化都在 `src/premium/` 和 Python 后处理中实现

**Architecture:**
```
           LLM
            │
            ▼
    完整 Intelligence Report
    (100% 内容 + 全部 JSON)
            │
            ▼
     Python Product Layer
            │
    ┌───────┼────────┐
    │       │        │
    ▼       ▼        ▼
 Free     Pro   Enterprise
```

**What Goes Where:**
- Prompt: 分析方式、写作规范、结构组织、推理链、阵营比较、引用格式、JSON Schema、风险评估、总结能力
- Python: 章节裁剪、源数量限制、历史天数、导出格式、邮件推送、实时预警、API、Dashboard、Team、Token配额、Logo/Watermark
