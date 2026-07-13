"""
Benchmark Runner v1 — 固定数据集回放与评分

用法:
    python -m benchmark.runner --dataset golden_2026-07-13
    python -m benchmark.runner --dataset all --pipeline v3
    python -m benchmark.runner --compare v2 v3 --dataset golden_2026-07-13

输出:
    benchmark/reports/{dataset}_{pipeline}_{timestamp}.json
"""

import argparse
import json
import os
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.telemetry import PipelineTelemetry, Telemetry, estimate_cost
from src.utils import load_env, setup_logging


BENCHMARK_DIR = PROJECT_ROOT / "benchmark"
DATASET_DIR = BENCHMARK_DIR / "datasets"
REPORT_DIR = BENCHMARK_DIR / "reports"

DATASET_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════

def score_output(telemetry: PipelineTelemetry) -> dict:
    """统一的 Benchmark 评分（0-100 加权）"""
    weights = {
        "regions": 15,
        "chapters": 10,
        "cleanliness": 15,
        "json": 10,
        "citation": 10,
        "signal_accuracy": 15,
        "latency": 10,
        "token_efficiency": 10,
        "stability": 5,
    }

    subscores = {}

    # 区域覆盖
    subscores["regions"] = min(telemetry.regions_covered / 12 * weights["regions"], weights["regions"])

    # 章节完整性
    subscores["chapters"] = min(telemetry.h2_chapters / 10 * weights["chapters"], weights["chapters"])

    # 整洁度（无占位符+空泛词）
    cleanliness = weights["cleanliness"]
    cleanliness -= telemetry.placeholder_count * 5
    cleanliness -= telemetry.vague_count * 3
    subscores["cleanliness"] = max(0, cleanliness)

    # JSON 解析
    subscores["json"] = weights["json"] if telemetry.json_parse_success else 0

    # 引用密度
    subscores["citation"] = min(telemetry.citation_density * weights["citation"], weights["citation"])

    # 信号准确性（S<=1, A<=5）
    signal_score = weights["signal_accuracy"]
    if telemetry.s_level_count > 1:
        signal_score -= (telemetry.s_level_count - 1) * 5
    if telemetry.a_level_count > 5:
        signal_score -= (telemetry.a_level_count - 5) * 2
    subscores["signal_accuracy"] = max(0, signal_score)

    # 延迟（<180s 满分，>600s 0分）
    if telemetry.total_seconds < 180:
        subscores["latency"] = weights["latency"]
    elif telemetry.total_seconds < 600:
        subscores["latency"] = weights["latency"] * (600 - telemetry.total_seconds) / 420
    else:
        subscores["latency"] = 0

    # Token 效率
    if telemetry.total_tokens < 50000:
        subscores["token_efficiency"] = weights["token_efficiency"]
    elif telemetry.total_tokens < 100000:
        subscores["token_efficiency"] = weights["token_efficiency"] * 0.7
    else:
        subscores["token_efficiency"] = weights["token_efficiency"] * 0.3

    # 稳定性（无回退/截断/解析失败）
    stability = weights["stability"]
    if telemetry.had_fallback:
        stability -= 3
    if telemetry.had_truncation:
        stability -= 2
    if telemetry.had_parse_error:
        stability -= 2
    subscores["stability"] = max(0, stability)

    total = sum(subscores.values())
    return {
        "total_score": round(total, 1),
        "subscores": {k: round(v, 1) for k, v in subscores.items()},
        "grade": _grade(total),
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    return "F"


# ═══════════════════════════════════════════════════
# Dataset Management
# ═══════════════════════════════════════════════════

def save_dataset(name: str, news_items: list) -> Path:
    """保存新闻数据集（用于后续回放）"""
    path = DATASET_DIR / f"{name}.pkl"
    with open(path, "wb") as f:
        pickle.dump(news_items, f)
    print(f"[OK] Dataset saved: {path} ({len(news_items)} items)")
    return path


def load_dataset(name: str) -> list:
    """加载数据集"""
    path = DATASET_DIR / f"{name}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path, "rb") as f:
        return pickle.load(f)


def list_datasets() -> list[str]:
    """列出所有可用数据集"""
    return sorted([p.stem for p in DATASET_DIR.glob("*.pkl")])


# ═══════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════

def run_benchmark(dataset_name: str, pipeline: str = "v3") -> dict:
    """对单个数据集运行 Benchmark"""
    from src.main import run as run_pipeline

    news_items = load_dataset(dataset_name)
    print(f"\n[Benchmark] Dataset: {dataset_name} ({len(news_items)} items)")
    print(f"[Benchmark] Pipeline: {pipeline}")

    start = time.time()
    try:
        report_path = run_pipeline(
            skip_fetch=False,  # will use mock
            max_news=len(news_items),
            auto_open=False,
            pipeline=pipeline,
        )
        elapsed = time.time() - start

        # Load telemetry
        telemetry = Telemetry()
        records = telemetry.load_recent(days=1, pipeline=pipeline)
        t = records[0] if records else PipelineTelemetry()

        scoring = score_output(t)

        result = {
            "dataset": dataset_name,
            "pipeline": pipeline,
            "timestamp": datetime.now().isoformat(),
            "elapsed": round(elapsed, 1),
            "report_path": str(report_path) if report_path else None,
            "scoring": scoring,
            "telemetry": t.to_dict() if t else {},
        }

    except Exception as e:
        elapsed = time.time() - start
        result = {
            "dataset": dataset_name,
            "pipeline": pipeline,
            "timestamp": datetime.now().isoformat(),
            "elapsed": round(elapsed, 1),
            "error": str(e),
            "scoring": {"total_score": 0, "grade": "F", "subscores": {}},
        }

    # Save report
    ts = datetime.now().strftime("%H%M%S")
    report_path = REPORT_DIR / f"{dataset_name}_{pipeline}_{ts}.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Benchmark] Report: {report_path}")
    print(f"[Benchmark] Score: {result['scoring']['total_score']}/100 ({result['scoring']['grade']})")

    return result


def compare_pipelines(dataset_name: str) -> dict:
    """对比 v2 vs v3 在同一个数据集上"""
    print(f"\n{'='*60}")
    print(f"Pipeline Comparison: {dataset_name}")
    print(f"{'='*60}")

    results = {}
    for pipeline in ["v2", "v3"]:
        try:
            results[pipeline] = run_benchmark(dataset_name, pipeline)
        except Exception as e:
            results[pipeline] = {"error": str(e)}
            print(f"[ERROR] {pipeline}: {e}")

    # Comparison
    v2_score = results.get("v2", {}).get("scoring", {}).get("total_score", 0)
    v3_score = results.get("v3", {}).get("scoring", {}).get("total_score", 0)

    comparison = {
        "dataset": dataset_name,
        "timestamp": datetime.now().isoformat(),
        "v2_score": v2_score,
        "v3_score": v3_score,
        "winner": "v3" if v3_score >= v2_score else "v2",
        "delta": round(v3_score - v2_score, 1),
        "details": results,
    }

    ts = datetime.now().strftime("%H%M%S")
    comp_path = REPORT_DIR / f"compare_{dataset_name}_{ts}.json"
    comp_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"Result: v2={v2_score} vs v3={v3_score} → {comparison['winner']} wins (+{comparison['delta']})")
    print(f"Report: {comp_path}")

    return comparison


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="PrismLens Benchmark Runner")
    parser.add_argument("--dataset", default="all", help="Dataset name or 'all'")
    parser.add_argument("--pipeline", default="v3", choices=["v2", "v3"])
    parser.add_argument("--compare", nargs=2, metavar=("P1", "P2"), help="Compare two pipelines")
    parser.add_argument("--list", action="store_true", help="List available datasets")
    parser.add_argument("--save", help="Save current fetch as dataset (provide name)")
    args = parser.parse_args()

    if args.list:
        ds = list_datasets()
        print(f"Available datasets ({len(ds)}):")
        for d in ds:
            print(f"  - {d}")
        return

    if args.save:
        from src.fetcher import NewsFetcher
        env = load_env()
        fetcher = NewsFetcher(proxy=env.get("https_proxy", ""))
        items = fetcher.fetch_all(max_news=500)
        save_dataset(args.save, items)
        return

    if args.compare:
        compare_pipelines(args.dataset)
        return

    if args.dataset == "all":
        for ds in list_datasets():
            run_benchmark(ds, args.pipeline)
    else:
        run_benchmark(args.dataset, args.pipeline)


if __name__ == "__main__":
    main()
