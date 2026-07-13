"""
工具模块：日志配置、路径管理、通用辅助函数
"""

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

# ─────────────────────────────────────────────────
# 项目根目录
# ─────────────────────────────────────────────────

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def get_config_dir() -> Path:
    """获取配置目录路径"""
    return PROJECT_ROOT / "config"


def get_output_dir() -> Path:
    """获取输出目录路径"""
    return PROJECT_ROOT / "output"


def get_cache_dir() -> Path:
    """获取缓存目录路径"""
    return PROJECT_ROOT / "cache"


def get_data_dir() -> Path:
    """获取数据目录路径"""
    return PROJECT_ROOT / "data"


def get_history_dir() -> Path:
    """获取历史数据目录路径"""
    return get_data_dir() / "history"


def get_logs_dir() -> Path:
    """获取日志目录路径"""
    return PROJECT_ROOT / "logs"


def get_prompts_dir() -> Path:
    """获取提示词目录路径"""
    return PROJECT_ROOT / "prompts"


def get_templates_dir() -> Path:
    """获取模板目录路径"""
    return PROJECT_ROOT / "templates"


def get_runtime_dir() -> Path:
    """获取运行时状态目录路径"""
    return get_data_dir() / "runtime"


def get_metrics_dir() -> Path:
    """获取可观测性指标目录路径"""
    return get_data_dir() / "metrics"


# ─────────────────────────────────────────────────
# 目录初始化
# ─────────────────────────────────────────────────


def ensure_directories() -> None:
    """确保所有必需目录存在"""
    for dir_func in [
        get_output_dir,
        get_cache_dir,
        get_data_dir,
        get_history_dir,
        get_logs_dir,
        get_runtime_dir,
        get_metrics_dir,
    ]:
        dir_func().mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────────


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    配置全局日志系统

    输出到:
    - 控制台 (带颜色)
    - 日志文件 (按日期命名)
    """
    # 确保日志目录存在
    log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    # 日志文件名
    log_file = log_dir / f"briefing_{datetime.now().strftime('%Y%m%d')}.log"

    # 创建 logger
    logger = logging.getLogger("global_news")
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # 日志格式
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台 handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 handler (RotatingFileHandler, 最大 5MB, 保留 3 个备份)
    file_handler = RotatingFileHandler(
        str(log_file), encoding="utf-8", mode="a", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# ─────────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────────


def load_yaml_config(filename: str) -> dict:
    """
    加载 YAML 配置文件

    免费版优先加载 _free 后缀配置（如 sources_free.yaml），
    不存在时回退到完整版。
    付费版直接加载完整版。

    Args:
        filename: 配置文件名 (不含路径)

    Returns:
        解析后的字典
    """
    try:
        from src.premium import is_premium_enabled
    except ImportError:

        def is_premium_enabled() -> bool:
            return False

    config_dir = get_config_dir()
    name, ext = filename.rsplit(".", 1)
    free_filename = f"{name}_free.{ext}"
    free_path = config_dir / free_filename
    full_path = config_dir / filename

    if not is_premium_enabled() and free_path.exists():
        config_path = free_path
    elif full_path.exists():
        config_path = full_path
    elif free_path.exists():
        config_path = free_path
    else:
        raise FileNotFoundError(
            f"配置文件不存在: {full_path.name}。请确保 config/ 目录下存在该文件，或检查文件名是否正确。"
        )

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 语法错误 ({config_path.name}): {e}") from e
    if config is None:
        return {}
    return config


def load_sources_config() -> list[dict]:
    """加载新闻源配置"""
    config = load_yaml_config("sources.yaml")
    return config.get("sources", [])


def load_importance_config() -> dict:
    """加载重要性评分配置"""
    return load_yaml_config("importance_keywords.yaml")


def load_event_aliases_config() -> dict:
    """加载事件别名配置"""
    return load_yaml_config("event_aliases.yaml")


def load_entities_config() -> dict:
    """加载实体注册表"""
    return load_yaml_config("entities.yaml")


def load_actions_config() -> dict:
    """加载动作词表"""
    return load_yaml_config("actions.yaml")


def load_topics_config() -> dict:
    """加载主题词表"""
    return load_yaml_config("topics.yaml")


def load_phase_transitions_config() -> dict:
    """加载阶段转换规则"""
    return load_yaml_config("phase_transitions.yaml")


def load_prompt_template(lang: str = "zh") -> str:
    """加载 AI 提示词模板（优先完整版，回退免费版）"""
    if lang == "en":
        filename = "summary_prompt_en.txt"
        free_filename = "summary_prompt_free_en.txt"
    else:
        filename = "summary_prompt.txt"
        free_filename = "summary_prompt_free.txt"

    prompt_path = get_prompts_dir() / filename
    if not prompt_path.exists():
        # 回退到免费版
        free_path = get_prompts_dir() / free_filename
        if free_path.exists():
            prompt_path = free_path
        else:
            raise FileNotFoundError(f"提示词模板不存在: {prompt_path}")

    with open(prompt_path, encoding="utf-8") as f:
        return f.read()


# ─────────────────────────────────────────────────
# 环境变量加载
# ─────────────────────────────────────────────────


def load_env() -> dict[str, str]:
    """
    从 .env 文件加载环境变量

    Returns:
        环境变量字典
    """
    from dotenv import load_dotenv

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise FileNotFoundError(".env 文件不存在，请复制 .env.example 并填写配置")

    load_dotenv(env_path, override=True)

    api_key = os.getenv("API_KEY", "")
    if api_key == "your_api_key_here" or not api_key:
        raise ValueError("API_KEY 未配置或仍为占位符值，请在 .env 文件中填写真实的 API 密钥")

    try:
        smtp_port = int(os.getenv("SMTP_PORT", "465"))
    except ValueError:
        smtp_port = 465
        logging.getLogger("global_news").warning(
            "SMTP_PORT 环境变量值无效 (应为 1-65535 的整数)，使用默认端口 465"
        )

    try:
        market_data_timeout = int(os.getenv("MARKET_DATA_TIMEOUT", "30"))
    except ValueError:
        market_data_timeout = 30

    try:
        market_data_retry_count = int(os.getenv("MARKET_DATA_RETRY_COUNT", "2"))
    except ValueError:
        market_data_retry_count = 2

    return {
        "api_url": os.getenv("API_URL", "https://api.deepseek.com/v1/chat/completions"),
        "api_key": api_key,
        "model_name": os.getenv("MODEL_NAME", "deepseek-chat"),
        "https_proxy": os.getenv("HTTPS_PROXY", ""),
        "ssl_verify": os.getenv("SSL_VERIFY", "true").lower() == "true",
        "smtp_enabled": os.getenv("SMTP_ENABLED", "false").lower() == "true",
        "smtp_host": os.getenv("SMTP_HOST", "smtp.qq.com"),
        "smtp_port": smtp_port,
        "smtp_user": os.getenv("SMTP_USER", ""),
        "smtp_pass": os.getenv("SMTP_PASS", ""),
        "smtp_to": os.getenv("SMTP_TO", ""),
        "market_data_cache_ttl": int(os.getenv("MARKET_DATA_CACHE_TTL", "30")),
        "market_data_timeout": market_data_timeout,
        "market_data_retry_count": market_data_retry_count,
    }


# ─────────────────────────────────────────────────
# 日期工具
# ─────────────────────────────────────────────────


def get_today_str() -> str:
    """获取今日日期字符串 YYYY-MM-DD"""
    return datetime.now().strftime("%Y-%m-%d")


def get_today_filename(
    prefix: str = "每日简报", ext: str = "html", output_dir: str = "", lang: str = "zh"
) -> str:
    """生成按日期命名的文件名，如已存在则自动追加编号（含微秒戳防竞态）"""
    if lang == "en" and prefix == "每日简报":
        prefix = "DailyBriefing"
    base = f"{prefix}-{get_today_str()}"
    name = f"{base}.{ext}"
    if output_dir:
        n = 2
        suffix = "-第{n}次" if lang == "zh" else "-{n}"
        while os.path.exists(os.path.join(output_dir, name)):
            name = f"{base}{suffix.format(n=n)}.{ext}"
            n += 1
        # 防 TOCTOU 竞态：追加微秒级时间戳确保唯一性
        if n > 2:
            us = datetime.now().strftime("%f")[:3]
            name_parts = name.rsplit(".", 1)
            name = f"{name_parts[0]}-{us}.{name_parts[1]}"
    return name


# ─────────────────────────────────────────────────
# URL 去重工具
# ─────────────────────────────────────────────────


def normalize_url(url: str) -> str:
    """
    标准化 URL 用于去重
    去除尾部斜杠、UTM 参数、查询参数等差异
    """
    from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

    parsed = urlparse(url.strip())
    # 去除尾部斜杠
    path = parsed.path.rstrip("/")

    # 过滤 UTM 和追踪参数，保留功能性参数
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        filtered = {
            k: v
            for k, v in params.items()
            if not k.lower().startswith(("utm_", "ceid", "hl", "gl", "fbclid", "gclid"))
        }
        query = urlencode(filtered, doseq=True) if filtered else ""
    else:
        query = ""

    normalized = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",  # params
            query,
            "",  # fragment
        )
    )
    # 移除仅含追踪参数时残留的 '?'
    if normalized.endswith("?"):
        normalized = normalized[:-1]
    return normalized


# ─────────────────────────────────────────────────
# 运行状态管理 (State Layer)
# ─────────────────────────────────────────────────


class RunState:
    """
    统一运行状态管理器

    跟踪每次 pipeline 运行的步骤状态，支持断点恢复和调试。
    状态文件: data/runtime/current_run.json
    """

    def __init__(self):
        self.state_file = get_runtime_dir() / "current_run.json"
        self.run_id = datetime.now().isoformat()
        self.data = {
            "run_id": self.run_id,
            "status": "running",
            "completed_steps": [],
            "failed_steps": [],
            "skipped_steps": [],
            "started_at": self.run_id,
            "updated_at": self.run_id,
            "metrics": {},
        }
        self._save()

    def _save(self):
        """原子写入状态文件"""
        self.data["updated_at"] = datetime.now().isoformat()
        tmp = self.state_file.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp), str(self.state_file))
        except Exception as e:
            logging.getLogger("global_news").warning("运行状态保存失败: %s", e)

    def mark_step(self, step: str, status: str, detail: str = ""):
        """标记步骤状态: completed / failed / skipped"""
        valid_statuses = {"completed", "failed", "skipped"}
        if status not in valid_statuses:
            logging.getLogger("global_news").warning(
                "步骤 '%s' 的状态值 '%s' 无效 (应为 completed/failed/skipped)，已忽略", step, status
            )
            return

        entry = {"step": step, "status": status, "time": datetime.now().isoformat()}
        if detail:
            entry["detail"] = detail

        if status == "completed":
            self.data["completed_steps"].append(entry)
        elif status == "failed":
            self.data["failed_steps"].append(entry)
        elif status == "skipped":
            self.data["skipped_steps"].append(entry)
        self._save()

    def finish(self, final_status: str = "completed"):
        """标记运行结束"""
        self.data["status"] = final_status
        self._save()

    def is_step_done(self, step: str) -> bool:
        """检查某步骤是否已完成"""
        return any(e["step"] == step for e in self.data["completed_steps"])

    def get_last_run_state(self) -> dict | None:
        """获取上次运行状态（用于断点恢复判断）"""
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logging.getLogger("global_news").warning(
                    "运行状态文件读取失败 (%s): %s", self.state_file.name, e
                )
                return None
        return None


# ─────────────────────────────────────────────────
# 可观测性指标 (Observability)
# ─────────────────────────────────────────────────


class MetricsCollector:
    """
    运行指标收集器

    记录 fetch、AI、生成等各阶段的性能和质量指标。
    指标文件: data/metrics/metrics_YYYY-MM-DD.json
    """

    def __init__(self):
        self.metrics_dir = get_metrics_dir()
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.data = {
            "date": get_today_str(),
            "timestamp": datetime.now().isoformat(),
            "fetch": {
                "total_sources": 0,
                "success_sources": 0,
                "failed_sources": 0,
                "total_news": 0,
                "duplicate_count": 0,
                "repeat_count": 0,
                "fetch_time_seconds": 0,
                "failed_source_names": [],
            },
            "ai": {
                "summary_tokens": 0,
                "summary_latency_seconds": 0,
                "extraction_tokens": 0,
                "extraction_latency_seconds": 0,
                "extraction_success": False,
            },
            "output": {
                "html_size_bytes": 0,
                "events_count": 0,
                "risk_themes_count": 0,
                "evolution_active_events": 0,
                "phase_transitions": 0,
            },
            "pipeline": {
                "total_time_seconds": 0,
                "steps_completed": 0,
                "steps_failed": 0,
                "steps_skipped": 0,
            },
        }

    def record_fetch(
        self,
        total_sources: int,
        success: int,
        failed: int,
        total_news: int,
        duplicates: int,
        repeats: int,
        elapsed: float,
        failed_names: list[str],
    ):
        """记录抓取指标"""
        self.data["fetch"].update(
            {
                "total_sources": total_sources,
                "success_sources": success,
                "failed_sources": failed,
                "total_news": total_news,
                "duplicate_count": duplicates,
                "repeat_count": repeats,
                "fetch_time_seconds": round(elapsed, 1),
                "failed_source_names": failed_names,
            }
        )

    def record_ai_summary(self, tokens: int, elapsed: float):
        """记录 AI 摘要指标"""
        self.data["ai"]["summary_tokens"] = tokens
        self.data["ai"]["summary_latency_seconds"] = round(elapsed, 1)

    def record_ai_extraction(self, tokens: int, elapsed: float, success: bool):
        """记录 AI 结构化提取指标"""
        self.data["ai"]["extraction_tokens"] = tokens
        self.data["ai"]["extraction_latency_seconds"] = round(elapsed, 1)
        self.data["ai"]["extraction_success"] = success

    def record_output(
        self,
        html_size: int = 0,
        events: int = 0,
        risk_themes: int = 0,
        evolution_active: int = 0,
        phase_transitions: int = 0,
    ):
        """记录输出指标"""
        self.data["output"].update(
            {
                "html_size_bytes": html_size,
                "events_count": events,
                "risk_themes_count": risk_themes,
                "evolution_active_events": evolution_active,
                "phase_transitions": phase_transitions,
            }
        )

    def record_pipeline(self, total_time: float, completed: int, failed: int, skipped: int):
        """记录 pipeline 整体指标"""
        self.data["pipeline"].update(
            {
                "total_time_seconds": round(total_time, 1),
                "steps_completed": completed,
                "steps_failed": failed,
                "steps_skipped": skipped,
            }
        )

    def save(self):
        """保存指标到文件"""
        self.data["timestamp"] = datetime.now().isoformat()
        metrics_file = self.metrics_dir / f"metrics_{get_today_str()}.json"
        try:
            with open(metrics_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.getLogger("global_news").warning(
                "指标文件保存失败 (%s): %s", metrics_file.name, e
            )
