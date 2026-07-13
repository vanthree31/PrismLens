# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## 敏感凭据

本项目涉及以下敏感信息，请务必妥善保管：

- `API_KEY` — AI 服务 API 密钥（DeepSeek / OpenAI 等）
- `SMTP_PASS` — 邮件服务授权码
- `REDIS_PASSWORD` — Redis 访问密码
- `HTTPS_PROXY` — 代理服务器地址

这些凭据通过 `.env` 文件加载，**严禁提交到版本控制**。

## Reporting a Vulnerability

如果你发现安全漏洞，请通过以下方式报告：

1. **GitHub Security Advisories**（推荐）：在仓库页面使用 "Security" > "Report a vulnerability" 提交私密报告
2. **GitHub Issues**：对于低敏感度问题，可在 Issues 中报告并添加 `security` 标签

### 报告内容

请尽量包含：

- 漏洞描述及影响范围
- 复现步骤
- 涉及的文件或代码路径
- 建议的修复方案（如有）

### 响应时间

- 确认收到报告：48 小时内
- 初步评估：7 天内
- 修复发布：视严重程度而定

## 安全最佳实践

使用本项目时请注意：

1. **永远不要**将 `.env` 文件提交到 Git 仓库
2. **定期轮换** API 密钥和 SMTP 授权码
3. **使用强密码** 作为 Redis 密码
4. **限制 `.env` 文件权限**：`chmod 600 .env`（Linux/macOS）
5. **不要在日志中输出**敏感凭据
6. **生产环境**建议使用环境变量或密钥管理服务，而非 `.env` 文件
