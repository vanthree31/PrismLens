---
name: pre-push-checklist
description: 每次 git push 前必须执行的三项检查，确保 CI 一次通过
metadata:
  type: feedback
---

# 推送前检查清单（强制执行）

**Why:** 多次推送后才修复格式问题，浪费 CI 资源和时间。必须是推送前完成，不是推送后补救。

**How to apply:** 每次 `git push` 前，必须依次执行以下检查，全部通过后才能推送：

### 1. 格式检查
```bash
python -m ruff format --check src/ run.py
```

### 2. Lint 检查
```bash
python -m ruff check src/ run.py
```

### 3. 测试
```bash
python -m pytest tests/ -q
```

### 4. 痕迹检查
```bash
git diff --cached | grep -i "noreply@"
# 必须无任何输出
```

### 5. 提交消息检查
```bash
git log -1 --format='%s%n%b' | grep -i "noreply@"
# 必须无任何输出
```

**任何一项未通过 → 修复 → 重新检查 → 全部通过后才允许 push。**

**历史教训：**
- 2026-07-08: 两次推送后 CI Lint 失败（格式未检查就推送），用户明确不满
