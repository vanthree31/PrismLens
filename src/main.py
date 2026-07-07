"""
主流程编排模块

负责协调 fetcher、summarizer、generator、history_manager 及四层情报架构完成完整工作流。

流程:
1. 抓取新闻
2. AI 生成日报 (第一次 API 调用)
3. 生成 HTML 报告
4. AI 提取结构化数据 (第二次 API 调用)
5. 更新四层架构: 事件图谱 / 历史记忆 / 风险评分 / 演化追踪

容错设计:
- 每个步骤独立 try/except，单步失败不影响其他步骤
- 运行状态持久化到 data/runtime/current_run.json
- 性能指标记录到 data/metrics/metrics_YYYY-MM-DD.json
"""

import logging
import sys
import time
import webbrowser
from pathlib import Path

from src.event_graph import save_event_graph
from src.evolution_tracker import EvolutionTracker
from src.fetcher import NewsFetcher
from src.generator import ReportGenerator
from src.history_manager import HistoryManager
from src.risk_scorer import RiskScorer, DailyRiskReport, save_risk_report
from src.structured_extractor import StructuredExtractor, build_event_graph_from_extraction
from src.summarizer import NewsSummarizer
from src.utils import (
    ensure_directories,
    load_env,
    setup_logging,
    get_today_str,
    get_cache_dir,
    RunState,
    MetricsCollector,
)

logger = logging.getLogger("global_news.main")


# ─────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────

def run(
    skip_fetch: bool = False,
    skip_ai: bool = False,
    mock_summary: str = "",
    max_news: int = 150,
    auto_open: bool = False,
    lang: str = "zh",
) -> Path:
    """执行完整的新闻简报生成流程，支持断步容错"""
    pipeline_start = time.time()

    # ─── 1. 初始化 ───
    ensure_directories()
    global logger
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("Global News Briefing 启动")
    logger.info(f"日期: {get_today_str()}")
    logger.info("=" * 60)

    state = RunState()
    metrics = MetricsCollector()

    try:
        env = load_env()
    except FileNotFoundError as e:
        logger.error(str(e))
        state.mark_step("init", "failed", str(e))
        state.finish("failed")
        sys.exit(1)

    today = get_today_str()

    # ─── 2. 抓取新闻 ───
    news_items = []
    fetch_elapsed = 0
    if not skip_fetch:
        logger.info("[1/6] 抓取全球新闻...")
        fetch_start = time.time()
        try:
            fetcher = NewsFetcher(
                proxy=env.get("https_proxy", ""),
                ssl_verify=env.get("ssl_verify", True),
            )
            news_items = fetcher.fetch_all(max_news=max_news)
            fetch_elapsed = time.time() - fetch_start

            if not news_items:
                logger.warning("未获取到任何新闻，流程终止")
                state.mark_step("fetch", "failed", "无新闻")
                state.finish("failed")
                sys.exit(1)

            logger.info(f"抓取完成: {len(news_items)} 条新闻 ({fetch_elapsed:.1f}s)")
            state.mark_step("fetch", "completed")

            # 记录抓取指标
            metrics.record_fetch(
                total_sources=len(getattr(fetcher, '_last_sources', [])),
                success=getattr(fetcher, '_last_success_count', 0),
                failed=getattr(fetcher, '_last_failed_count', 0),
                total_news=len(news_items),
                duplicates=getattr(fetcher, '_last_duplicate_count', 0),
                repeats=getattr(fetcher, '_last_repeat_count', 0),
                elapsed=fetch_elapsed,
                failed_names=getattr(fetcher, '_last_failed_names', []),
            )

            # 保存抓取缓存用于增量去重
            _save_fetch_cache(news_items)

        except Exception as e:
            fetch_elapsed = time.time() - fetch_start
            logger.error(f"新闻抓取失败: {type(e).__name__}: {e}")
            state.mark_step("fetch", "failed", str(e))
            state.finish("failed")
            sys.exit(1)
    else:
        logger.info("跳过新闻抓取 (skip_fetch=True)")
        state.mark_step("fetch", "skipped")

    # ─── 3. AI 生成日报 ───
    summary = ""
    summary_elapsed = 0
    if skip_ai:
        logger.info("跳过 AI 分析 (skip_ai=True)")
        summary = mock_summary if mock_summary else _generate_mock_summary(news_items)
        state.mark_step("summary", "skipped")
    else:
        logger.info("[2/6] AI 生成日报...")
        summary_start = time.time()
        try:
            summarizer = NewsSummarizer(
                api_url=env["api_url"],
                api_key=env["api_key"],
                model_name=env["model_name"],
                proxy=env.get("https_proxy", ""),
                ssl_verify=env.get("ssl_verify", True),
                lang=lang,
            )
            summary = summarizer.summarize(news_items)
            summary_elapsed = time.time() - summary_start
            logger.info(f"日报生成完成: {len(summary)} 字符 ({summary_elapsed:.1f}s)")
            state.mark_step("summary", "completed")

            # 缓存摘要用于 fallback
            _save_summary_cache(summary)

            # 记录 AI 指标 (token 数近似：中文约 1.5 char/token)
            est_tokens = len(summary) * 2 // 3
            metrics.record_ai_summary(tokens=est_tokens, elapsed=summary_elapsed)

        except Exception as e:
            summary_elapsed = time.time() - summary_start
            logger.error(f"AI 日报生成失败: {type(e).__name__}: {e}")
            state.mark_step("summary", "failed", str(e))

            # 尝试加载缓存的摘要作为 fallback
            cached = _load_summary_cache()
            if cached:
                logger.info("使用缓存的日报作为 fallback")
                summary = cached
            else:
                logger.warning("无缓存日报可用，使用 mock 数据")
                summary = _generate_mock_summary(news_items)

    # ─── 4. 生成 HTML 报告 ───
    report_path = None
    try:
        logger.info("[3/6] 生成 HTML 报告...")
        generator = ReportGenerator()
        source_count = len(set(n.source for n in news_items)) if news_items else 0
        report_path = generator.generate(
            markdown_content=summary,
            source_count=source_count,
            news_count=len(news_items),
            lang=lang,
        )
        logger.info(f"HTML 报告已生成: {report_path}")
        state.mark_step("html", "completed")

        # 立即打开浏览器（不等待后续步骤）
        if auto_open:
            open_report(report_path)

        # 记录输出指标
        html_size = report_path.stat().st_size if report_path.exists() else 0
        metrics.record_output(html_size=html_size)

    except Exception as e:
        logger.error(f"HTML 报告生成失败: {type(e).__name__}: {e}")
        state.mark_step("html", "failed", str(e))

    # ─── 5. 保存历史数据 ───
    try:
        logger.info("[4/6] 保存历史数据...")
        history_manager = HistoryManager()
        history_manager.save_daily(summary)
        state.mark_step("history", "completed")
    except Exception as e:
        logger.error(f"历史数据保存失败: {type(e).__name__}: {e}")
        state.mark_step("history", "failed", str(e))

    # ─── 6. 结构化提取 + 四层架构更新 ───
    extracted = None
    extraction_elapsed = 0
    if not skip_ai:
        logger.info("[5/6] 结构化提取与情报架构更新...")
        extraction_start = time.time()
        try:
            extractor = StructuredExtractor(
                api_url=env["api_url"],
                api_key=env["api_key"],
                model_name=env["model_name"],
                proxy=env.get("https_proxy", ""),
                ssl_verify=env.get("ssl_verify", True),
                lang=lang,
            )
            extracted = extractor.extract(summary)
            extraction_elapsed = time.time() - extraction_start

            est_ext_tokens = 15000 * 2 // 3  # 输入截断 15000 字符
            metrics.record_ai_extraction(
                tokens=est_ext_tokens,
                elapsed=extraction_elapsed,
                success=extracted is not None,
            )

            if extracted:
                state.mark_step("extraction", "completed")
            else:
                state.mark_step("extraction", "failed", "返回空结果")

        except Exception as e:
            extraction_elapsed = time.time() - extraction_start
            logger.error(f"结构化提取失败: {type(e).__name__}: {e}")
            state.mark_step("extraction", "failed", str(e))
    else:
        logger.info("跳过结构化提取 (skip_ai=True)")
        state.mark_step("extraction", "skipped")

    # ─── 6a. 事件图谱 ───
    if extracted:
        try:
            logger.info("  → 更新事件图谱...")
            event_graph = build_event_graph_from_extraction(extracted, today)
            save_event_graph(event_graph)
            state.mark_step("event_graph", "completed")
            metrics.data["output"]["events_count"] = len(event_graph.events)
        except Exception as e:
            logger.error(f"  → 事件图谱更新失败: {e}")
            state.mark_step("event_graph", "failed", str(e))

        # ─── 6b. 风险评分 ───
        try:
            logger.info("  → 计算风险评分...")
            scorer = RiskScorer()
            risk_scores = []
            for theme_name in scorer.risk_themes:
                gov_signals = extracted.get("government_signals", [])
                fin_signals = extracted.get("financial_signals", [])
                energy_signals = extracted.get("energy_signals", [])
                tt_signals = extracted.get("think_tank_signals", [])

                risk_score = scorer.score_theme(
                    theme=theme_name,
                    news_items=news_items,
                    government_signals=gov_signals,
                    financial_signals=fin_signals,
                    energy_signals=energy_signals,
                    think_tank_signals=tt_signals,
                )
                risk_score.momentum = scorer.calculate_momentum(theme_name, risk_score.score)
                risk_score.acceleration = scorer.calculate_acceleration(theme_name, risk_score.score)
                risk_scores.append(risk_score)

            global_stress = min(100, sum(s.score for s in risk_scores) // max(1, len(risk_scores)))
            risk_report = DailyRiskReport(
                date=today,
                scores=risk_scores,
                global_stress_index=global_stress,
            )
            save_risk_report(risk_report)
            logger.info(f"  → 风险评分完成: 全球压力指数 {global_stress}/100")
            state.mark_step("risk_scoring", "completed")
            metrics.data["output"]["risk_themes_count"] = len(risk_scores)

        except Exception as e:
            logger.error(f"  → 风险评分失败: {e}")
            state.mark_step("risk_scoring", "failed", str(e))

        # ─── 6c. 演化追踪 ───
        try:
            logger.info("  → 更新演化追踪...")
            tracker = EvolutionTracker()
            evolution_report = tracker.update_from_events(event_graph.events, today)
            logger.info(f"  → 演化追踪完成: {evolution_report.active_events} 个活跃事件, "
                        f"{len(evolution_report.phase_transitions_today)} 个阶段跃迁")
            state.mark_step("evolution", "completed")
            metrics.data["output"]["evolution_active_events"] = evolution_report.active_events
            metrics.data["output"]["phase_transitions"] = len(evolution_report.phase_transitions_today)

        except Exception as e:
            logger.error(f"  → 演化追踪失败: {e}")
            state.mark_step("evolution", "failed", str(e))

    else:
        logger.warning("结构化提取失败或跳过，四层架构未更新")
        for step in ["event_graph", "risk_scoring", "evolution"]:
            state.mark_step(step, "skipped")

    # ─── 7. 完成 ───
    pipeline_elapsed = time.time() - pipeline_start

    # 汇总 pipeline 指标
    completed = len(state.data["completed_steps"])
    failed = len(state.data["failed_steps"])
    skipped = len(state.data["skipped_steps"])
    metrics.record_pipeline(pipeline_elapsed, completed, failed, skipped)
    metrics.save()

    # 确定最终状态
    if failed == 0:
        final_status = "completed"
    elif completed > 0:
        final_status = "partial"
    else:
        final_status = "failed"
    state.finish(final_status)

    logger.info("[6/6] 流程完成!")
    logger.info("=" * 60)
    logger.info(f"状态: {final_status} | 耗时: {pipeline_elapsed:.1f}s")
    logger.info(f"步骤: {completed} 完成 / {failed} 失败 / {skipped} 跳过")
    if report_path:
        logger.info(f"报告路径: {report_path}")
    if extracted:
        logger.info(f"事件图谱: data/events/events_{today}.json")
        logger.info(f"风险评分: data/risk/scores_{today}.json")
        logger.info(f"演化追踪: data/evolution/evolution_{today}.json")
    logger.info(f"运行指标: data/metrics/metrics_{today}.json")

    # 免费版提示
    try:
        from src.premium import is_premium_enabled
        if not is_premium_enabled():
            logger.info("")
            logger.info("╔═══════════════════════════════════════════════════════════╗")
            logger.info("║           PrismLens 免费版 - 功能限制提示                 ║")
            logger.info("╠═══════════════════════════════════════════════════════════╣")
            logger.info("║  当前限制：                                               ║")
            logger.info("║  • 新闻源：10个（付费版：101+，持续更新）                 ║")
            logger.info("║  • 日报章节：3章（付费版：10章完整分析）                  ║")
            logger.info("║  • 阵营对比：单阵营（付费版：多阵营对比）                 ║")
            logger.info("║  • 历史回溯：无（付费版：90天）                           ║")
            logger.info("║  • 实时预警：无（付费版：S/A级即时推送）                  ║")
            logger.info("║  • 传导链验证：无（付费版：9条量化传导链）                ║")
            logger.info("║                                                           ║")
            logger.info("║  升级到付费版，请联系：                                   ║")
            logger.info("║  📧 vanthree31@gmail.com                                  ║")
            logger.info("║  💬 微信：Sany1ovo                                        ║")
            logger.info("║  🎮 小黑盒：xiaoheihe.cn/user/ad29685205e7                ║")
            logger.info("║  💬 QQ：1448840796                                        ║")
            logger.info("╚═══════════════════════════════════════════════════════════╝")
    except ImportError:
        pass

    logger.info("=" * 60)

    if report_path is None:
        logger.error("HTML 报告未生成，流程异常结束")
        sys.exit(1)

    return report_path


def open_report(report_path: Path) -> None:
    """在默认浏览器中打开报告"""
    try:
        url = report_path.resolve().as_uri()
        webbrowser.open(url)
        logger.info(f"已在浏览器中打开: {url}")
    except Exception as e:
        logger.warning(f"无法自动打开浏览器: {e}")
        logger.info(f"请手动打开: {report_path}")


# ─────────────────────────────────────────────────
# 缓存工具
# ─────────────────────────────────────────────────

def _save_fetch_cache(news_items: list) -> None:
    """保存抓取缓存（URL hash 集合 + 时间戳）"""
    import hashlib
    from src.utils import normalize_url

    cache_file = get_cache_dir() / "last_fetch.json"
    url_hashes = set()
    for item in news_items:
        normalized = normalize_url(item.url)
        h = hashlib.md5(normalized.encode()).hexdigest()
        url_hashes.add(h)

    data = {
        "date": get_today_str(),
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "url_hashes": list(url_hashes),
        "count": len(url_hashes),
    }
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            import json
            json.dump(data, f)
    except Exception:
        pass


def _save_summary_cache(summary: str) -> None:
    """缓存 AI 生成的摘要用于 fallback"""
    cache_file = get_cache_dir() / "last_summary.md"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(summary)
    except Exception:
        pass


def _load_summary_cache() -> str:
    """加载缓存的摘要"""
    cache_file = get_cache_dir() / "last_summary.md"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return ""


# ─────────────────────────────────────────────────
# 模拟数据 (测试用)
# ─────────────────────────────────────────────────

def _generate_mock_summary(news_items: list) -> str:
    """生成模拟的 AI 总结 (用于测试 HTML 生成)"""
    lines = ["# 今日国际局势简报\n"]
    lines.append("## 〇、今日最高优先级风险\n")
    lines.append("| 风险项 | 信号等级 | 置信度 | 风险来源 | 升级信号 | 涉及国家 | 潜在演化 |")
    lines.append("|--------|----------|--------|----------|----------|----------|----------|")
    lines.append("| 中东航运风险 | A | 高 | 霍尔木兹海峡封锁 | 美军增派护航 | 伊朗、美国 | 能源危机扩大 |")
    lines.append("| AI芯片出口管制 | B | 中 | 美国商务部讨论 | 正式扩大HBM限制 | 美国、中国 | 科技脱钩加速 |\n")

    lines.append("## 一、核心国际事件分析\n")
    lines.append("### 中东航运危机持续\n")
    lines.append("**信号等级**：A\n")
    lines.append("**事件概述**：霍尔木兹海峡封锁持续，全球航运保险费率飙升，多国紧急建设替代管线。\n")

    lines.append("## 二、区域媒体视角\n")
    lines.append("- **Reuters**（发布: 2026-05-16 08:00）：全球关注中东航运中断影响。\n")

    lines.append("## 三、政府与智库信号\n")
    lines.append("- 美国国务院发表声明关注航行自由。（信号等级: A）\n")

    lines.append("## 四、金融风险层\n")
    lines.append("### 4.1 全球金融市场信号\n")
    lines.append("- 债券市场持续承压，收益率上行。\n")

    lines.append("## 五、科技战层\n")
    lines.append("### 5.1 半导体与AI芯片\n")
    lines.append("- 美国讨论扩大HBM出口管制范围。\n")

    lines.append("## 六、事件聚类\n")
    lines.append("- **中东航运危机**：霍尔木兹封锁、油价飙升、替代管线建设属于同一事件。\n")

    lines.append("## 七、长期趋势分析与信号变化\n")
    lines.append("### 7.1 事件演化分析\n")
    lines.append("- 中东局势从外交阶段升级至军事阶段。\n")

    lines.append("## 八、A股投资研判\n")
    lines.append("### 8.1 核心影响路径\n")
    lines.append("中东航运中断 → 油价上行 → 能源板块受益。\n")

    lines.append("## 九、原始新闻链接\n")
    if news_items:
        for item in news_items[:10]:
            lines.append(f"- {item.source} - {item.title} [阅读原文]({item.url})")

    return "\n".join(lines)
