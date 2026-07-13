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

import json
import logging
import re
import sys
import time
import webbrowser
from pathlib import Path

from src.event_graph import save_event_graph
from src.evolution_tracker import EvolutionTracker
from src.fetcher import NewsFetcher
from src.generator import ReportGenerator
from src.history_manager import HistoryManager
from src.risk_scorer import DailyRiskReport, RiskScorer, _find_event_first_seen, save_risk_report
from src.structured_extractor import StructuredExtractor, build_event_graph_from_extraction
from src.summarizer import NewsSummarizer
from src.summarizer_v3 import NewsSummarizerV3
from src.utils import (
    MetricsCollector,
    RunState,
    ensure_directories,
    get_cache_dir,
    get_data_dir,
    get_output_dir,
    get_today_str,
    load_env,
    setup_logging,
)

logger = logging.getLogger("global_news.main")

# 延迟导入：市场数据（可能不可用）
_market_provider = None


def _get_market_provider():
    """延迟加载市场数据提供器，避免 yfinance 不可用时导入失败"""
    global _market_provider
    if _market_provider is None:
        try:
            from src.market_data import get_market_provider

            _market_provider = get_market_provider()
        except ImportError:
            logger.warning("market_data 模块不可用，传导链验证将跳过")
            _market_provider = False  # 标记为不可用，避免重复尝试
    return _market_provider if _market_provider is not False else None


def _get_market_data_for_chains() -> dict:
    """获取传导链验证所需的市场数据字典"""
    provider = _get_market_provider()
    if provider is None:
        return {}
    try:
        summary = provider.get_market_summary()
        if "暂未接入" in summary or "获取失败" in summary:
            return {}
        # 从 provider 的缓存中提取原始数据
        return provider._cache if provider._cache else {}
    except Exception as e:
        logger.warning(f"获取传导链市场数据失败: {e}")
        return {}


# 输出文件保留天数
OUTPUT_RETENTION_DAYS = 7


# ─────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────


def _cleanup_old_output(retention_days: int = OUTPUT_RETENTION_DAYS) -> int:
    """清理超过保留天数的旧输出文件，返回删除数量"""
    from datetime import datetime

    output_dir = get_output_dir()
    if not output_dir.exists():
        return 0

    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed = 0
    for f in output_dir.glob("*.html"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except Exception:
            pass
    if removed:
        logger.info(f"  → 清理旧报告: 删除 {removed} 个超过 {retention_days} 天的文件")
    return removed


def run(
    skip_fetch: bool = False,
    skip_ai: bool = False,
    mock_summary: str = "",
    max_news: int = 500,
    auto_open: bool = False,
    lang: str = "zh",
    pipeline: str = "v3",
) -> Path:
    """执行完整的新闻简报生成流程，支持断步容错

    Args:
        skip_fetch: 跳过新闻抓取步骤（使用上次缓存数据）
        skip_ai: 跳过 AI 分析步骤（使用 mock 或缓存摘要）
        mock_summary: 自定义 mock 摘要内容（仅 skip_ai=True 时生效）
        max_news: 最大抓取新闻数量，默认 500
        auto_open: 生成后自动在浏览器中打开报告
        lang: 语言，'zh' 中文 / 'en' 英文
        pipeline: Pipeline 版本，'v2'=三阶段（旧）, 'v3'=单阶段（新·默认）, 'shadow'=v2+v3对比

    Returns:
        生成的 HTML 报告文件路径

    Raises:
        SystemExit: 当新闻抓取失败或 HTML 报告未生成时退出
    """
    pipeline_start = time.time()

    # ─── 1. 初始化 ───
    ensure_directories()
    setup_logging()  # 配置 root logger，模块级 logger (global_news.main) 自动继承 handlers

    logger.info("正在加载配置...")
    _cleanup_old_output()

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

    # 重置 premium 模块缓存（确保 .env 加载后重新检查）
    try:
        import src.premium as _premium

        _premium._PREMIUM_ENABLED = None
    except ImportError:
        pass

    today = get_today_str()

    # ─── 2. 抓取新闻 ───
    news_items = []
    fetch_elapsed = 0
    if not skip_fetch:
        logger.info("[1/6] 抓取全球新闻...")
        logger.info("  → 初始化新闻源连接...")
        fetch_start = time.time()
        try:
            fetcher = NewsFetcher(
                proxy=env.get("https_proxy", ""),
                ssl_verify=env.get("ssl_verify", True),
            )
            news_items = fetcher.fetch_all(max_news=max_news)
            fetch_elapsed = time.time() - fetch_start

            if not news_items:
                logger.warning(
                    "未获取到任何新闻，流程终止。"
                    "可能原因：网络连接问题、代理配置错误、所有新闻源不可达。"
                    "请检查 .env 中的 HTTPS_PROXY 设置，或使用 --skip-fetch 跳过抓取步骤。"
                )
                state.mark_step("fetch", "failed", "无新闻")
                state.finish("failed")
                sys.exit(1)

            # 数据抓取健康检查
            source_count = len({n.source for n in news_items})
            if source_count < 5:
                logger.error(
                    f"数据抓取健康检查失败：仅覆盖 {source_count} 个新闻源，不满足最低 5 源要求。"
                    f"流程终止。请检查 sources.yaml 配置和网络连接。"
                )
                state.mark_step("fetch", "failed", f"源数不足({source_count}<5)")
                state.finish("failed")
                sys.exit(1)

            logger.info(
                f"抓取完成: {len(news_items)} 条新闻, {source_count} 个源 ({fetch_elapsed:.1f}s)"
            )
            state.mark_step("fetch", "completed")

            # 记录抓取指标
            _last_total = getattr(fetcher, "_last_sources", [])
            _last_total_count = len(_last_total) if isinstance(_last_total, list) else 0
            metrics.record_fetch(
                total_sources=_last_total_count,
                success=getattr(fetcher, "_last_success_count", 0),
                failed=getattr(fetcher, "_last_failed_count", 0),
                total_news=len(news_items),
                duplicates=getattr(fetcher, "_last_duplicate_count", 0),
                repeats=getattr(fetcher, "_last_repeat_count", 0),
                elapsed=fetch_elapsed,
                failed_names=getattr(fetcher, "_last_failed_names", []),
            )

            # 保存抓取缓存用于增量去重
            _save_fetch_cache(news_items)

        except Exception as e:
            fetch_elapsed = time.time() - fetch_start
            logger.error(f"[step:fetch] 新闻抓取失败: {e}", exc_info=True)
            state.mark_step("fetch", "failed", str(e))
            state.finish("failed")
            sys.exit(1)
    else:
        logger.info("跳过新闻抓取 (skip_fetch=True)")
        state.mark_step("fetch", "skipped")

    # ─── 3. AI 生成日报 ───
    summary = ""
    summary_elapsed = 0
    v3_result = None  # v3 pipeline 的结构化输出
    if skip_ai:
        logger.info("跳过 AI 分析 (skip_ai=True)")
        summary = mock_summary if mock_summary else _generate_mock_summary(news_items)
        state.mark_step("summary", "skipped")
    elif pipeline == "v3":
        # ─── v3: 单阶段 Pipeline ───
        logger.info(f"[2/6] AI 生成日报 (v3 单阶段, {len(news_items)}条新闻)...")
        summary_start = time.time()
        try:
            summarizer_v3 = NewsSummarizerV3(
                api_url=env["api_url"],
                api_key=env["api_key"],
                model_name=env["model_name"],
                proxy=env.get("https_proxy", ""),
                ssl_verify=env.get("ssl_verify", True),
                lang=lang,
            )
            v3_result = summarizer_v3.summarize(news_items)
            summary = v3_result.get("markdown", "")
            summary_elapsed = v3_result.get("elapsed", time.time() - summary_start)

            if not summary:
                logger.warning("v3 返回空日报，回退到 v2")
                pipeline_actual = "v2"
            else:
                pipeline_actual = "v3"
                logger.info(
                    f"v3 日报生成完成: {len(summary)} 字符 ({summary_elapsed:.0f}s), "
                    f"events={len(v3_result.get('structured_data', {}).get('events', []) or [])}"
                )
                state.mark_step("summary", "completed")
                _save_summary_cache(summary)

                est_tokens = len(summary) * 2 // 3
                metrics.record_ai_summary(tokens=est_tokens, elapsed=summary_elapsed)

        except Exception as e:
            summary_elapsed = time.time() - summary_start
            logger.error(f"[step:summary] v3 日报生成失败: {e}", exc_info=True)
            logger.warning("v3 失败，尝试 v2 回退...")
            pipeline_actual = "v2"
    else:
        # ─── v2: 三阶段 Pipeline（保留为回退） ───
        pipeline_actual = "v2"

    if pipeline_actual == "v2":
        logger.info("[2/6] AI 生成日报 (v2 三阶段)...")
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
            logger.error(f"[step:summary] AI 日报生成失败: {e}", exc_info=True)
            state.mark_step("summary", "failed", str(e))

            # 尝试加载缓存的摘要作为 fallback
            cached = _load_summary_cache()
            if cached:
                logger.info("使用缓存的日报作为 fallback")
                summary = cached
            else:
                logger.warning("无缓存日报可用，使用 mock 数据")
                summary = _generate_mock_summary(news_items)

    # ─── 3.5 后处理：注入真实市场数据、新闻链接、区域覆盖检查 ───
    summary = _inject_market_data(summary, lang)
    if news_items:
        summary = _inject_news_links(summary, news_items, lang)
    summary = _enforce_regional_balance(summary, news_items, lang)

    # ─── 3.6 QualityChecker 程序化校验 ───
    try:
        from src.quality_checker import QualityChecker

        qc = QualityChecker(lang=lang)
        qc_result = qc.check_all(summary, len(news_items))
        if not qc_result["passed"]:
            logger.warning(f"QualityChecker: {qc_result['summary']}")
            for issue in qc_result["issues"]:
                logger.warning(f"  [{issue['category']}] {issue['message']}")
        for w in qc_result.get("warnings", []):
            logger.info(f"  ⚠ [{w['category']}] {w['message']}")
        if qc_result["passed"]:
            logger.info(f"QualityChecker: {qc_result['summary']}")
    except ImportError:
        pass

    # ─── 4. 生成 HTML 报告 ───
    report_path = None
    try:
        logger.info("[3/6] 生成 HTML 报告...")
        generator = ReportGenerator()
        source_count = len({n.source for n in news_items}) if news_items else 0
        # 检查是否为付费版
        try:
            from src.premium import is_premium_enabled

            is_premium = is_premium_enabled()
        except ImportError:
            is_premium = False
        report_path = generator.generate(
            markdown_content=summary,
            source_count=source_count,
            news_count=len(news_items),
            lang=lang,
            is_premium=is_premium,
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
        logger.error(f"[step:html] HTML 报告生成失败: {e}", exc_info=True)
        state.mark_step("html", "failed", str(e))

    # ─── 5. 保存历史数据 ───
    try:
        logger.info("[4/6] 保存历史数据...")
        history_manager = HistoryManager()
        history_manager.save_daily(summary)
        state.mark_step("history", "completed")
    except Exception as e:
        logger.error(f"[step:history] 历史数据保存失败: {e}", exc_info=True)
        state.mark_step("history", "failed", str(e))

    # ─── 6. 结构化提取 + 四层架构更新 ───
    extracted = None
    extraction_elapsed = 0

    # v3: 如果 v3 已经输出了嵌入式 JSON，直接使用，无需额外 API 调用
    if v3_result and v3_result.get("structured_data"):
        logger.info("[5/6] v3 已输出嵌入式 JSON，跳过 Stage 0 API 调用")
        extracted = v3_result["structured_data"]
        state.mark_step("extraction", "completed")

        # 保存 v3 的结构化输出
        try:
            sd_path = get_data_dir() / "events" / f"events_{today}_v3.json"
            sd_path.write_text(
                json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        # 保存引用数据
        if v3_result.get("citations"):
            try:
                cite_path = get_data_dir() / "citations" / f"citations_{today}.json"
                cite_path.parent.mkdir(parents=True, exist_ok=True)
                cite_path.write_text(
                    json.dumps(v3_result["citations"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass
    elif not skip_ai:
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
            logger.error(f"[step:extraction] 结构化提取失败: {e}", exc_info=True)
            state.mark_step("extraction", "failed", str(e))
    else:
        logger.info("跳过结构化提取 (skip_ai=True)")
        state.mark_step("extraction", "skipped")

    # ─── 6a. 事件图谱 ───
    event_graph = None
    if extracted:
        try:
            logger.info("  → 更新事件图谱...")
            event_graph = build_event_graph_from_extraction(extracted, today)
            save_event_graph(event_graph)
            state.mark_step("event_graph", "completed")
            metrics.data["output"]["events_count"] = len(event_graph.events)
        except Exception as e:
            logger.error(f"[step:event_graph] 事件图谱更新失败: {e}", exc_info=True)
            state.mark_step("event_graph", "failed", str(e))

        # ─── 6b. 风险评分 ───
        try:
            logger.info("  → 计算风险评分...")
            scorer = RiskScorer()
            risk_scores = []

            # 从所有事件中计算多数 source_lean，避免仅取第一个事件的偏差
            _events = extracted.get("events", [])
            if _events:
                from collections import Counter

                lean_counter = Counter(e.get("source_lean", "中立") for e in _events)
                source_lean = lean_counter.most_common(1)[0][0]
            else:
                source_lean = "中立"

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
                    source_lean=source_lean,
                    first_seen_date=_find_event_first_seen(theme_name, news_items),
                )
                risk_score.momentum = scorer.calculate_momentum(theme_name, risk_score.score)
                risk_score.acceleration = scorer.calculate_acceleration(
                    theme_name, risk_score.score
                )
                risk_score.trend = RiskScorer.determine_trend(risk_score.momentum)
                risk_scores.append(risk_score)

            # 使用传导链验证计算全球压力指数
            market_data = _get_market_data_for_chains()
            global_stress, chain_validations = scorer.calculate_global_stress_with_chains(
                risk_scores, market_data
            )
            if chain_validations:
                chain_names = [c["chain_name"] for c in chain_validations]
                total_bonus = sum(c["bonus"] for c in chain_validations)
                logger.info(
                    f"  → 传导链验证: {len(chain_validations)}条触发 "
                    f"({', '.join(chain_names)}), 加成+{total_bonus}"
                )
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
            logger.error(f"[step:risk_scoring] 风险评分失败: {e}", exc_info=True)
            state.mark_step("risk_scoring", "failed", str(e))

        # ─── 6c. 演化追踪 ───
        try:
            logger.info("  → 更新演化追踪...")
            if event_graph is None:
                logger.warning("事件图谱未生成，跳过演化追踪")
                state.mark_step("evolution", "skipped", "事件图谱不可用")
            else:
                tracker = EvolutionTracker()
                evolution_report = tracker.update_from_events(event_graph.events, today)
                logger.info(
                    f"  → 演化追踪完成: {evolution_report.active_events} 个活跃事件, "
                    f"{len(evolution_report.phase_transitions_today)} 个阶段跃迁"
                )
                state.mark_step("evolution", "completed")
                metrics.data["output"]["evolution_active_events"] = evolution_report.active_events
                metrics.data["output"]["phase_transitions"] = len(
                    evolution_report.phase_transitions_today
                )

        except Exception as e:
            logger.error(f"[step:evolution] 演化追踪失败: {e}", exc_info=True)
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
            if lang == "zh":
                logger.info("")
                logger.info(
                    "╔═══════════════════════════════════════════════╗\n"
                    "║     PrismLens 免费版 - 功能限制提示           ║\n"
                    "╠═══════════════════════════════════════════════╣\n"
                    "║  当前限制：                                   ║\n"
                    "║  · 新闻源: 10个（付费版: 101+，持续更新）    ║\n"
                    "║  · 日报章节: 3章（付费版: 10章完整分析）      ║\n"
                    "║  · 阵营对比: 单阵营（付费版: 多阵营对比）     ║\n"
                    "║  · 历史回溯: 无（付费版: 90天）               ║\n"
                    "║  · 实时预警: 无（付费版: S/A级即时推送）      ║\n"
                    "║  · 传导链验证: 无（付费版: 9条量化传导链）    ║\n"
                    "║                                               ║\n"
                    "║  升级到付费版，请联系：                       ║\n"
                    "║  vanthree31@gmail.com                         ║\n"
                    "║  微信: Sany1ovo  |  QQ: 1448840796           ║\n"
                    "╚═══════════════════════════════════════════════╝"
                )
            else:
                logger.info("")
                logger.info(
                    "╔═══════════════════════════════════════════════╗\n"
                    "║     PrismLens Free - Feature Limits           ║\n"
                    "╠═══════════════════════════════════════════════╣\n"
                    "║  Current limits:                              ║\n"
                    "║  · Sources: 10 (Premium: 101+)                ║\n"
                    "║  · Chapters: 3 (Premium: 10)                  ║\n"
                    "║  · Camp comparison: single (Premium: multi)   ║\n"
                    "║  · History: none (Premium: 90 days)           ║\n"
                    "║  · Alerts: none (Premium: S/A instant)        ║\n"
                    "║  · Chain validation: none (Premium: 9 chains) ║\n"
                    "║                                               ║\n"
                    "║  Contact for premium:                         ║\n"
                    "║  vanthree31@gmail.com                         ║\n"
                    "╚═══════════════════════════════════════════════╝"
                )
    except ImportError:
        logger.debug("premium 模块不可用，使用免费版行为")

    # ─── 遥测记录 ───
    try:
        from src.telemetry import PipelineTelemetry, Telemetry, estimate_cost

        t = PipelineTelemetry(
            run_id=state.run_id,
            date=today,
            pipeline_version=pipeline,
            started_at=state.data.get("started_at", ""),
            fetch_seconds=fetch_elapsed,
            ai_seconds=summary_elapsed,
            total_seconds=pipeline_elapsed,
            api_calls=1 if pipeline == "v3" else 4,
            news_count=len(news_items),
            source_count=len({n.source for n in news_items}) if news_items else 0,
            source_success_rate=(
                metrics.data["fetch"]["success_sources"] / max(metrics.data["fetch"]["total_sources"], 1)
                if metrics.data["fetch"]["total_sources"] > 0 else 0
            ),
            output_chars=len(summary),
            json_parse_success=v3_result is not None and v3_result.get("structured_data") is not None,
            citation_density=(
                v3_result.get("citations", {}).get("citation_density", 0)
                if v3_result and v3_result.get("citations") else 0
            ),
            citation_count=(
                len(v3_result.get("citations", {}).get("claims", []))
                if v3_result and v3_result.get("citations") else 0
            ),
        )
        # 从 v3 structured_data 提取更多质量指标
        if v3_result and v3_result.get("structured_data"):
            sd = v3_result["structured_data"]
            t.events_count = len(sd.get("events", []))
            regions_set = set()
            for e in sd.get("events", []):
                if isinstance(e, dict) and e.get("region"):
                    regions_set.add(e["region"])
            t.regions_covered = len(regions_set)
            t.region_names = list(regions_set)
            t.s_level_count = sum(1 for e in sd.get("events", []) if isinstance(e, dict) and e.get("signal_level") == "S")
            t.a_level_count = sum(1 for e in sd.get("events", []) if isinstance(e, dict) and e.get("signal_level") == "A")
        if v3_result and v3_result.get("metadata"):
            t.quality_self_assessment = v3_result["metadata"].get("quality_self_assessment", "")
            t.hallucination_risk = v3_result["metadata"].get("hallucination_risk", "")
        # 估算成本
        t.estimated_cost_usd = estimate_cost(t.input_tokens, t.output_tokens)

        telemetry = Telemetry()
        telemetry.save(t)
        logger.info(f"  遥测已记录: quality_score={t.quality_score()}/100")

        # 生产指标自动记录 (data/runs.db)
        try:
            from src.production_metrics import RunRecord, record

            run_record = RunRecord(
                run_id=state.run_id,
                pipeline=pipeline,
                date=today,
                started_at=state.data.get("started_at", ""),
                fetch_seconds=fetch_elapsed,
                ai_seconds=summary_elapsed,
                total_seconds=pipeline_elapsed,
                input_tokens=t.input_tokens,
                output_tokens=t.output_tokens,
                estimated_cost=t.estimated_cost_usd,
                news_count=len(news_items),
                source_count=len({n.source for n in news_items}) if news_items else 0,
                source_success_rate=(
                    metrics.data["fetch"]["success_sources"] / max(metrics.data["fetch"]["total_sources"], 1)
                    if metrics.data["fetch"]["total_sources"] > 0 else 0
                ),
                output_chars=len(summary),
                h2_chapters=t.h2_chapters,
                events_count=t.events_count,
                regions_covered=t.regions_covered,
                json_valid=t.json_parse_success,
                html_generated=report_path is not None,
                finish_reason=t.finish_reason if hasattr(t, 'finish_reason') else "",
                quality_score=t.quality_score(),
                placeholder_count=t.placeholder_count,
                had_fallback=(
                    bool(getattr(summarizer_v3, '_fallback_used', False))
                    if v3_result else False
                ),
                had_error=(report_path is None),
            )
            record(run_record)
            logger.info("  生产指标已写入 data/runs.db")
        except Exception as e:
            logger.debug(f"  生产指标记录失败: {e}")

        # Shadow Run: 如果 pipeline=shadow，额外跑 v2 并对比
        if pipeline == "shadow":
            logger.info("  [Shadow] 运行 v2 对比...")
            try:
                v2_start = time.time()
                v2_summarizer = NewsSummarizer(
                    api_url=env["api_url"], api_key=env["api_key"],
                    model_name=env["model_name"],
                    proxy=env.get("https_proxy", ""),
                    ssl_verify=env.get("ssl_verify", True), lang=lang,
                )
                v2_summary = v2_summarizer.summarize(news_items)
                v2_elapsed = time.time() - v2_start

                v2_t = PipelineTelemetry(
                    run_id=f"{state.run_id}_v2", date=today, pipeline_version="v2",
                    total_seconds=v2_elapsed, api_calls=3,
                    news_count=len(news_items),
                    output_chars=len(v2_summary),
                )
                telemetry.save(v2_t)
                comparison = telemetry.compare_pipelines(today)
                if comparison:
                    logger.info(
                        f"  [Shadow] v2={comparison['v2']['quality_score']} vs "
                        f"v3={comparison['v3']['quality_score']}, "
                        f"regression={'YES ⚠️' if comparison['regression'] else 'no'}"
                    )
                    # 保存对比报告
                    shadow_dir = Path(get_output_dir()) / "shadow"
                    shadow_dir.mkdir(parents=True, exist_ok=True)
                    (shadow_dir / f"comparison_{today}.json").write_text(
                        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception as e:
                logger.warning(f"  [Shadow] v2 运行失败: {e}")
    except ImportError:
        logger.debug("遥测模块不可用，跳过记录")

    logger.info("=" * 60)

    if report_path is None:
        logger.error(
            "HTML 报告未生成，流程异常结束。"
            "请检查: 1) templates/ 目录是否完整; 2) summary 内容是否为空; "
            "3) 查看上方是否有 HTML 报告生成失败的错误日志。"
        )
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
        logger.info(f"请手动打开文件: {report_path.name} (路径: {report_path.resolve()})")


# ─────────────────────────────────────────────────
# 缓存工具
# ─────────────────────────────────────────────────


def _save_fetch_cache(news_items: list) -> None:
    """保存抓取缓存（URL hash + 内容指纹 + 时间戳），原子写入"""
    import hashlib
    import json
    import os
    import tempfile

    from src.utils import normalize_url

    cache_file = get_cache_dir() / "last_fetch.json"
    url_hashes = set()
    for item in news_items:
        normalized = normalize_url(item.url)
        url_hashes.add(hashlib.md5(normalized.encode()).hexdigest())

    data = {
        "date": get_today_str(),
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "url_hashes": list(url_hashes),
        "count": len(url_hashes),
    }
    try:
        # 原子写入：先写临时文件再 rename
        fd, tmp_path = tempfile.mkstemp(dir=str(cache_file.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(cache_file))
        except Exception:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.warning(f"保存抓取缓存失败: {e}")


def _inject_market_data(summary: str, lang: str = "zh") -> str:
    """将真实市场数据注入日报 markdown，替换 AI 生成的占位符"""
    try:
        from src.market_data import get_market_provider

        market_text = get_market_provider().get_data_for_prompt()
        if not market_text or "暂无" in market_text:
            return summary
    except Exception:
        return summary

    # 解析 market_text 生成 markdown 表格
    lines = []
    label_price = "最新价" if lang == "zh" else "Price"
    label_change = "涨跌(vs昨收)" if lang == "zh" else "Change(vs Prev)"
    label_trend = "趋势" if lang == "zh" else "Trend"
    label_time = "数据时间" if lang == "zh" else "Data Time"

    lines.append(f"| 指标 | {label_price} | {label_change} | {label_trend} | {label_time} |")
    lines.append("|------|------|------|------|------|")

    for line in market_text.split("\n"):
        if line.startswith("- "):
            # 新格式: "- 标普500: 7575.39 点 (+0.42% vs昨收) ↑ [07-11 16:00 UTC]"
            # 旧格式兼容: "- 美元指数: 100.97 (+0.03% vs昨收) ↑"
            match = re.match(
                r"- (.+?): ([\d.,]+)\s*(\S*)\s*\(([+-].+?)%\s*vs昨收\)\s*(\S+)(?:\s*\[(.+?)\])?$",
                line.strip(),
            )
            if match:
                name, price, unit, change, trend, dt = match.groups()
                price_display = f"{price} {unit}".strip() if unit else price
                trend_icon = "↑" if "+" in change else "↓"
                time_display = dt if dt else "—"
                lines.append(
                    f"| {name} | {price_display} | {change}% | {trend_icon} | {time_display} |"
                )

    if len(lines) <= 2:
        return summary

    market_table = "\n".join(lines)

    # 查找并替换 AI 生成的市场数据表（根据语言选择匹配模式）
    if lang == "zh":
        table_pattern = r"\| 指标.*?\|[\s\S]*?(?=\n\n|\n##|\Z)"
        section_pattern = r"(#{2,4}\s*[四4][、.\s].*金融.*)"
        section_log = "已追加市场数据表到金融章节"
    else:
        table_pattern = r"\| Indicator.*?\|[\s\S]*?(?=\n\n|\n##|\Z)"
        section_pattern = r"(#{2,4}\s*(?:IV|iv|4)[、.\s].*Financial.*)"
        section_log = "Market data table appended to Financial chapter"
    if re.search(table_pattern, summary):
        summary = re.sub(table_pattern, market_table, summary, count=1)
        logger.info("  已注入真实市场数据表")
    else:
        if re.search(section_pattern, summary):
            summary = re.sub(
                section_pattern,
                r"\1\n\n" + market_table + "\n",
                summary,
                count=1,
            )
            logger.info(f"  {section_log}")

    return summary


def _inject_news_links(summary: str, news_items: list, lang: str = "zh") -> str:
    """将真实新闻链接注入日报，替换 AI 生成的占位符链接"""
    if not news_items:
        logger.debug("新闻链接注入跳过: news_items 为空")
        return summary

    valid_count = 0
    skipped_count = 0
    by_region: dict[str, list] = {}
    for n in news_items:
        url = getattr(n, "url", "")
        if not url:
            skipped_count += 1
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            skipped_count += 1
            continue
        valid_count += 1
        region = getattr(n, "region", "其他")
        by_region.setdefault(region, []).append(n)

    logger.info(
        f"  新闻链接处理: {valid_count} 条有效, {skipped_count} 条跳过 "
        f"(共 {len(news_items)} 条新闻)"
    )

    lines = []
    read_label = "阅读原文" if lang == "zh" else "Read original"
    for region, items in sorted(by_region.items()):
        lines.append(f"\n**{region}：**")
        for item in items[:5]:  # 每区域最多5条
            source = getattr(item, "source", "未知")
            title = getattr(item, "title", "")
            url = getattr(item, "url", "")
            lines.append(f"- {source} - {title} [{read_label}]({url})")

    links_section = "\n".join(lines)

    # 替换 AI 生成的原始新闻链接章节
    # 使用章节编号模式匹配（更鲁棒，不受编码影响）
    if not lines:
        return summary

    # 匹配 "## 十、" 或 "## 10." 或 "## X." 形式的最后章节标题
    if lang == "zh":
        section_patterns = [
            r"## 十[、.\s]",  # "## 十、"
            r"## 10[.、\s]",  # "## 10."
        ]
    else:
        section_patterns = [
            r"## X[.、\s]",
            r"## 10[.、\s]",
        ]

    replaced = False
    for section_pat in section_patterns:
        pattern = rf"({section_pat}[^\n]*\n)[\s\S]*"
        if re.search(pattern, summary):
            summary = re.sub(pattern, r"\1\n" + links_section + "\n", summary, count=1)
            total_links = sum(len(v) for v in by_region.values())
            logger.info(f"  已注入 {total_links} 条真实新闻链接（{len(by_region)} 个区域）")
            replaced = True
            break

    if not replaced:
        logger.warning("  新闻链接注入未找到匹配章节标题")

    return summary


def _enforce_regional_balance(summary: str, news_items: list, lang: str = "zh") -> str:
    """检查并标注区域覆盖缺口"""
    if not news_items or not summary:
        return summary

    # 区域名映射：中文 → 英文
    _REGION_EN: dict[str, str] = {
        "北美": "North America",
        "欧洲": "Europe",
        "中东": "Middle East",
        "亚洲": "Asia",
        "中国": "China",
        "俄罗斯": "Russia",
        "南美": "South America",
        "非洲": "Africa",
        "大洋洲": "Oceania",
        "中亚": "Central Asia",
        "东南亚": "Southeast Asia",
        "南亚": "South Asia",
    }

    if lang == "zh":
        all_regions = set(_REGION_EN.keys())
    else:
        all_regions = set(_REGION_EN.values())

    # 统计实际覆盖区域（新闻源配置使用中文区域名）
    covered = set()
    for n in news_items:
        region = getattr(n, "region", "")
        if not region:
            continue
        if lang == "zh":
            covered.add(region)
        else:
            covered.add(_REGION_EN.get(region, region))

    # 检查日报中已提及的区域
    mentioned = set()
    for region in all_regions:
        if region in summary:
            mentioned.add(region)

    # 有新闻但日报未提及的区域
    missing = covered - mentioned
    # 过滤掉未知和无效区域
    if lang == "zh":
        missing.discard("未知")
    else:
        missing.discard("Unknown")

    if not missing:
        return summary

    note_label = "区域覆盖提醒" if lang == "zh" else "Regional Coverage Note"
    missing_list = "、".join(sorted(missing))
    note = (
        f"\n\n---\n\n**{note_label}**：以下区域在今日新闻源中有覆盖"
        f"但日报正文未充分提及：{missing_list}。建议在下次生成时关注这些区域。\n"
    )

    logger.info(
        f"  区域覆盖检查: 已提及 {len(mentioned)} 个区域, 缺失 {len(missing)} 个: {missing_list}"
    )
    return summary + note


def _save_summary_cache(summary: str) -> None:
    """缓存 AI 生成的摘要用于 fallback"""
    cache_file = get_cache_dir() / "last_summary.md"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(summary)
    except Exception as e:
        logger.warning(f"保存摘要缓存失败: {e}")


def _load_summary_cache() -> str:
    """加载缓存的摘要"""
    cache_file = get_cache_dir() / "last_summary.md"
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"加载摘要缓存失败: {e}")
    return ""


# ─────────────────────────────────────────────────
# 模拟数据 (测试用)
# ─────────────────────────────────────────────────


def _generate_mock_summary(news_items: list) -> str:
    """生成模拟的 AI 总结 (用于测试 HTML 生成)

    免费版限制3章，付费版10章完整。
    """
    # 检查版本限制
    try:
        from src.premium import get_chapter_limit

        chapter_limit = get_chapter_limit()
    except ImportError:
        chapter_limit = 3

    lines = ["# 今日国际局势简报\n"]
    lines.append("## 〇、今日最高优先级风险\n")
    lines.append("| 风险项 | 信号等级 | 置信度 | 风险来源 | 升级信号 | 涉及国家 | 潜在演化 |")
    lines.append("|--------|----------|--------|----------|----------|----------|----------|")
    lines.append(
        "| 中东航运风险 | A | 高 | 霍尔木兹海峡封锁 | 美军增派护航 | 伊朗、美国 | 能源危机扩大 |"
    )
    lines.append(
        "| AI芯片出口管制 | B | 中 | 美国商务部讨论 | 正式扩大HBM限制 | 美国、中国 | 科技脱钩加速 |\n"
    )

    lines.append("## 一、核心国际事件分析\n")
    lines.append("### 中东航运危机持续\n")
    lines.append("**信号等级**：A\n")
    lines.append(
        "**事件概述**：霍尔木兹海峡封锁持续，全球航运保险费率飙升，多国紧急建设替代管线。\n"
    )

    lines.append("## 二、区域媒体视角\n")
    lines.append("- **Reuters**（发布: 2026-05-16 08:00）：全球关注中东航运中断影响。\n")

    # 免费版到此为止（3章：〇、一、二）
    if chapter_limit <= 3:
        return "\n".join(lines)

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
