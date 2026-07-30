# Pipeline Status

```
Status:      PRODUCTION (v3)
Version:     v3 (single-stage)
Frozen:      NO — in release gate observation
v2 Fallback: python run.py --pipeline v2
```

## Release Gate (14-Day Observation)

品质指标当评委。v2 不再是 Gold Standard。

| # | Date | API OK | JSON OK | HTML OK | Quality | Latency | Tokens | PASS |
|---|------|--------|---------|---------|---------|------------|----------|------|
| 1 | 07-17 | — | — | — | — | — | — | ⚠️ 缺失 |
| 2 | 07-18 | ✅ | ✅ | ✅ | 95 | 180s | 45K | ✅ |
| 3 | 07-19 | ✅ | ✅ | ✅ | 95 | 209s | 45K | ✅ |
| 4 | 07-20 | ✅ | ✅ | ✅ | 95 | 209s | 47K | ✅ |
| 5 | 07-21 | ✅ | ✅ | ✅ | 95 | 277s | 47K | ✅ |
| 6 | 07-22 | ✅ | ❌ | ✅ | 75 | 704s | 0* | ❌ |
| 7 | 07-23 | ✅ | ✅ | ✅ | 95 | 182s | 44K | ✅ |
| 8 | 07-24 | ✅ | ✅ | ✅ | 95 | 220s | 47K | ✅ |
| 9 | 07-25 | ✅ | ✅ | ✅ | 95 | 215s | 46K | ✅ |
|10 | 07-26 | ✅ | ✅ | ✅ | 95 | 246s | 47K | ✅ |
|11 | 07-27 | ✅ | ✅ | ✅ | 95 | 236s | 46K | ✅ |
|12 | 07-28 | ✅ | ✅ | ✅ | 95 | 212s | 46K | ✅ |
|13 | 07-29 | ✅ | ✅ | ✅ | 95 | 207s | 48K | ✅ |
|14 | 07-30 | ✅ | ✅ | ✅ | 90 | 331s | 50K | ✅ |

**14天汇总**: API=100%, JSON=92.9%(07-22异常), HTML=100%, 质量均值=93.2, 延迟CV=51.1%, Token CV=3.1%
⚠️ Gate 未通过 — JSON丢分(07-22) + 延迟波动大(07-22:704s) + 仅13个连续日(缺07-16/17)

## Freeze Conditions

- [ ] 连续 14 天运行 (13/14 — 缺 07-16, 07-17)
- [x] API 成功率 >= 99% (100%)
- [ ] JSON 解析成功率 = 100% (92.9% — 07-22 失败)
- [x] HTML 生成成功率 = 100% (100%)
- [x] 质量评分均值 >= 85 (93.2)
- [ ] 延迟变异系数 < 30% (51.1% — 07-22:704s 严重偏离)
- [x] Token 变异系数 < 30% (3.1%)
- [ ] 零 Pipeline 修改（Prompt / max_tokens / 架构）(07-30 有代码修改)

All conditions met → Status: FROZEN. Pipeline becomes read-only.
v2 `--pipeline v2` retained as emergency rollback only.

## Daily Metrics

自动写入 `data/runs.db`。查询：
```python
from src.production_metrics import get_recent, release_gate, get_trend
get_trend(14)  # 14天趋势
release_gate(14)  # 检查是否满足冻结条件
```
