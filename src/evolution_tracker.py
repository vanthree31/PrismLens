"""
演化追踪层 — 事件生命周期与阶段跃迁检测

追踪事件从：外交 → 制裁 → 军事 → 金融 → 降温 的完整生命周期
检测阶段跃迁、参与者变化、金融市场传导
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from src.utils import get_data_dir, load_phase_transitions_config

logger = logging.getLogger("global_news.evolution")

SCHEMA_VERSION = "1.0"

# ─────────────────────────────────────────────────
# 从配置加载（带 fallback）
# ─────────────────────────────────────────────────


def _load_phase_types() -> dict:
    """加载阶段类型定义"""
    try:
        config = load_phase_transitions_config()
        return config.get("phase_types", {})
    except Exception:
        return {
            "diplomatic": {"label": "外交阶段", "risk_base": 20},
            "economic": {"label": "经济阶段", "risk_base": 40},
            "sanction": {"label": "制裁阶段", "risk_base": 55},
            "military": {"label": "军事阶段", "risk_base": 75},
            "financial": {"label": "金融传导", "risk_base": 65},
            "de-escalation": {"label": "降温阶段", "risk_base": 15},
        }


def _load_phase_deltas() -> dict:
    """加载阶段转换风险增量"""
    try:
        config = load_phase_transitions_config()
        result = {}
        for _key, info in config.get("phase_transitions", {}).items():
            from_tuple = (info["from"], info["to"])
            result[from_tuple] = info["risk_delta"]
        return result
    except Exception:
        return {
            ("diplomatic", "sanction"): 10,
            ("diplomatic", "military"): 25,
            ("diplomatic", "economic"): 8,
            ("sanction", "military"): 25,
            ("sanction", "economic"): 10,
            ("economic", "military"): 25,
            ("economic", "financial"): 15,
            ("military", "financial"): 20,
            ("military", "de-escalation"): -30,
            ("financial", "de-escalation"): -20,
            ("de-escalation", "diplomatic"): -15,
        }


PHASE_TYPES = _load_phase_types()
PHASE_DELTAS = _load_phase_deltas()
TIME_DECAY_HALF_LIFE_DAYS = 14  # 14天半衰期


# ─────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────


@dataclass
class PhaseRecord:
    """阶段记录"""

    date: str
    phase_type: str
    description: str
    signal_level: str
    risk_delta: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EventEvolution:
    """单个事件的演化轨迹"""

    event_id: str
    title: str
    current_phase: str = "diplomatic"
    current_risk: int = 0
    phases: list[PhaseRecord] = field(default_factory=list)
    participants_timeline: list[dict] = field(default_factory=list)
    market_impact_timeline: list[dict] = field(default_factory=list)
    first_seen: str = ""
    last_updated: str = ""

    def to_dict(self) -> dict:
        self.phases = [p.to_dict() if isinstance(p, PhaseRecord) else p for p in self.phases]
        d = asdict(self)
        return d


@dataclass
class DailyEvolutionReport:
    """每日演化报告"""

    date: str
    schema_version: str = SCHEMA_VERSION
    evolutions: list[EventEvolution] = field(default_factory=list)
    active_events: int = 0
    phase_transitions_today: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        self.evolutions = [
            e.to_dict() if isinstance(e, EventEvolution) else e for e in self.evolutions
        ]
        d = asdict(self)
        return d


# ─────────────────────────────────────────────────
# 演化追踪器
# ─────────────────────────────────────────────────


class EvolutionTracker:
    """事件演化追踪器"""

    def __init__(self):
        self.evolution_dir = get_data_dir() / "evolution"
        self.evolution_dir.mkdir(parents=True, exist_ok=True)
        self.master_file = self.evolution_dir / "evolution_master.json"

    def update_from_events(self, events: list, date_str: str) -> DailyEvolutionReport:
        """
        从今日事件图谱更新演化轨迹

        Args:
            events: 今日事件列表 (Event objects)
            date_str: 日期字符串

        Returns:
            每日演化报告
        """
        # 加载主演化数据库
        master = self._load_master()

        transitions_today = []

        for event in events:
            eid = event.event_id if hasattr(event, "event_id") else event.get("event_id", "")
            title = event.title if hasattr(event, "title") else event.get("title", "")
            phase = event.phase if hasattr(event, "phase") else event.get("phase", "diplomatic")
            signal = (
                event.signal_level
                if hasattr(event, "signal_level")
                else event.get("signal_level", "C")
            )
            actors = event.actors if hasattr(event, "actors") else event.get("actors", [])
            summary = event.summary if hasattr(event, "summary") else event.get("summary", "")

            if eid not in master:
                # 新事件
                master[eid] = EventEvolution(
                    event_id=eid,
                    title=title,
                    current_phase=phase,
                    first_seen=date_str,
                    last_updated=date_str,
                )

            evolution = master[eid]

            # 检测阶段跃迁
            if phase != evolution.current_phase:
                risk_delta = self._calc_phase_delta(evolution.current_phase, phase)
                transition = {
                    "date": date_str,
                    "from": evolution.current_phase,
                    "to": phase,
                    "risk_delta": risk_delta,
                    "event": title,
                }
                transitions_today.append(transition)

                evolution.phases.append(
                    PhaseRecord(
                        date=date_str,
                        phase_type=phase,
                        description=summary[:200],
                        signal_level=signal,
                        risk_delta=risk_delta,
                    )
                )
                evolution.current_phase = phase
                evolution.current_risk = max(0, min(100, evolution.current_risk + risk_delta))

            # 更新参与者
            new_actors = [a for a in actors if a not in self._get_all_actors(evolution)]
            if new_actors:
                evolution.participants_timeline.append(
                    {
                        "date": date_str,
                        "new_actors": new_actors,
                    }
                )

            evolution.last_updated = date_str

        # 时间衰减
        self._apply_time_decay(master, date_str)

        # 归档不活跃事件 (>60天无更新且 risk=0)
        self._prune_inactive(master, date_str)

        # 保存
        self._save_master(master)

        # 构建报告
        active = [
            e
            for e in master.values()
            if e.last_updated >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        ]
        report = DailyEvolutionReport(
            date=date_str,
            evolutions=active,
            active_events=len(active),
            phase_transitions_today=transitions_today,
        )
        self._save_daily_report(report)

        logger.info(f"演化追踪更新: {len(active)} 个活跃事件, {len(transitions_today)} 个阶段跃迁")
        return report

    def _get_all_actors(self, evolution: EventEvolution) -> list[str]:
        """获取事件历史上所有参与者"""
        actors = []
        for entry in evolution.participants_timeline:
            actors.extend(entry.get("new_actors", []))
        return actors

    def _calc_phase_delta(self, old_phase: str, new_phase: str) -> int:
        """计算阶段跃迁的风险增量（从配置加载）"""
        return PHASE_DELTAS.get((old_phase, new_phase), 0)

    def _apply_time_decay(self, master: dict, date_str: str) -> None:
        """对长期无更新的事件应用时间衰减"""
        today = datetime.strptime(date_str, "%Y-%m-%d")
        import math

        for _eid, evolution in master.items():
            if not evolution.last_updated:
                continue
            try:
                last = datetime.strptime(evolution.last_updated, "%Y-%m-%d")
                days_since = (today - last).days
                if days_since > 0:
                    decay_factor = math.exp(-0.693 * days_since / TIME_DECAY_HALF_LIFE_DAYS)
                    evolution.current_risk = int(evolution.current_risk * decay_factor)
            except ValueError:
                pass

    def _load_master(self) -> dict[str, EventEvolution]:
        """加载主演化数据库（单条容错：单条解析失败跳过，不丢弃全部）"""
        if not self.master_file.exists():
            return {}
        try:
            with open(self.master_file, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            # 尝试读取 .tmp 备份
            tmp_file = self.master_file.with_suffix(".json.tmp")
            if tmp_file.exists():
                try:
                    with open(tmp_file, encoding="utf-8") as f:
                        data = json.load(f)
                    logger.info("从 .tmp 备份恢复演化主数据库")
                except Exception as e:
                    logger.warning(f"加载演化主数据库失败（含备份）: {e}")
                    return {}
            else:
                logger.warning("演化主数据库 JSON 解析失败，无备份可用")
                return {}
        except Exception as e:
            logger.warning(f"加载演化主数据库失败: {e}")
            return {}

        master = {}
        skipped = 0
        for eid, edata in data.items():
            try:
                phases = [PhaseRecord(**p) for p in edata.get("phases", [])]
                master[eid] = EventEvolution(
                    event_id=edata["event_id"],
                    title=edata["title"],
                    current_phase=edata.get("current_phase", "diplomatic"),
                    current_risk=edata.get("current_risk", 0),
                    phases=phases,
                    participants_timeline=edata.get("participants_timeline", []),
                    market_impact_timeline=edata.get("market_impact_timeline", []),
                    first_seen=edata.get("first_seen", ""),
                    last_updated=edata.get("last_updated", ""),
                )
            except Exception:
                skipped += 1
                continue
        if skipped:
            logger.warning(f"演化主数据库: 跳过 {skipped} 条损坏记录")
        return master

    def _save_master(self, master: dict[str, EventEvolution]) -> None:
        """保存主演化数据库（原子写入：先写 .tmp 再 rename）"""
        import os

        data = {eid: e.to_dict() for eid, e in master.items()}
        tmp_file = self.master_file.with_suffix(".json.tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(str(tmp_file), str(self.master_file))
        except Exception as e:
            logger.error(f"保存演化主数据库失败: {e}")
            raise

    def _prune_inactive(
        self, master: dict[str, EventEvolution], date_str: str, inactive_days: int = 60
    ) -> int:
        """
        归档长期不活跃的事件

        规则：current_risk == 0 且 last_updated 超过 inactive_days 天的事件
        移入 data/evolution/archived/ 目录

        Returns:
            归档的事件数量
        """
        today = datetime.strptime(date_str, "%Y-%m-%d")
        archived_dir = self.evolution_dir / "archived"
        archived_dir.mkdir(parents=True, exist_ok=True)

        to_archive = []
        for eid, evolution in master.items():
            if evolution.current_risk > 0:
                continue
            if not evolution.last_updated:
                continue
            try:
                last = datetime.strptime(evolution.last_updated, "%Y-%m-%d")
                days_inactive = (today - last).days
                if days_inactive >= inactive_days:
                    to_archive.append(eid)
            except ValueError:
                continue

        for eid in to_archive:
            evolution = master.pop(eid)
            archive_file = archived_dir / f"archived_{eid}.json"
            try:
                with open(archive_file, "w", encoding="utf-8") as f:
                    json.dump(evolution.to_dict(), f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        if to_archive:
            logger.info(f"演化追踪: 归档 {len(to_archive)} 个不活跃事件 (>{inactive_days}天)")
        return len(to_archive)

    def _save_daily_report(self, report: DailyEvolutionReport) -> None:
        """保存每日演化报告"""
        file_path = self.evolution_dir / f"evolution_{report.date}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
