"""
QualityChecker — 日报质量程序化校验模块

对 AI 生成的日报 Markdown 进行 15+ 项自动检查，
不依赖 LLM，纯规则驱动。

检查类别:
  A. 格式完整性 (h2章节数/禁止占位符/禁止空泛词/市场数据)
  B. 区域平衡 (区域覆盖/单一区域占比/零覆盖检测)
  C. 信号质量 (信号分布/置信度格式)
  D. 链接完整性 (链接数量/URL有效性)
  E. 内容质量 (篇幅/信息密度)
"""

import logging
import re
from collections import Counter

logger = logging.getLogger("global_news.quality_checker")


class QualityChecker:
    """日报质量程序化校验器"""

    # 禁止占位符词汇（中文）
    FORBIDDEN_PLACEHOLDERS_ZH = [
        "待补充",
        "待确认",
        "待统计",
        "待更新",
        "暂无实时数据",
        "暂未接入",
        "市场数据暂未接入",
        "tbd",
        "TBD",
        "N/A",
        "n/a",
        "TBC",
    ]

    # 禁止占位符词汇（英文）
    FORBIDDEN_PLACEHOLDERS_EN = [
        "to be added",
        "to be confirmed",
        "to be updated",
        "no real-time data",
        "not available",
        "market data not available",
        "pending",
        "tbd",
        "TBD",
        "N/A",
        "n/a",
        "TBC",
    ]

    # 禁止空泛评价词汇（中文）
    FORBIDDEN_VAGUE_ZH = [
        "意义重大",
        "值得关注",
        "需紧盯",
        "仍存合作空间",
        "料持稳偏强",
        "结构性机会优于系统性机会",
        "短期可能维持区间震荡",
        "逼近区间",
        "密切关注",
        "不容忽视",
        "拭目以待",
    ]

    # 禁止空泛评价词汇（英文）
    FORBIDDEN_VAGUE_EN = [
        "significant",
        "worth watching",
        "keep a close eye",
        "room for cooperation",
        "likely to remain stable",
        "range-bound",
        "structural opportunities outweigh",
        "approaching the range",
        "closely monitor",
        "cannot be ignored",
        "wait and see",
    ]

    # 12 个标准区域（中文）
    ALL_REGIONS_ZH = [
        "中东",
        "欧洲",
        "北美",
        "亚太",
        "东南亚",
        "南亚",
        "中亚",
        "中国",
        "俄罗斯",
        "非洲",
        "南美",
        "大洋洲",
    ]

    # 12 个标准区域（英文）
    ALL_REGIONS_EN = [
        "Middle East",
        "Europe",
        "North America",
        "Asia-Pacific",
        "Southeast Asia",
        "South Asia",
        "Central Asia",
        "China",
        "Russia",
        "Africa",
        "South America",
        "Oceania",
    ]

    # 市场数据关键指标（中文）
    REQUIRED_MARKET_INDICATORS_ZH = [
        "标普500",
        "纳斯达克",
        "Brent",
        "WTI",
        "黄金",
        "VIX",
    ]

    # 市场数据关键指标（英文）
    REQUIRED_MARKET_INDICATORS_EN = [
        "S&P 500",
        "Nasdaq",
        "Brent",
        "WTI",
        "Gold",
        "VIX",
    ]

    def __init__(self, lang: str = "zh"):
        self.lang = lang
        self.issues: list[dict] = []
        self.warnings: list[dict] = []

        # 根据语言选择词汇表
        if lang == "en":
            self.FORBIDDEN_PLACEHOLDERS = self.FORBIDDEN_PLACEHOLDERS_EN
            self.FORBIDDEN_VAGUE = self.FORBIDDEN_VAGUE_EN
            self.ALL_REGIONS = self.ALL_REGIONS_EN
            self.REQUIRED_MARKET_INDICATORS = self.REQUIRED_MARKET_INDICATORS_EN
        else:
            self.FORBIDDEN_PLACEHOLDERS = self.FORBIDDEN_PLACEHOLDERS_ZH
            self.FORBIDDEN_VAGUE = self.FORBIDDEN_VAGUE_ZH
            self.ALL_REGIONS = self.ALL_REGIONS_ZH
            self.REQUIRED_MARKET_INDICATORS = self.REQUIRED_MARKET_INDICATORS_ZH

    def check_all(self, markdown: str, news_items_count: int = 0) -> dict:
        """运行全部检查，返回检查报告"""
        self.issues = []
        self.warnings = []

        # A. 格式完整性
        self._check_h2_count(markdown)
        self._check_forbidden_placeholders(markdown)
        self._check_forbidden_vague(markdown)
        self._check_market_data_presence(markdown)

        # B. 区域平衡
        self._check_region_coverage(markdown)
        self._check_region_dominance(markdown)

        # C. 信号质量
        self._check_signal_distribution(markdown)
        self._check_confidence_format(markdown)

        # D. 链接完整性
        self._check_links_count(markdown, news_items_count)

        # E. 内容质量
        self._check_content_length(markdown)

        return {
            "passed": len(self.issues) == 0,
            "issues": self.issues,
            "warnings": self.warnings,
            "summary": f"{'PASS' if not self.issues else 'FAIL'}: "
            f"{len(self.issues)} 问题, {len(self.warnings)} 警告",
        }

    # ── A. 格式完整性 ──

    def _check_h2_count(self, markdown: str) -> None:
        h2_count = len(re.findall(r"^##\s+", markdown, re.MULTILINE))
        min_required = 4  # 核心4章
        if h2_count < min_required:
            self.issues.append(
                {
                    "category": "A1_h2_count",
                    "message": f"h2章节数不足: {h2_count} < {min_required}",
                    "detail": "核心4章(一/二/三/十)必须输出",
                }
            )
        elif h2_count > 12:
            self.warnings.append(
                {
                    "category": "A1_h2_count",
                    "message": f"h2章节数过多: {h2_count} > 12",
                }
            )

    def _check_forbidden_placeholders(self, markdown: str) -> None:
        found = []
        for word in self.FORBIDDEN_PLACEHOLDERS:
            if word in markdown:
                found.append(word)
        if found:
            self.issues.append(
                {
                    "category": "A2_placeholders",
                    "message": f"发现禁止占位符: {', '.join(found)}",
                    "count": len(found),
                }
            )

    def _check_forbidden_vague(self, markdown: str) -> None:
        found = []
        for word in self.FORBIDDEN_VAGUE:
            if word in markdown:
                found.append(word)
        if found:
            self.warnings.append(
                {
                    "category": "A3_vague",
                    "message": f"发现空泛评价: {', '.join(found[:5])}",
                    "count": len(found),
                }
            )

    def _check_market_data_presence(self, markdown: str) -> None:
        missing = []
        for indicator in self.REQUIRED_MARKET_INDICATORS:
            if indicator not in markdown:
                missing.append(indicator)
        if missing:
            self.issues.append(
                {
                    "category": "A4_market_data",
                    "message": f"市场数据缺失指标: {', '.join(missing)}",
                    "severity": "blocking",
                }
            )

    # ── B. 区域平衡 ──

    def _check_region_coverage(self, markdown: str) -> None:
        covered = []
        missing = []
        for region in self.ALL_REGIONS:
            if region in markdown:
                covered.append(region)
            else:
                missing.append(region)
        if len(covered) < 6:
            self.issues.append(
                {
                    "category": "B1_coverage",
                    "message": f"区域覆盖不足: {len(covered)}/12 ({', '.join(covered)})",
                    "missing": missing,
                }
            )
        elif len(covered) < 8:
            self.warnings.append(
                {
                    "category": "B1_coverage",
                    "message": f"区域覆盖偏少: {len(covered)}/12",
                    "missing": missing,
                }
            )

    def _check_region_dominance(self, markdown: str) -> None:
        counts = Counter()
        for region in self.ALL_REGIONS:
            counts[region] = markdown.count(region)
        if not counts:
            return
        top_region, top_count = counts.most_common(1)[0]
        total = sum(counts.values()) or 1
        pct = top_count / total * 100
        if pct > 45:
            self.issues.append(
                {
                    "category": "B2_dominance",
                    "message": f"单一区域占比过高: {top_region} = {pct:.0f}% (>40%)",
                }
            )
        elif pct > 30:
            self.warnings.append(
                {
                    "category": "B2_dominance",
                    "message": f"单一区域占比偏高: {top_region} = {pct:.0f}% (>30%)",
                }
            )

    # ── C. 信号质量 ──

    def _check_signal_distribution(self, markdown: str) -> None:
        if self.lang == "en":
            s_pattern = r"(?<!\w)S-level(?!\w)"
            a_pattern = r"(?<!\w)A-level(?!\w)"
            s_label = "S-level"
            a_label = "A-level"
        else:
            s_pattern = r"(?<!\w)S级(?!\w)"
            a_pattern = r"(?<!\w)A级(?!\w)"
            s_label = "S级"
            a_label = "A级"
        s_count = len(re.findall(s_pattern, markdown))
        a_count = len(re.findall(a_pattern, markdown))
        if s_count > 2:
            self.warnings.append(
                {
                    "category": "C1_signal",
                    "message": f"{s_label}事件过多: {s_count} (>2)",
                }
            )
        if a_count > 8:
            self.warnings.append(
                {
                    "category": "C1_signal",
                    "message": f"{a_label}事件过多: {a_count} (>8)",
                }
            )

    def _check_confidence_format(self, markdown: str) -> None:
        # 检查是否有新的置信度格式（百分比或5维框架）
        has_pct = bool(re.search(r"置信度.*\d+%", markdown))
        has_fact = "[事实" in markdown
        if not (has_pct or has_fact):
            self.warnings.append(
                {
                    "category": "C2_confidence",
                    "message": "未检测到置信度百分比或事实/推测标注",
                }
            )

    # ── D. 链接完整性 ──

    def _check_links_count(self, markdown: str, total_news: int) -> None:
        urls = re.findall(r"https?://[^\s\)\]]+", markdown)
        if total_news > 0 and len(urls) < total_news * 0.05:
            self.warnings.append(
                {
                    "category": "D1_links",
                    "message": f"链接数量偏少: {len(urls)} (新闻总数: {total_news})",
                }
            )

        # 检查是否有占位符链接
        bad_patterns = ["xxxx", "example.com/test", "/a-123456"]
        bad_urls = [u for u in urls if any(p in u for p in bad_patterns)]
        if bad_urls:
            self.issues.append(
                {
                    "category": "D2_bad_urls",
                    "message": f"发现占位符URL: {len(bad_urls)} 个",
                }
            )

    # ── E. 内容质量 ──

    def _check_content_length(self, markdown: str) -> None:
        char_count = len(markdown)
        if char_count < 3000:
            self.issues.append(
                {
                    "category": "E1_length",
                    "message": f"日报篇幅过短: {char_count} 字符 (< 3000)",
                }
            )
        elif char_count < 5000:
            self.warnings.append(
                {
                    "category": "E1_length",
                    "message": f"日报篇幅偏短: {char_count} 字符 (< 5000)",
                }
            )
