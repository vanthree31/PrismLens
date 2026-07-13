"""
HistoryAnalyzer — 90天历史趋势预计算引擎

对 history_*.json 文件进行时间序列分析，生成结构化趋势仪表盘，注入 Stage 3 prompt。

功能:
1. 主题轨迹追踪（theme trajectories）
2. 拐点检测（inflection points）
3. 新兴/消亡主题识别
4. 区域覆盖时间序列
5. 信号等级分布演变
"""

import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger("global_news.history_analyzer")


class HistoryAnalyzer:
    """90天历史趋势分析器"""

    # 主题归一化规则（正则 + 规范名称）
    THEME_NORMALIZATION = [
        (r"霍尔木兹|Hormuz|伊朗.*攻击|伊朗.*袭击|美伊", "霍尔木兹/美伊冲突"),
        (r"南海|South China Sea|COC|行为准则", "南海安全"),
        (r"台海|Taiwan Strait|台湾|Taiwan", "台海态势"),
        (r"AI.*芯片|chip.*export|半导体|semiconductor|ASML|NVIDIA", "AI芯片战"),
        (r"OPEC|油价|oil price|Brent|WTI|原油|石油", "能源价格波动"),
        (r"美联储|Fed |利率|interest rate|通胀|inflation", "全球货币政策"),
        (r"俄乌|Ukraine|乌克兰|Zelensky", "俄乌冲突"),
        (r"金砖|BRICS|去美元|dedollar", "去美元化/金砖"),
        (r"稀土|rare earth|锂矿|lithium|关键矿产|critical mineral", "关键矿产供应链"),
        (r"LNG|天然气|natural gas|能源安全|energy security", "能源安全"),
        (r"关税|tariff|贸易战|trade war|WTO", "贸易摩擦"),
        (r"北约|NATO|峰会|summit", "北约动态"),
        (r"东盟|ASEAN|东南亚|Southeast Asia", "东盟/东南亚"),
        (r"非洲|Africa|Sahel|萨赫勒", "非洲安全与发展"),
        (r"朝鲜|North Korea|朝核|弹道导弹|ballistic", "朝鲜半岛"),
        (r"太空|space|卫星|satellite|反卫星", "太空军事化"),
        (r"网络|cyber|黑客|ransomware|网络攻击", "网络安全"),
    ]

    def __init__(self, history_dir: Path | None = None, max_days: int = 90):
        from src.utils import get_history_dir

        self.history_dir = history_dir or get_history_dir()
        self.max_days = max_days

    def analyze(self) -> dict:
        """主入口：执行完整趋势分析，返回可序列化的仪表盘"""
        files = self._list_history_files()
        if not files:
            return self._empty_dashboard()

        records = self._load_all(files)
        if not records:
            return self._empty_dashboard()

        theme_trajectory = self._compute_theme_trajectory(records)
        inflection_points = self._detect_inflection_points(theme_trajectory)
        emerging = self._identify_emerging_themes(theme_trajectory)
        regional = self._compute_regional_timeline(records)
        signal_distribution = self._compute_signal_distribution(records)

        return {
            "days_analyzed": len(records),
            "date_range": f"{min(records.keys())} ~ {max(records.keys())}",
            "theme_trajectory": theme_trajectory,
            "inflection_points": inflection_points,
            "emerging_themes": emerging,
            "regional_coverage": regional,
            "signal_distribution": signal_distribution,
        }

    def render_for_prompt(self, lang: str = "zh") -> str:
        """将分析结果渲染为 Stage 3 prompt 文本（~3000 tokens）"""
        dashboard = self.analyze()
        if dashboard.get("days_analyzed", 0) == 0:
            return (
                "暂无足够历史数据进行趋势分析。"
                if lang == "zh"
                else "Insufficient historical data for trend analysis."
            )

        zh = lang == "zh"
        lines = []

        # 头部
        lines.append("### 90天历史趋势仪表盘" if zh else "### 90-Day Historical Trend Dashboard")
        lines.append(
            f"分析范围: {dashboard['date_range']} ({dashboard['days_analyzed']} 天)"
            if zh
            else f"Scope: {dashboard['date_range']} ({dashboard['days_analyzed']} days)"
        )
        lines.append("")

        # 1. 主题轨迹（上升/下降/稳定）
        lines.append("### 主题趋势" if zh else "### Theme Trends")
        trajectories = dashboard.get("theme_trajectory", {})
        if trajectories:
            sorted_themes = sorted(
                trajectories.items(), key=lambda x: x[1].get("trend_score", 0), reverse=True
            )
            for theme, data in sorted_themes[:10]:
                trend = data.get("trend", "→")
                score = data.get("trend_score", 0)
                count = data.get("total_mentions", 0)
                icon = "🔺" if score > 1 else ("🔻" if score < -1 else "➡️")
                lines.append(f"- {icon} **{theme}**: {trend} (提及{count}次, 趋势分{score})")
        lines.append("")

        # 2. 拐点
        lines.append("### 近期拐点" if zh else "### Recent Inflections")
        inflections = dashboard.get("inflection_points", [])
        if inflections:
            for inf in inflections[:5]:
                lines.append(
                    f"- ⚡ {inf['date']}: **{inf['theme']}** "
                    f"{'升级' if zh else 'escalated'} {inf['from_level']}→{inf['to_level']}"
                )
        else:
            lines.append("- 近期无显著拐点" if zh else "- No significant inflections recently")
        lines.append("")

        # 3. 新兴主题
        lines.append("### 新兴信号" if zh else "### Emerging Signals")
        emerging = dashboard.get("emerging_themes", [])
        if emerging:
            for em in emerging[:5]:
                lines.append(
                    f"- 🆕 **{em['theme']}**: 首次出现于 {em['first_seen']}, 后续{em.get('subsequent_days', 0)}天持续出现"
                )
        else:
            lines.append("- 近期无明确新兴主题" if zh else "- No clear emerging themes")
        lines.append("")

        # 4. 区域覆盖
        lines.append("### 区域覆盖趋势" if zh else "### Regional Coverage Trends")
        regional = dashboard.get("regional_coverage", {})
        if regional:
            sorted_regions = sorted(
                regional.items(), key=lambda x: x[1].get("avg_articles", 0), reverse=True
            )
            for region, data in sorted_regions[:8]:
                avg = data.get("avg_articles", 0)
                trend = data.get("trend", "→")
                lines.append(f"- {region}: 日均{avg:.1f}篇, 趋势{trend}")
        lines.append("")

        # 5. 信号分布
        lines.append("### 信号等级分布" if zh else "### Signal Level Distribution")
        sig_dist = dashboard.get("signal_distribution", {})
        if sig_dist:
            lines.append(
                f"- S级: {sig_dist.get('S', 0)}次 | A级: {sig_dist.get('A', 0)}次 | "
                f"B级: {sig_dist.get('B', 0)}次 | C级: {sig_dist.get('C', 0)}次"
            )

        return "\n".join(lines)

    # ── 内部方法 ──

    def _list_history_files(self) -> list[Path]:
        """列出历史数据文件"""
        if not self.history_dir.exists():
            return []
        files = sorted(self.history_dir.glob("history_*.json"), reverse=True)
        return files[: self.max_days]

    def _load_all(self, files: list[Path]) -> dict[str, dict]:
        """加载所有历史文件，返回 {date: parsed_data}"""
        records = {}
        for fp in files:
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", fp.name)
            if not date_match:
                continue
            date = date_match.group(1)
            try:
                with open(fp, encoding="utf-8") as f:
                    data = json.load(f)
                records[date] = data
            except Exception:
                pass
        return records

    def _normalize_theme(self, text: str) -> str:
        """将事件标题/主题归一化到标准主题名"""
        for pattern, name in self.THEME_NORMALIZATION:
            if re.search(pattern, text, re.IGNORECASE):
                return name
        return "其他"

    def _compute_theme_trajectory(self, records: dict[str, dict]) -> dict:
        """计算每个主题在时间轴上的出现轨迹"""
        theme_days: dict[str, list] = defaultdict(list)

        for date, data in sorted(records.items()):
            themes_today: Counter = Counter()
            # 从 clusters 和 signals 中提取主题
            for section in ["clusters", "signals"]:
                items = data.get(section, [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            text = item.get("theme", item.get("title", ""))
                        elif isinstance(item, str):
                            text = item
                        else:
                            continue
                        norm = self._normalize_theme(str(text))
                        themes_today[norm] += 1
            for theme, count in themes_today.items():
                theme_days[theme].append((date, count))

        # 计算趋势
        result = {}
        for theme, occurrences in theme_days.items():
            if len(occurrences) < 2:
                continue
            # 简单线性趋势：比较前半段和后半段的平均出现次数
            mid = len(occurrences) // 2
            first_half_avg = sum(c for _, c in occurrences[:mid]) / max(mid, 1)
            second_half_avg = sum(c for _, c in occurrences[mid:]) / max(len(occurrences) - mid, 1)
            trend_score = (second_half_avg - first_half_avg) * 10  # 放大便于阅读
            trend = "上升 ↑" if trend_score > 1 else ("下降 ↓" if trend_score < -1 else "稳定 →")
            result[theme] = {
                "total_mentions": sum(c for _, c in occurrences),
                "days_active": len(occurrences),
                "trend": trend,
                "trend_score": round(trend_score, 1),
                "last_seen": occurrences[-1][0] if occurrences else "",
            }
        return result

    def _detect_inflection_points(self, trajectories: dict) -> list[dict]:
        """检测信号等级跳变的拐点"""
        # 简化版：基于趋势分数的剧烈变化
        inflections = []
        for theme, data in trajectories.items():
            score = data.get("trend_score", 0)
            if abs(score) > 5:
                inflections.append(
                    {
                        "theme": theme,
                        "date": data.get("last_seen", ""),
                        "from_level": "B" if score > 0 else "A",
                        "to_level": "A" if score > 0 else "B",
                    }
                )
        return sorted(
            inflections,
            key=lambda x: abs(trajectories.get(x["theme"], {}).get("trend_score", 0)),
            reverse=True,
        )

    def _identify_emerging_themes(self, trajectories: dict) -> list[dict]:
        """识别新兴主题：最近7天首次出现且持续"""
        emerging = []
        for theme, data in trajectories.items():
            days_active = data.get("days_active", 0)
            if 1 <= days_active <= 7:
                emerging.append(
                    {
                        "theme": theme,
                        "first_seen": data.get("last_seen", ""),
                        "subsequent_days": days_active - 1,
                    }
                )
        return sorted(emerging, key=lambda x: x.get("subsequent_days", 0), reverse=True)

    def _compute_regional_timeline(self, records: dict[str, dict]) -> dict:
        """计算区域覆盖的时间序列"""
        region_stats: dict[str, list] = defaultdict(list)
        for __date, data in sorted(records.items()):
            regions = data.get("regions", data.get("region", []))
            if isinstance(regions, str):
                regions = [regions]
            if isinstance(regions, dict):
                regions = list(regions.keys())
            if not isinstance(regions, list):
                continue
            for r in regions[:10]:
                region_stats[r].append(1)

        result = {}
        for region, counts in region_stats.items():
            if len(counts) < 2:
                continue
            total = len(counts)
            avg = sum(counts) / max(total, 1)
            mid = total // 2
            trend_score = (sum(counts[mid:]) / max(total - mid, 1)) - (
                sum(counts[:mid]) / max(mid, 1)
            )
            trend = "↑" if trend_score > 0.05 else ("↓" if trend_score < -0.05 else "→")
            result[region] = {"avg_articles": avg, "trend": trend, "days_covered": total}
        return result

    def _compute_signal_distribution(self, records: dict[str, dict]) -> dict:
        """统计各信号等级在历史中的出现频次"""
        dist: Counter = Counter()
        for data in records.values():
            signals = data.get("signals", [])
            if isinstance(signals, list):
                for s in signals:
                    if isinstance(s, dict):
                        level = s.get("signal_level", s.get("level", ""))
                        if level:
                            dist[level] += 1
                    elif isinstance(s, str):
                        dist["B"] += 1  # 默认为B级
        return dict(dist)

    def _empty_dashboard(self) -> dict:
        return {
            "days_analyzed": 0,
            "date_range": "N/A",
            "theme_trajectory": {},
            "inflection_points": [],
            "emerging_themes": [],
            "regional_coverage": {},
            "signal_distribution": {},
        }
