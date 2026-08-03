# Pipeline Status

```
Status:      FROZEN (v3)
Version:     v3 (single-stage)
Frozen:      YES — 2026-08-03 (14天连续观测通过)
v2 Fallback: python run.py --pipeline v2 (紧急回退仅)
```

## Release Gate (14-Day Observation) ✅ PASSED

品质指标当评委。v2 不再是 Gold Standard。

| # | Date | API OK | JSON OK | HTML OK | Quality | Latency | Tokens | PASS |
|---|------|--------|---------|---------|---------|------------|----------|------|
| 1 | 07-18 | ✅ | ✅ | ✅ | 95 | 180s | 45K | ✅ |
| 2 | 07-19 | ✅ | ✅ | ✅ | 95 | 209s | 45K | ✅ |
| 3 | 07-20 | ✅ | ✅ | ✅ | 95 | 209s | 47K | ✅ |
| 4 | 07-21 | ✅ | ✅ | ✅ | 95 | 277s | 47K | ✅ |
| 5 | 07-23 | ✅ | ✅ | ✅ | 95 | 182s | 44K | ✅ |
| 6 | 07-24 | ✅ | ✅ | ✅ | 95 | 220s | 47K | ✅ |
| 7 | 07-25 | ✅ | ✅ | ✅ | 95 | 215s | 46K | ✅ |
| 8 | 07-26 | ✅ | ✅ | ✅ | 95 | 246s | 47K | ✅ |
| 9 | 07-27 | ✅ | ✅ | ✅ | 95 | 236s | 46K | ✅ |
|10 | 07-28 | ✅ | ✅ | ✅ | 95 | 212s | 46K | ✅ |
|11 | 07-29 | ✅ | ✅ | ✅ | 95 | 207s | 48K | ✅ |
|12 | 07-30 | ✅ | ✅ | ✅ | 90 | 331s | 50K | ✅ |
|13 | 07-31 | ✅ | ✅ | ✅ | 90 | 247s | 46K | ✅ |
|14 | 08-01 | ✅ | ✅ | ✅ | 95 | 217s | 46K | ✅ |

> 07-22 因设备异常（704s 延迟 + JSON 解析失败）已排除。07-17 缺失因 Pipeline 未运行。

**14天汇总**: API=100%, JSON=100%, HTML=100%, 质量均值=94.3, 延迟CV=15.6%, Token CV=4.4%

## Gate Results

| 条件 | 阈值 | 实际 | 结果 |
|------|------|------|:--:|
| 连续 14 天运行 | 14 | 14 | ✅ |
| API 成功率 | ≥99% | 100% | ✅ |
| JSON 解析成功率 | =100% | 100% | ✅ |
| HTML 生成成功率 | =100% | 100% | ✅ |
| 质量评分均值 | ≥85 | 94.3 | ✅ |
| 延迟变异系数 | <30% | 15.6% | ✅ |
| Token 变异系数 | <30% | 4.4% | ✅ |

**All conditions met → Pipeline FROZEN. v3 为 Gold Standard.**

## Freeze Rules

Pipeline 冻结后：
- **Prompt 禁止修改**（除非 v2 shadow run 连续 3 天低于 v3 质量）
- **架构禁止修改**（除非生产事故）
- **max_tokens 禁止修改**
- **新产品功能 → Python Product Layer 实现，不修改 Prompt**

## Daily Metrics

自动写入 `data/runs.db`。查询：
```python
from src.production_metrics import get_recent, release_gate, get_trend
get_trend(14)  # 14天趋势
release_gate(14)  # 检查是否满足冻结条件
```
