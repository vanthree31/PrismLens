---
name: premium-only-focus
description: 所有开发、测试、日报生成只使用完整付费版配置，不考虑免费版
metadata:
  type: feedback
---

# 只关注完整付费版

**Why:** 免费版只是 marketing 手段，完整付费版才是实际产品。开发精力应 100% 集中在付费版质量和体验上。

**How to apply:**

### 每日日报生成
- 始终使用完整版 sources.yaml（101+源）、importance_keywords.yaml、summary_prompt.txt
- 始终启用 PRISM_LENS_PREMIUM=true
- 始终使用 10 章完整日报结构
- 所有 prompt 改进只修改完整版 prompt 文件

### 开发优先级
- 付费版功能优先：多阵营对比、9条传导链、90天历史、实时预警
- 免费版仅做最小维护（不崩溃即可）
- 数据源配置只维护 sources.yaml，不维护 sources_free.yaml
- Prompt 只维护 summary_prompt.txt，不维护 summary_prompt_free.txt

### 测试
- 运行日报生成时始终不加 --skip-fetch（除非测试 HTML/CSS）
- 验证日报质量时以 10 章完整版为标准
- 不测试免费版 3 章模式

### 例外
- 推送到 GitHub 的代码保持免费版可用（避免 CI 崩溃）
- GitHub 仓库的 README 和文档可以有免费版描述
