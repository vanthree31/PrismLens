"""
Prompt Manager v1 — Prompt 版本管理与 Benchmark 绑定

设计原则:
- 每次 Prompt 修改必须有版本记录
- 版本与 Benchmark 结果绑定（同版本 → 同 Benchmark）
- 可追溯：知道每次修改的原因、影响、验证结果

用法:
    manager = PromptManager()
    manager.current_version  # "3.0.0"
    manager.record_change("修改了编辑策略中的信号分级表")
    manager.get_changelog()  # 所有变更历史
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("global_news.prompt_manager")


@dataclass
class PromptVersion:
    """Prompt 版本记录"""
    version: str
    timestamp: str
    reason: str = ""
    affected_layers: list[str] = field(default_factory=list)
    benchmark_score: float | None = None
    benchmark_dataset: str = ""
    checksum: str = ""
    author: str = "system"


class PromptManager:
    """Prompt 版本管理器"""

    VERSION_FILE = "prompt_version.json"

    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            from src.utils import get_prompts_dir
            base_dir = get_prompts_dir()
        self.prompts_dir = base_dir
        self.version_path = base_dir / self.VERSION_FILE
        self._history: list[PromptVersion] = []
        self._load()

    @property
    def current_version(self) -> str:
        if self._history:
            return self._history[-1].version
        return "0.0.0"

    def _load(self) -> None:
        if self.version_path.exists():
            try:
                data = json.loads(self.version_path.read_text(encoding="utf-8"))
                self._history = [
                    PromptVersion(**v) for v in data.get("history", [])
                ]
            except Exception as e:
                logger.warning(f"Prompt 版本文件加载失败: {e}")

    def _save(self) -> None:
        data = {
            "current_version": self.current_version,
            "last_updated": datetime.now().isoformat(),
            "history": [asdict(v) for v in self._history],
        }
        self.version_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def compute_checksum(self, layers: list[str] | None = None) -> str:
        """计算当前 Prompt 文件的 SHA256 checksum"""
        if layers is None:
            from src.prompt_builder import LAYER_FILES
            layers = list(LAYER_FILES.keys())

        hasher = hashlib.sha256()
        for layer_name in sorted(layers):
            from src.prompt_builder import LAYER_FILES
            filename = LAYER_FILES.get(layer_name)
            if filename:
                v3_path = self.prompts_dir / "v3" / "zh" / filename
                if v3_path.exists():
                    hasher.update(v3_path.read_bytes())
        return hasher.hexdigest()[:12]

    def record_change(
        self,
        reason: str,
        affected_layers: list[str] | None = None,
        bump: str = "patch",
    ) -> PromptVersion:
        """记录一次 Prompt 修改

        Args:
            reason: 为什么修改？
            affected_layers: 影响了哪些 Layer？
            bump: 版本号升哪一位 (major/minor/patch)
        """
        new_version = self._bump_version(bump)

        v = PromptVersion(
            version=new_version,
            timestamp=datetime.now().isoformat(),
            reason=reason,
            affected_layers=affected_layers or [],
            checksum=self.compute_checksum(affected_layers),
        )

        self._history.append(v)
        self._save()

        logger.info(
            f"Prompt {v.version}: {reason} "
            f"(layers: {','.join(v.affected_layers) if v.affected_layers else 'all'})"
        )
        return v

    def bind_benchmark(self, score: float, dataset: str = "") -> None:
        """将当前版本的 Benchmark 结果绑定到版本记录"""
        if self._history:
            self._history[-1].benchmark_score = score
            self._history[-1].benchmark_dataset = dataset
            self._save()

    def get_changelog(self, n: int = 20) -> list[dict]:
        """获取最近 N 条变更记录"""
        return [
            {
                "version": v.version,
                "timestamp": v.timestamp,
                "reason": v.reason,
                "layers": v.affected_layers,
                "benchmark": v.benchmark_score,
            }
            for v in self._history[-n:]
        ]

    def validate_no_regression(self, current_score: float, threshold: float = 3.0) -> dict:
        """验证当前版本没有性能退化

        Returns:
            {"regression": bool, "delta": float, "previous_score": float}
        """
        if len(self._history) < 2:
            return {"regression": False, "delta": 0, "previous_score": None}

        prev = self._history[-2].benchmark_score
        if prev is None:
            return {"regression": False, "delta": 0, "previous_score": None}

        delta = current_score - prev
        return {
            "regression": delta < -threshold,
            "delta": round(delta, 1),
            "previous_score": prev,
            "threshold": threshold,
        }

    def _bump_version(self, bump: str = "patch") -> str:
        if not self._history:
            return "3.0.0" if bump == "major" else "3.0.1"

        parts = self.current_version.split(".")
        if bump == "major":
            parts = [str(int(parts[0]) + 1), "0", "0"]
        elif bump == "minor":
            parts = [parts[0], str(int(parts[1]) + 1), "0"]
        else:
            parts = [parts[0], parts[1], str(int(parts[2]) + 1)]

        return ".".join(parts)


# 全局实例
_prompt_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager
