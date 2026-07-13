# Pipeline Status

```
Status:      PRODUCTION (v3)
Version:     v3 (single-stage)
Frozen:      NO — in release gate observation
v2 Fallback: python run.py --pipeline v2
```

## Release Gate (14-Day Observation)

品质指标当评委。v2 不再是 Gold Standard。

| # | Date | API OK | JSON OK | HTML OK | Quality | Latency CV | Token CV | PASS |
|---|------|--------|---------|---------|---------|------------|----------|------|
| 1 | —    | —      | —       | —       | —       | —          | —        | —    |
| 2 | —    | —      | —       | —       | —       | —          | —        | —    |
| 3 | —    | —      | —       | —       | —       | —          | —        | —    |
| 4 | —    | —      | —       | —       | —       | —          | —        | —    |
| 5 | —    | —      | —       | —       | —       | —          | —        | —    |
| 6 | —    | —      | —       | —       | —       | —          | —        | —    |
| 7 | —    | —      | —       | —       | —       | —          | —        | —    |
| 8 | —    | —      | —       | —       | —       | —          | —        | —    |
| 9 | —    | —      | —       | —       | —       | —          | —        | —    |
| 10| —    | —      | —       | —       | —       | —          | —        | —    |
| 11| —    | —      | —       | —       | —       | —          | —        | —    |
| 12| —    | —      | —       | —       | —       | —          | —        | —    |
| 13| —    | —      | —       | —       | —       | —          | —        | —    |
| 14| —    | —      | —       | —       | —       | —          | —        | —    |

## Freeze Conditions

- [ ] 连续 14 天运行
- [ ] API 成功率 >= 99%
- [ ] JSON 解析成功率 = 100%
- [ ] HTML 生成成功率 = 100%
- [ ] 质量评分均值 >= 85
- [ ] 延迟变异系数 < 30%
- [ ] Token 变异系数 < 30%
- [ ] 零 Pipeline 修改（Prompt / max_tokens / 架构）

All conditions met → Status: FROZEN. Pipeline becomes read-only.
v2 `--pipeline v2` retained as emergency rollback only.

## Daily Metrics

自动写入 `data/runs.db`。查询：
```python
from src.production_metrics import get_recent, release_gate, get_trend
get_trend(14)  # 14天趋势
release_gate(14)  # 检查是否满足冻结条件
```
