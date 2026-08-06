"""
HTML 产物结构校验模块

对生成的日报 HTML 做结构断言，检测"设计的功能是否真的出现在产物里"。

与 QualityChecker 互补：
- QualityChecker 校验 markdown 文本内容（占位符、空泛词、章节数）
- 本模块校验 HTML 产物结构（观察哨卡片、风险矩阵、市场数据表、信号 badge）

背景：观察哨卡片 07-08 设计、28 天后才真正生效，风险卡片从未生成——
这类静默失效在质量评分和 Release Gate 上毫无痕迹。本模块将静默失效变为显式失败。
"""

import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("global_news.html_validator")

# 中英文观察哨标题（允许括号后缀如"（3-5个）"）
WATCH_TITLE_ZH = r"未来48小时关键观察哨"
WATCH_TITLE_EN = r"48-Hour Key Watchpoints"
# 风险矩阵标题
RISK_TITLE_ZH = r"最高优先级风险"
RISK_TITLE_EN = r"Top Priority Risks"
# 市场数据表标题（07-30 前为"核心市场数据"，之后为"全量市场数据"）
MARKET_TITLE_ZH = r"全量市场数据|核心市场数据"
MARKET_TITLE_EN = r"Full Market Data|Market Data"


def detect_lang(html: str) -> str:
    """根据标题关键词自动检测日报语言"""
    if re.search(WATCH_TITLE_EN, html):
        return "en"
    return "zh"


def _table_row_count(segment: str) -> int:
    """统计表格行数（<tr> 数量）"""
    return len(re.findall(r"<tr>", segment))


def validate_report_structure(html: str, lang: str | None = None) -> dict:
    """
    对日报 HTML 做结构断言。

    参数:
        html: 日报 HTML 全文
        lang: 日报语言（zh/en），None 时自动检测

    返回:
        {
            "passed": bool,
            "issues": [{"check": str, "ok": bool, "detail": str}, ...],
            "summary": str,
        }
    """
    if lang is None:
        lang = detect_lang(html)
    is_en = lang == "en"

    watch_title = WATCH_TITLE_EN if is_en else WATCH_TITLE_ZH
    risk_title = RISK_TITLE_EN if is_en else RISK_TITLE_ZH
    market_title = MARKET_TITLE_EN if is_en else MARKET_TITLE_ZH

    issues = []

    # ─── 1. 章节完整性：h2 数量 ≥ 8 ───
    h2_count = len(re.findall(r'<h2[^>]*class="chapter-title"', html))
    ok = h2_count >= 8
    issues.append(
        {
            "check": "章节完整性",
            "ok": ok,
            "detail": f"h2 章节数 = {h2_count}（要求 ≥ 8）",
        }
    )

    # ─── 2. 观察哨：卡片网格 ≥3 张，或表格（历史合法格式） ───
    watch_cards = len(re.findall(r'<div class="watch-card">', html))
    watch_idx = re.search(rf"<h[34][^>]*>{watch_title}[^<]*</h[34]>", html)
    watch_is_table = False
    if watch_idx:
        seg = html[watch_idx.end() : watch_idx.end() + 800]
        watch_is_table = "<table>" in seg and _table_row_count(seg[:800]) >= 3
    ok = watch_cards >= 3 or watch_is_table
    detail = (
        f"卡片 = {watch_cards} 张"
        if watch_cards >= 3
        else (
            "观察哨为表格（合法历史格式）" if watch_is_table else "观察哨未转换（既无卡片也无表格）"
        )
    )
    issues.append(
        {
            "check": "观察哨格式",
            "ok": ok,
            "detail": detail,
        }
    )

    # ─── 3. 风险矩阵：表格存在且 ≥3 行 ───
    risk_idx = re.search(rf"<h[34][^>]*>{risk_title}[^<]*</h[34]>", html)
    risk_rows = 0
    if risk_idx:
        seg = html[risk_idx.end() : risk_idx.end() + 3000]
        risk_rows = _table_row_count(seg)
    ok = risk_rows >= 3
    issues.append(
        {
            "check": "风险矩阵",
            "ok": ok,
            "detail": f"表格行数 = {risk_rows}（要求 ≥ 3）",
        }
    )

    # ─── 4. 市场数据表：存在且 ≥10 行 ───
    # 标题可能带编号前缀（如 "4.1 全量市场数据"），前缀放宽容许
    market_idx = re.search(rf"<h[1-6][^>]*>[^<]*{market_title}[^<]*</h[1-6]>", html)
    market_rows = 0
    if market_idx:
        seg = html[market_idx.end() : market_idx.end() + 3000]
        market_rows = _table_row_count(seg)
    ok = market_rows >= 10
    issues.append(
        {
            "check": "市场数据表",
            "ok": ok,
            "detail": f"表格行数 = {market_rows}（要求 ≥ 10）",
        }
    )

    # ─── 5. 信号等级 badge：至少一种等级出现 ───
    sig_levels = sorted(set(re.findall(r'class="sig sig-([SABC])"', html)))
    ok = len(sig_levels) >= 1
    issues.append(
        {
            "check": "信号badge",
            "ok": ok,
            "detail": f"出现等级: {','.join(sig_levels) if sig_levels else '无'}",
        }
    )

    failed = [i for i in issues if not i["ok"]]
    passed = not failed
    summary = (
        f"{len(issues)} 项检查全部通过"
        if passed
        else f"{len(failed)}/{len(issues)} 项结构检查失败: " + "、".join(i["check"] for i in failed)
    )
    return {"passed": passed, "issues": issues, "summary": summary}


def check_file(path: Path, verbose: bool = True) -> dict:
    """校验单个 HTML 文件，返回结构验证结果"""
    html = path.read_text(encoding="utf-8")
    result = validate_report_structure(html)
    if verbose:
        print(f"文件: {path.name}")
        for issue in result["issues"]:
            mark = "[PASS]" if issue["ok"] else "[FAIL]"
            print(f"  {mark} {issue['check']}: {issue['detail']}")
        print(f"结果: {'PASS' if result['passed'] else 'FAIL'} — {result['summary']}")
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI 入口: python -m src.html_validator --check 文件..."""
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or "--help" in argv or "-h" in argv:
        print("用法: python -m src.html_validator --check <日报.html> [更多文件...]")
        return 0

    if argv[0] == "--check":
        files = [Path(p) for p in argv[1:]]
    else:
        files = [Path(p) for p in argv]

    failed_files = []
    for f in files:
        if not f.exists():
            print(f"文件不存在: {f}")
            failed_files.append(f)
            continue
        result = check_file(f)
        if not result["passed"]:
            failed_files.append(f)
    return 1 if failed_files else 0


if __name__ == "__main__":
    sys.exit(main())
