"""
基础测试 - 验证项目结构和导入
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_project_root():
    """测试项目根目录路径获取"""
    from src.utils import PROJECT_ROOT
    assert PROJECT_ROOT.exists()
    assert PROJECT_ROOT.is_dir()


def test_config_dir():
    """测试配置目录存在"""
    from src.utils import get_config_dir
    config_dir = get_config_dir()
    assert config_dir.exists()
    # 免费版或完整版配置文件存在即可
    assert (config_dir / "sources.yaml").exists() or (config_dir / "sources_free.yaml").exists()


def test_prompts_dir():
    """测试提示词目录存在"""
    from src.utils import get_prompts_dir
    prompts_dir = get_prompts_dir()
    assert prompts_dir.exists()
    # 免费版或完整版 prompt 存在即可
    assert (prompts_dir / "summary_prompt.txt").exists() or (prompts_dir / "summary_prompt_free.txt").exists()


def test_import_fetcher():
    """测试fetcher模块可导入"""
    from src.fetcher import NewsFetcher
    assert NewsFetcher is not None


def test_import_summarizer():
    """测试summarizer模块可导入"""
    from src.summarizer import NewsSummarizer
    assert NewsSummarizer is not None


def test_import_generator():
    """测试generator模块可导入"""
    from src.generator import ReportGenerator
    assert ReportGenerator is not None


def test_import_risk_scorer():
    """测试risk_scorer模块可导入"""
    from src.risk_scorer import RiskScorer
    assert RiskScorer is not None


def test_import_event_graph():
    """测试event_graph模块可导入"""
    from src.event_graph import Event, DailyEventGraph
    assert Event is not None
    assert DailyEventGraph is not None


def test_event_dataclass():
    """测试Event数据类"""
    from src.event_graph import Event
    event = Event(
        event_id="test-001",
        title="测试事件",
        signal_level="A",
        confidence="高",
        actors=["美国", "中国"],
        source_lean="中立",
        lean_reasoning="测试"
    )
    assert event.event_id == "test-001"
    assert event.signal_level == "A"
    assert event.source_lean == "中立"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
