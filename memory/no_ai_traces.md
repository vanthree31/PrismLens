---
name: no-external-traces
description: 禁止在提交消息、代码注释、文档中包含任何外部工具痕迹 — 最高优先级，推送前强制执行
metadata:
  type: feedback
---

# 禁止外部工具痕迹（最高优先级）

**Why:** 项目为 GitHub 公开仓库 (vanthree31/PrismLens)。git 提交历史永久公开可见，包含外部辅助工具痕迹会暴露开发工具链内部信息，不专业且不必要。

**How to apply:**

### 提交前必检
```bash
git log -1 --format='%b' | grep -qi "noreply" && echo "BLOCKED" || echo "OK"
```

### 推送前必检
```bash
git log --format='%H %s%n%b' origin/main..HEAD | grep -qi "noreply" && echo "BLOCKED" || echo "OK"
```

### 代码内容必检
```bash
grep -ri "noreply@" --include="*.py" --include="*.md" --include="*.txt" --include="*.yaml" --include="*.yml" --include="*.json" . 2>/dev/null && echo "BLOCKED" || echo "OK"
```

### 提交消息格式规范
- ❌ 禁止任何第三方合著标记
- ❌ 禁止 commit body 中出现外部工具品牌名或邮箱域名
- ✅ 仅 `vanthree31 <vanthree31@gmail.com>` 为合法作者

### 代码/文件内容规范
- ❌ 代码注释中不得出现外部工具品牌名
- ❌ README、CHANGELOG、CONTRIBUTING 不得提及具体开发工具
- ❌ 所有 .md/.py/.yaml 文件不得有外部工具相关元数据

### 发现痕迹时的修复方式
1. **单 commit**: `git commit --amend` 修改消息
2. **多 commit 历史**: `git filter-branch` 清理后 force push

### 历史教训
- 2026-07-08: 多个 commit 含第三方合著标记，导致 GitHub Contributors 出现异常
- 修复方式: filter-branch 重写全部 commit + force push

[[project_architecture_v2]]
