"""
工具函数高级测试
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

from src.utils import (
    PROJECT_ROOT,
    get_config_dir,
    get_output_dir,
    get_cache_dir,
    get_data_dir,
    get_history_dir,
    get_logs_dir,
    get_prompts_dir,
    get_templates_dir,
    get_runtime_dir,
    get_metrics_dir,
    get_today_str,
    get_today_filename,
    normalize_url,
)


class TestDirectoryPaths:
    """目录路径函数测试"""

    def test_project_root_is_path(self):
        """测试：PROJECT_ROOT 是 Path 对象"""
        assert isinstance(PROJECT_ROOT, Path)

    def test_project_root_exists(self):
        """测试：PROJECT_ROOT 存在"""
        assert PROJECT_ROOT.exists()

    def test_config_dir_returns_path(self):
        """测试：get_config_dir 返回 Path"""
        result = get_config_dir()
        assert isinstance(result, Path)

    def test_config_dir_ends_with_config(self):
        """测试：config 目录路径以 'config' 结尾"""
        result = get_config_dir()
        assert result.name == "config"

    def test_output_dir(self):
        """测试：output 目录路径正确"""
        result = get_output_dir()
        assert result.name == "output"
        assert result.parent == PROJECT_ROOT

    def test_cache_dir(self):
        """测试：cache 目录路径正确"""
        result = get_cache_dir()
        assert result.name == "cache"
        assert result.parent == PROJECT_ROOT

    def test_data_dir(self):
        """测试：data 目录路径正确"""
        result = get_data_dir()
        assert result.name == "data"
        assert result.parent == PROJECT_ROOT

    def test_history_dir(self):
        """测试：history 目录路径正确"""
        result = get_history_dir()
        assert result.name == "history"
        assert result.parent == get_data_dir()

    def test_logs_dir(self):
        """测试：logs 目录路径正确"""
        result = get_logs_dir()
        assert result.name == "logs"
        assert result.parent == PROJECT_ROOT

    def test_prompts_dir(self):
        """测试：prompts 目录路径正确"""
        result = get_prompts_dir()
        assert result.name == "prompts"
        assert result.parent == PROJECT_ROOT

    def test_templates_dir(self):
        """测试：templates 目录路径正确"""
        result = get_templates_dir()
        assert result.name == "templates"
        assert result.parent == PROJECT_ROOT

    def test_runtime_dir(self):
        """测试：runtime 目录路径正确"""
        result = get_runtime_dir()
        assert result.name == "runtime"
        assert result.parent == get_data_dir()

    def test_metrics_dir(self):
        """测试：metrics 目录路径正确"""
        result = get_metrics_dir()
        assert result.name == "metrics"
        assert result.parent == get_data_dir()


class TestDateFunctions:
    """日期工具函数测试"""

    def test_get_today_str_format(self):
        """测试：日期格式为 YYYY-MM-DD"""
        result = get_today_str()
        # 验证格式
        datetime.strptime(result, "%Y-%m-%d")

    def test_get_today_str_matches_today(self):
        """测试：日期匹配今天"""
        result = get_today_str()
        expected = datetime.now().strftime("%Y-%m-%d")
        assert result == expected

    def test_get_today_filename_default(self):
        """测试：默认文件名格式"""
        result = get_today_filename()
        today = get_today_str()
        assert today in result
        assert result.endswith(".html")

    def test_get_today_filename_custom_prefix(self):
        """测试：自定义前缀"""
        result = get_today_filename(prefix="测试报告")
        assert result.startswith("测试报告")

    def test_get_today_filename_custom_ext(self):
        """测试：自定义扩展名"""
        result = get_today_filename(ext="pdf")
        assert result.endswith(".pdf")

    def test_get_today_filename_with_output_dir(self):
        """测试：指定输出目录时的去重逻辑"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # 第一次创建
            name1 = get_today_filename(output_dir=tmpdir)
            # 手动创建同名文件
            (Path(tmpdir) / name1).touch()
            # 第二次应该追加 -第2次
            name2 = get_today_filename(output_dir=tmpdir)
            assert "第2次" in name2


class TestNormalizeUrl:
    """URL 标准化函数测试"""

    def test_remove_trailing_slash(self):
        """测试：去除尾部斜杠"""
        result = normalize_url("https://example.com/path/")
        assert result == "https://example.com/path"

    def test_remove_utm_params(self):
        """测试：去除 UTM 参数"""
        url = "https://example.com/page?utm_source=google&utm_medium=cpc&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=123" in result

    def test_remove_fbclid(self):
        """测试：去除 fbclid 参数"""
        url = "https://example.com/page?fbclid=abc123&id=1"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "id=1" in result

    def test_preserve_non_tracking_params(self):
        """测试：保留非追踪参数"""
        url = "https://example.com/search?q=test&page=2"
        result = normalize_url(url)
        assert "q=test" in result
        assert "page=2" in result

    def test_empty_query_string(self):
        """测试：无查询参数"""
        result = normalize_url("https://example.com/path")
        assert "?" not in result

    def test_strip_whitespace(self):
        """测试：去除 URL 两侧空白"""
        result = normalize_url("  https://example.com  ")
        assert result == "https://example.com"

    def test_remove_fragment(self):
        """测试：去除 fragment"""
        result = normalize_url("https://example.com/page#section1")
        assert "#section1" not in result

    def test_normalize_same_url(self):
        """测试：相同 URL 标准化后一致"""
        url1 = "https://example.com/path/?utm_source=a"
        url2 = "https://example.com/path/?utm_source=b"
        assert normalize_url(url1) == normalize_url(url2)


class TestConfigLoading:
    """配置加载函数测试"""

    def test_load_yaml_config_exists(self):
        """测试：加载存在的配置文件"""
        from src.utils import load_yaml_config
        config = load_yaml_config("sources.yaml")
        assert isinstance(config, dict)

    def test_load_yaml_config_not_exists(self):
        """测试：加载不存在的配置文件抛出异常"""
        from src.utils import load_yaml_config
        with pytest.raises(FileNotFoundError):
            load_yaml_config("nonexistent_config.yaml")

    def test_load_sources_config(self):
        """测试：加载新闻源配置"""
        from src.utils import load_sources_config
        sources = load_sources_config()
        assert isinstance(sources, list)
        assert len(sources) > 0

    def test_load_importance_config(self):
        """测试：加载重要性配置"""
        from src.utils import load_importance_config
        config = load_importance_config()
        assert isinstance(config, dict)

    def test_load_event_aliases_config(self):
        """测试：加载事件别名配置"""
        from src.utils import load_event_aliases_config
        config = load_event_aliases_config()
        assert isinstance(config, dict)

    def test_load_entities_config(self):
        """测试：加载实体配置"""
        from src.utils import load_entities_config
        config = load_entities_config()
        assert isinstance(config, dict)

    def test_load_actions_config(self):
        """测试：加载动作词表"""
        from src.utils import load_actions_config
        config = load_actions_config()
        assert isinstance(config, dict)

    def test_load_topics_config(self):
        """测试：加载主题词表"""
        from src.utils import load_topics_config
        config = load_topics_config()
        assert isinstance(config, dict)

    def test_load_phase_transitions_config(self):
        """测试：加载阶段转换配置"""
        from src.utils import load_phase_transitions_config
        config = load_phase_transitions_config()
        assert isinstance(config, dict)

    def test_load_prompt_template(self):
        """测试：加载提示词模板"""
        from src.utils import load_prompt_template
        template = load_prompt_template()
        assert isinstance(template, str)
        assert len(template) > 0


class TestRunState:
    """运行状态管理测试"""

    def test_run_state_init(self):
        """测试：RunState 初始化"""
        from src.utils import RunState
        state = RunState()
        assert state.data["status"] == "running"
        assert state.data["run_id"] is not None

    def test_run_state_mark_completed(self):
        """测试：标记步骤完成"""
        from src.utils import RunState
        state = RunState()
        state.mark_step("test_step", "completed")
        assert state.is_step_done("test_step")

    def test_run_state_mark_failed(self):
        """测试：标记步骤失败"""
        from src.utils import RunState
        state = RunState()
        state.mark_step("test_step", "failed", "error occurred")
        assert len(state.data["failed_steps"]) == 1
        assert state.data["failed_steps"][0]["detail"] == "error occurred"

    def test_run_state_mark_skipped(self):
        """测试：标记步骤跳过"""
        from src.utils import RunState
        state = RunState()
        state.mark_step("test_step", "skipped")
        assert len(state.data["skipped_steps"]) == 1

    def test_run_state_finish(self):
        """测试：完成运行"""
        from src.utils import RunState
        state = RunState()
        state.finish("completed")
        assert state.data["status"] == "completed"

    def test_run_state_is_step_done_false(self):
        """测试：未完成的步骤返回 False"""
        from src.utils import RunState
        state = RunState()
        assert state.is_step_done("nonexistent_step") is False


class TestMetricsCollector:
    """指标收集器测试"""

    def test_metrics_collector_init(self):
        """测试：MetricsCollector 初始化"""
        from src.utils import MetricsCollector
        collector = MetricsCollector()
        assert "fetch" in collector.data
        assert "ai" in collector.data
        assert "output" in collector.data
        assert "pipeline" in collector.data

    def test_record_fetch(self):
        """测试：记录抓取指标"""
        from src.utils import MetricsCollector
        collector = MetricsCollector()
        collector.record_fetch(
            total_sources=100,
            success=90,
            failed=10,
            total_news=500,
            duplicates=20,
            repeats=5,
            elapsed=12.5,
            failed_names=["source1", "source2"]
        )
        assert collector.data["fetch"]["total_sources"] == 100
        assert collector.data["fetch"]["success_sources"] == 90
        assert len(collector.data["fetch"]["failed_source_names"]) == 2

    def test_record_ai_summary(self):
        """测试：记录 AI 摘要指标"""
        from src.utils import MetricsCollector
        collector = MetricsCollector()
        collector.record_ai_summary(tokens=1500, elapsed=3.2)
        assert collector.data["ai"]["summary_tokens"] == 1500
        assert collector.data["ai"]["summary_latency_seconds"] == 3.2

    def test_record_ai_extraction(self):
        """测试：记录 AI 提取指标"""
        from src.utils import MetricsCollector
        collector = MetricsCollector()
        collector.record_ai_extraction(tokens=2000, elapsed=5.0, success=True)
        assert collector.data["ai"]["extraction_tokens"] == 2000
        assert collector.data["ai"]["extraction_success"] is True

    def test_record_output(self):
        """测试：记录输出指标"""
        from src.utils import MetricsCollector
        collector = MetricsCollector()
        collector.record_output(
            html_size=102400,
            events=15,
            risk_themes=5,
            evolution_active=3,
            phase_transitions=2
        )
        assert collector.data["output"]["html_size_bytes"] == 102400
        assert collector.data["output"]["events_count"] == 15

    def test_record_pipeline(self):
        """测试：记录 pipeline 指标"""
        from src.utils import MetricsCollector
        collector = MetricsCollector()
        collector.record_pipeline(total_time=60.5, completed=8, failed=1, skipped=1)
        assert collector.data["pipeline"]["total_time_seconds"] == 60.5
        assert collector.data["pipeline"]["steps_completed"] == 8
