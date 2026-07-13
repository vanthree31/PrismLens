---
name: no-email-during-test
description: 测试期间绝对禁止发送邮件，SMTP_ENABLED 保持 false，用户明确要求时才开启
metadata:
  type: feedback
---

# 测试期间禁止邮件推送

**Why:** 用户多次强调测试时不要发邮件到 zhoujinhua@ahmu.edu.cn。日报测试应只生成本地 HTML，不触发 SMTP 发送。

**How to apply:**

### .env 配置
- 测试期间 `SMTP_ENABLED=false`（已设置）
- 只有用户明确说"开启邮件推送"/"发到邮箱"时才改回 `true`

### 运行命令
- 测试: `python run.py --no-open`（不带 `--email`）
- 缓存测试: `python run.py --cache --no-open`
- 正式推送: 用户明确指令后才用 `--email`

### 不小心发邮件了怎么办
- 立即道歉，改回 `SMTP_ENABLED=false`
- 反思为什么手滑

### 历史教训
- 2026-07-08: 两次误发邮件到 zhoujinhua@ahmu.edu.cn，用户明确不满
