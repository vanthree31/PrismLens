"""
历史数据生命周期管理模块

功能:
- 保存每日结构化分析数据 (signals, clusters, regions)
- 自动清理过期数据
- 压缩归档旧数据
- 支持最近7天数据读取
"""

import gzip
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.utils import get_history_dir, get_today_str

logger = logging.getLogger("global_news.history")


# ─────────────────────────────────────────────────
# 历史数据模型
# ─────────────────────────────────────────────────

@dataclass
class DailyHistory:
    """每日历史数据"""
    date: str
    clusters: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    regions: dict[str, list[str]] = field(default_factory=dict)
    summary_excerpt: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "clusters": self.clusters,
            "signals": self.signals,
            "regions": self.regions,
            "summary_excerpt": self.summary_excerpt,
        }


# ─────────────────────────────────────────────────
# AI 输出解析器
# ─────────────────────────────────────────────────

def _extract_list_items(lines: list[str], start_idx: int, max_items: int = 15) -> list[str]:
    """从指定位置开始提取列表项，遇到下一个标题停止"""
    items = []
    for line in lines[start_idx:]:
        stripped = line.strip()
        # 遇到下一个标题停止（## 或 ###）
        if re.match(r'^#{2,3}\s+', stripped) and items:
            break
        # 提取列表项（- 或 * 开头）
        if re.match(r'^[-*]\s+', stripped):
            item = re.sub(r'^[-*]\s+', '', stripped).strip()
            if item and len(item) > 3:
                items.append(item)
            if len(items) >= max_items:
                break
    return items


def extract_signals_from_summary(summary: str) -> list[str]:
    """
    从 AI 总结中提取风险信号（正则匹配，支持多级标题和加粗格式）

    查找包含 "信号"/"风险"/"观察哨" 的章节下的列表项
    """
    lines = summary.split("\n")
    signal_pattern = re.compile(r'#{2,3}\s+.*?(?:信号|风险|观察哨)', re.IGNORECASE)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if signal_pattern.search(stripped):
            items = _extract_list_items(lines, i + 1, max_items=10)
            if items:
                return items

    # Fallback: 匹配任意包含"风险"的列表项
    fallback = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^[-*]\s+', stripped) and "风险" in stripped:
            item = re.sub(r'^[-*]\s+', '', stripped).strip()
            if item and len(item) > 5:
                fallback.append(item)
            if len(fallback) >= 5:
                break
    return fallback


def extract_clusters_from_summary(summary: str) -> list[str]:
    """
    从 AI 总结中提取事件聚类（正则匹配）

    查找包含 "聚类"/"事件" 的章节下的列表项
    """
    lines = summary.split("\n")
    cluster_pattern = re.compile(r'#{2,3}\s+.*?(?:聚类|事件聚类)', re.IGNORECASE)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if cluster_pattern.search(stripped):
            items = _extract_list_items(lines, i + 1, max_items=15)
            if items:
                return items

    # Fallback: 匹配 ### 开头的事件标题
    fallback = []
    for line in lines:
        stripped = line.strip()
        if re.match(r'^###\s+', stripped):
            title = re.sub(r'^###\s+', '', stripped).strip()
            if title and len(title) > 3:
                fallback.append(title)
            if len(fallback) >= 10:
                break
    return fallback


def extract_regions_from_summary(summary: str) -> dict[str, list[str]]:
    """
    从 AI 总结中提取各地区关键信息（正则匹配）

    支持 ## 和 ### 级别的地区标题，不要求关键词和标题在同一行前缀
    """
    regions = {}
    lines = summary.split("\n")

    region_map = {
        "北美": re.compile(r'北美|美国|加拿大', re.IGNORECASE),
        "欧洲": re.compile(r'欧洲|欧盟|英国|德国|法国', re.IGNORECASE),
        "俄罗斯/中东": re.compile(r'俄罗斯|中东|伊朗|以色列', re.IGNORECASE),
        "亚洲": re.compile(r'亚洲|日本|韩国|印度', re.IGNORECASE),
        "中国": re.compile(r'中国|新华社|CGTN', re.IGNORECASE),
        "南美": re.compile(r'南美|巴西|阿根廷|智利|秘鲁|委内瑞拉|哥伦比亚|拉丁美洲', re.IGNORECASE),
        "非洲": re.compile(r'非洲|南非|尼日利亚|肯尼亚|刚果|埃塞俄比亚|非盟', re.IGNORECASE),
        "大洋洲": re.compile(r'大洋洲|澳大利亚|澳洲|新西兰|太平洋岛国|斐济|巴新', re.IGNORECASE),
    }

    heading_pattern = re.compile(r'^#{2,3}\s+(.+)')

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = heading_pattern.match(stripped)
        if not m:
            continue
        heading_text = m.group(1)

        # 检测地区
        matched_region = None
        for region, pattern in region_map.items():
            if pattern.search(heading_text):
                matched_region = region
                break

        if matched_region:
            items = _extract_list_items(lines, i + 1, max_items=3)
            if items:
                regions.setdefault(matched_region, []).extend(items)
                # 每个地区最多 5 条
                regions[matched_region] = regions[matched_region][:5]

    return regions


# ─────────────────────────────────────────────────
# 历史管理器
# ─────────────────────────────────────────────────

class HistoryManager:
    """历史数据管理器"""

    # 保留完整 JSON 的天数
    KEEP_FULL_DAYS = 30
    # 保留压缩归档的天数
    KEEP_ARCHIVE_DAYS = 180

    def __init__(self):
        self.history_dir = get_history_dir()
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def save_daily(self, summary: str, date_str: str = "") -> Path:
        """
        保存每日分析数据

        Args:
            summary: AI 生成的完整分析报告
            date_str: 日期字符串 (默认今日)

        Returns:
            保存的文件路径
        """
        if not date_str:
            date_str = get_today_str()

        # 提取结构化数据
        signals = extract_signals_from_summary(summary)
        clusters = extract_clusters_from_summary(summary)
        regions = extract_regions_from_summary(summary)

        # 校验：若摘要很长但提取为空，说明 AI 输出格式可能变化
        if len(summary) > 500:
            if not signals:
                logger.warning(f"信号提取为空但摘要长度 {len(summary)} 字符 — AI 输出格式可能变化")
            if not clusters:
                logger.warning(f"聚类提取为空但摘要长度 {len(summary)} 字符 — AI 输出格式可能变化")

        # 提取摘要片段 (前500字符)
        excerpt = summary[:500].replace("\n", " ").strip()

        # 构建历史数据
        history = DailyHistory(
            date=date_str,
            clusters=clusters,
            signals=signals,
            regions=regions,
            summary_excerpt=excerpt,
        )

        # 保存为 JSON
        file_path = self.history_dir / f"history_{date_str}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(history.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(f"历史数据已保存: {file_path}")
        logger.info(f"  - 信号: {len(signals)} 条")
        logger.info(f"  - 聚类: {len(clusters)} 个")
        logger.info(f"  - 地区: {len(regions)} 个")

        # 执行清理
        self.cleanup()

        return file_path

    def load_recent(self, days: int = 7) -> list[DailyHistory]:
        """
        加载最近 N 天的历史数据

        Args:
            days: 天数

        Returns:
            历史数据列表 (按日期降序)
        """
        histories = []
        today = datetime.now()

        for i in range(days):
            date = today - timedelta(days=i)
            date_str = date.strftime("%Y-%m-%d")
            file_path = self.history_dir / f"history_{date_str}.json"

            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    histories.append(DailyHistory(**data))
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning(f"历史数据读取失败 {date_str}: {e}")

        logger.info(f"加载最近 {len(histories)} 天历史数据")
        return histories

    def cleanup(self) -> None:
        """
        自动清理过期数据

        策略:
        - 30天内: 保留完整 JSON
        - 30-180天: 压缩为 gzip 归档
        - 180天以上: 删除原始总结，仅保留信号和聚类
        """
        today = datetime.now()
        cleaned_count = 0
        archived_count = 0

        for file_path in self.history_dir.glob("history_*.json"):
            # 从文件名提取日期
            try:
                date_str = file_path.stem.replace("history_", "")
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            age_days = (today - file_date).days

            if age_days > self.KEEP_ARCHIVE_DAYS:
                # 180天以上：删除
                file_path.unlink()
                cleaned_count += 1
                logger.debug(f"删除过期历史: {date_str}")

            elif age_days > self.KEEP_FULL_DAYS:
                # 30-180天：压缩归档
                self._compress_to_gzip(file_path)
                archived_count += 1

        if cleaned_count or archived_count:
            logger.info(f"历史清理: 删除 {cleaned_count} 个, 归档 {archived_count} 个")

    def _compress_to_gzip(self, json_path: Path) -> None:
        """将 JSON 文件压缩为 gzip 归档"""
        gzip_path = json_path.with_suffix(".json.gz")

        # 如果已存在归档则跳过
        if gzip_path.exists():
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 只保留关键字段
            compressed_data = {
                "date": data.get("date"),
                "clusters": data.get("clusters", []),
                "signals": data.get("signals", []),
            }

            with gzip.open(gzip_path, "wt", encoding="utf-8") as f:
                json.dump(compressed_data, f, ensure_ascii=False)

            # 删除原始 JSON
            json_path.unlink()
            logger.debug(f"归档历史: {json_path.name} -> {gzip_path.name}")

        except Exception as e:
            logger.warning(f"归档失败 {json_path}: {e}")
