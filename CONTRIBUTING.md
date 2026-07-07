# Contributing to Global News Briefing

感谢您对 PrismLens Global Intelligence 项目的关注！

## 开发环境搭建

### 1. 克隆仓库

```bash
git clone https://github.com/yourusername/global-news-briefing.git
cd global-news-briefing
```

### 2. 创建虚拟环境

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 开发依赖（pytest, ruff等）
```

### 4. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填写您的配置
```

## 代码规范

### Lint & Format

项目使用 [Ruff](https://github.com/astral-sh/ruff) 进行代码检查和格式化：

```bash
# 检查
ruff check src/ run.py

# 自动修复
ruff check --fix src/ run.py

# 格式化
ruff format src/ run.py
```

### 提交规范

使用语义化提交信息：

- `feat:` 新功能
- `fix:` Bug修复
- `docs:` 文档更新
- `style:` 代码格式（不影响功能）
- `refactor:` 重构
- `test:` 测试相关
- `chore:` 构建/工具相关

示例：
```
feat: 添加Telegram推送渠道
fix: 修复Google News代理源503错误
docs: 更新README安装说明
```

## 提交Pull Request

1. Fork 项目
2. 创建功能分支：`git checkout -b feat/your-feature`
3. 提交更改：`git commit -m 'feat: 描述你的改动'`
4. 推送分支：`git push origin feat/your-feature`
5. 创建 Pull Request

### PR检查清单

- [ ] 代码通过 `ruff check`
- [ ] 代码通过 `ruff format --check`
- [ ] 测试通过 `pytest tests/`
- [ ] 更新相关文档
- [ ] 不包含敏感信息（API Key、密码等）

## 报告Issue

使用 GitHub Issues 报告问题，请包含：

1. 问题描述
2. 复现步骤
3. 期望行为
4. 实际行为
5. 环境信息（OS、Python版本等）

## 贡献新闻源

欢迎贡献新的新闻源！请在 `config/sources.yaml` 中添加：

```yaml
- name: "源名称"
  url: "RSS URL"
  type: rss
  region: "区域"
  media_type: mainstream_media
  narrative_alignment: neutral
  credibility: 7
  signal_weight: 7
  bias_score: 3
  weight: 7
```

## 许可证

提交贡献即表示您同意您的代码在 [MIT License](LICENSE) 下发布。
