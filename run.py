"""
Global News Briefing - 启动入口

使用方式:
    python run.py                    # 完整运行 (中文)
    python run.py --lang en          # 英文版
    python run.py --skip-ai          # 跳过 AI 分析 (测试用)
    python run.py --skip-fetch       # 跳过抓取 (使用缓存)
    python run.py --max-news 100     # 限制新闻数量
"""

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def _send_failure_notify(error_detail: str) -> None:
    """发送失败通知到发件人邮箱"""
    try:
        from src.utils import load_env
        from src.mailer import send_failure_notification
        env = load_env()
        if env.get("smtp_enabled") and env.get("smtp_user"):
            print("正在发送失败通知...")
            send_failure_notification(
                smtp_host=env["smtp_host"],
                smtp_port=env["smtp_port"],
                smtp_user=env["smtp_user"],
                smtp_pass=env["smtp_pass"],
                error_detail=error_detail,
            )
    except Exception:
        pass  # 通知失败不影响退出


def main():
    """主入口函数"""
    parser = argparse.ArgumentParser(
        description="Global News Briefing - 私人国际局势观察系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="跳过新闻抓取 (使用缓存数据)",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="跳过 AI 分析 (使用模拟数据，用于测试)",
    )
    parser.add_argument(
        "--mock-summary",
        type=str,
        default="",
        help="指定模拟的 AI 总结文件路径",
    )
    parser.add_argument(
        "--max-news",
        type=int,
        default=200,
        help="最大新闻数量 (默认 200)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="不自动打开浏览器",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="生成完成后发送邮件 (需在 .env 中配置 SMTP)",
    )
    parser.add_argument(
        "--lang",
        type=str,
        choices=["zh", "en"],
        default="zh",
        help="输出语言: zh=中文(默认), en=英文",
    )

    args = parser.parse_args()

    # 读取模拟总结文件 (如果指定)
    mock_summary = ""
    if args.mock_summary:
        mock_path = Path(args.mock_summary)
        if mock_path.exists():
            with open(mock_path, "r", encoding="utf-8") as f:
                mock_summary = f.read()
        else:
            print(f"错误: 模拟总结文件不存在: {mock_path}")
            sys.exit(1)

    # 导入并运行主流程
    from src.main import run

    try:
        report_path = run(
            skip_fetch=args.skip_fetch,
            skip_ai=args.skip_ai,
            mock_summary=mock_summary,
            max_news=args.max_news,
            auto_open=not args.no_open,
            lang=args.lang,
        )

    except KeyboardInterrupt:
        print("\n用户中断，正在退出...")
        sys.exit(0)
    except SystemExit as e:
        # run() 内部失败时的 sys.exit(1) 会抛出 SystemExit
        if e.code != 0 and args.email:
            _send_failure_notify("运行流程失败，请检查日志")
        sys.exit(e.code)
    except Exception as e:
        print(f"\n运行出错: {e}")
        import traceback
        traceback.print_exc()

        # 运行失败时发送通知
        if args.email:
            _send_failure_notify(f"运行异常: {type(e).__name__}: {e}")
        sys.exit(1)

    # 邮件发送
    if args.email:
        from src.utils import load_env
        from src.mailer import send_report, send_failure_notification

        env = load_env()
        if not env.get("smtp_enabled"):
            print("\n[!] 邮件发送已跳过: .env 中 SMTP_ENABLED 未设为 true")
            sys.exit(0)

        if not all([env.get("smtp_user"), env.get("smtp_pass"), env.get("smtp_to")]):
            print("\n[!] 邮件发送失败: .env 中 SMTP_USER / SMTP_PASS / SMTP_TO 未完整配置")
            sys.exit(1)

        print("\n正在发送邮件...")
        ok = send_report(
            report_path=report_path,
            smtp_host=env["smtp_host"],
            smtp_port=env["smtp_port"],
            smtp_user=env["smtp_user"],
            smtp_pass=env["smtp_pass"],
            smtp_to=env["smtp_to"],
            lang=args.lang,
        )
        if ok:
            print(f"[OK] 日报已发送至 {env['smtp_to']}")
        else:
            # 发送失败通知到发件人邮箱
            print("[X] 日报发送失败，正在发送失败通知...")
            send_failure_notification(
                smtp_host=env["smtp_host"],
                smtp_port=env["smtp_port"],
                smtp_user=env["smtp_user"],
                smtp_pass=env["smtp_pass"],
                error_detail="日报邮件发送失败，请检查日志",
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
