"""
公共 pytest fixtures
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def project_root():
    """返回项目根目录"""
    from src.utils import PROJECT_ROOT
    return PROJECT_ROOT


@pytest.fixture
def mock_env_without_premium():
    """模拟未启用 premium 的环境变量"""
    with patch.dict("os.environ", {}, clear=False):
        # 确保 PRISM_LENS_PREMIUM 未设置
        import os
        os.environ.pop("PRISM_LENS_PREMIUM", None)
        yield


@pytest.fixture
def mock_env_with_premium():
    """模拟启用 premium 的环境变量"""
    with patch.dict("os.environ", {"PRISM_LENS_PREMIUM": "true"}):
        yield


@pytest.fixture
def temp_config_dir(tmp_path):
    """创建临时配置目录并返回"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def sample_sources_config():
    """示例新闻源配置"""
    return {
        "sources": [
            {
                "name": "Reuters",
                "url": "https://www.reuters.com",
                "region": "north_america",
                "category": "finance"
            },
            {
                "name": "BBC",
                "url": "https://www.bbc.com",
                "region": "europe",
                "category": "general"
            }
        ]
    }
