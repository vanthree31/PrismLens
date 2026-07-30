"""
Premium 功能开关测试
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


class TestPremiumToggle:
    """Premium 功能开关测试"""

    def setup_method(self):
        """每个测试前重置 premium 状态"""
        import src.premium as premium_mod
        premium_mod._PREMIUM_ENABLED = None

    def teardown_method(self):
        """每个测试后清理 premium 状态"""
        import src.premium as premium_mod
        premium_mod._PREMIUM_ENABLED = None

    def test_premium_enabled_by_default(self):
        """测试：开发阶段默认启用 premium（全权限）"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            assert premium_mod.is_premium_enabled() is True

    def test_premium_enabled_via_env_true(self):
        """测试：通过环境变量启用 premium (true)"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "true"}):
            premium_mod._PREMIUM_ENABLED = None
            assert premium_mod.is_premium_enabled() is True

    def test_premium_enabled_via_env_1(self):
        """测试：通过环境变量启用 premium (1)"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "1"}):
            premium_mod._PREMIUM_ENABLED = None
            assert premium_mod.is_premium_enabled() is True

    def test_premium_enabled_via_env_yes(self):
        """测试：通过环境变量启用 premium (yes)"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "yes"}):
            premium_mod._PREMIUM_ENABLED = None
            assert premium_mod.is_premium_enabled() is True

    def test_premium_still_enabled_via_env_false(self):
        """测试：开发阶段即使环境变量为 false 仍启用 premium"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "false"}):
            premium_mod._PREMIUM_ENABLED = None
            assert premium_mod.is_premium_enabled() is True

    def test_premium_enabled_via_license_file(self):
        """测试：通过 license 文件启用 premium"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            with patch.object(premium_mod, "LICENSE_PATH") as mock_path:
                mock_path.exists.return_value = True
                mock_path.read_text.return_value = "PRISM-ABCD-1234-EFGH"
                premium_mod._PREMIUM_ENABLED = None
                assert premium_mod.is_premium_enabled() is True

    def test_premium_enabled_despite_invalid_license(self):
        """测试：开发阶段即使无效 license 仍启用 premium"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            with patch.object(premium_mod, "LICENSE_PATH") as mock_path:
                mock_path.exists.return_value = True
                mock_path.read_text.return_value = "INVALID-LICENSE"
                premium_mod._PREMIUM_ENABLED = None
                assert premium_mod.is_premium_enabled() is True


class TestPremiumFeatures:
    """Premium 功能列表测试"""

    def setup_method(self):
        import src.premium as premium_mod
        premium_mod._PREMIUM_ENABLED = None

    def teardown_method(self):
        import src.premium as premium_mod
        premium_mod._PREMIUM_ENABLED = None

    def test_get_premium_features_returns_dict(self):
        """测试：返回字典类型"""
        from src.premium import get_premium_features
        features = get_premium_features()
        assert isinstance(features, dict)

    def test_premium_features_has_required_keys(self):
        """测试：包含所有必需的功能键"""
        from src.premium import get_premium_features
        features = get_premium_features()
        required_keys = [
            "multi_camp_compare",
            "transmission_chain",
            "full_sources",
            "full_chapters",
            "history_90days",
            "realtime_alert"
        ]
        for key in required_keys:
            assert key in features, f"缺少功能: {key}"

    def test_premium_feature_structure(self):
        """测试：每个功能条目包含 name/description/enabled"""
        from src.premium import get_premium_features
        features = get_premium_features()
        for key, feature in features.items():
            assert "name" in feature, f"{key} 缺少 name"
            assert "description" in feature, f"{key} 缺少 description"
            assert "enabled" in feature, f"{key} 缺少 enabled"


class TestPremiumLimits:
    """Premium 限制值测试"""

    def setup_method(self):
        import src.premium as premium_mod
        premium_mod._PREMIUM_ENABLED = None

    def teardown_method(self):
        import src.premium as premium_mod
        premium_mod._PREMIUM_ENABLED = None

    def test_source_limit_default(self):
        """测试：开发阶段源限制为 101（全量）"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_source_limit
            assert get_source_limit() == 101

    def test_source_limit_premium(self):
        """测试：付费版源限制为 101"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "true"}):
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_source_limit
            assert get_source_limit() == 101

    def test_chapter_limit_default(self):
        """测试：开发阶段章节数限制为 10（全量）"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_chapter_limit
            assert get_chapter_limit() == 10

    def test_chapter_limit_premium(self):
        """测试：付费版章节数限制为 10"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "true"}):
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_chapter_limit
            assert get_chapter_limit() == 10

    def test_history_days_default(self):
        """测试：开发阶段历史天数为 90（全量）"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_history_days
            assert get_history_days() == 90

    def test_history_days_premium(self):
        """测试：付费版历史天数为 90"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "true"}):
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_history_days
            assert get_history_days() == 90

    def test_camp_compare_count_default(self):
        """测试：开发阶段阵营对比数为 7（全量）"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_camp_compare_count
            assert get_camp_compare_count() == 7

    def test_camp_compare_count_premium(self):
        """测试：付费版阵营对比数为 7"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "true"}):
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_camp_compare_count
            assert get_camp_compare_count() == 7

    def test_transmission_chains_default(self):
        """测试：开发阶段传导链数量为 9（全量）"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PRISM_LENS_PREMIUM", None)
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_transmission_chains
            chains = get_transmission_chains()
            assert len(chains) == 9

    def test_transmission_chains_premium(self):
        """测试：付费版传导链数量为 9"""
        import src.premium as premium_mod
        with patch.dict(os.environ, {"PRISM_LENS_PREMIUM": "true"}):
            premium_mod._PREMIUM_ENABLED = None
            from src.premium import get_transmission_chains
            chains = get_transmission_chains()
            assert len(chains) == 9


class TestLicenseValidation:
    """License 验证测试"""

    def test_validate_license_valid_format(self):
        """测试：有效 license 格式验证通过"""
        from src.premium import _validate_license
        assert _validate_license("PRISM-ABCD-1234-EFGH") is True

    def test_validate_license_empty(self):
        """测试：空 license 验证失败"""
        from src.premium import _validate_license
        assert _validate_license("") is False

    def test_validate_license_wrong_prefix(self):
        """测试：错误前缀验证失败"""
        from src.premium import _validate_license
        assert _validate_license("OTHER-ABCD-1234-EFGH") is False

    def test_validate_license_wrong_parts_count(self):
        """测试：错误部分数量验证失败"""
        from src.premium import _validate_license
        assert _validate_license("PRISM-ABCD-1234") is False


class TestLicenseKeyGeneration:
    """License Key 生成测试"""

    def test_generate_license_key_format(self):
        """测试：生成的 key 符合 PRISM-XXXX-XXXX-XXXX 格式"""
        from src.premium import generate_license_key
        key = generate_license_key()
        parts = key.split("-")
        assert len(parts) == 4
        assert parts[0] == "PRISM"
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert len(parts[3]) == 4

    def test_generate_license_key_unique(self):
        """测试：每次生成的 key 不同"""
        from src.premium import generate_license_key
        keys = {generate_license_key() for _ in range(10)}
        # 10 个 key 至少应该有不同的（概率极高）
        assert len(keys) > 1
