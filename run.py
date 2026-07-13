"""
PrismLens - 启动入口

使用方式:
    python run.py                    # 完整运行 (中文)
    python run.py --lang en          # 英文版
    python run.py --skip-ai          # 跳过 AI 分析 (测试用)
    python run.py --skip-fetch       # 跳过抓取 (使用缓存)
    python run.py --max-news 100     # 限制新闻数量
    python run.py --validate         # 仅验证配置，不运行 pipeline
"""

import argparse
import sys
from pathlib import Path

# Python 版本运行时检查 (README 要求 3.10+)
if sys.version_info < (3, 10):
    print(f"错误: PrismLens 需要 Python 3.10 或更高版本，当前版本为 {sys.version}")
    print("请升级 Python 后重试。")
    sys.exit(1)

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def _send_failure_notify(error_detail: str, env: dict | None = None) -> None:
    """发送失败通知到发件人邮箱"""
    try:
        from src.mailer import send_failure_notification

        if env is None:
            from src.utils import load_env

            env = load_env()

        if not env.get("smtp_enabled"):
            return
        if not env.get("smtp_user"):
            print("[!] 失败通知未发送: SMTP_USER 未配置")
            return
        if not env.get("smtp_pass"):
            print("[!] 失败通知未发送: SMTP_PASS 未配置")
            return
        if not env.get("smtp_host"):
            print("[!] 失败通知未发送: SMTP_HOST 未配置")
            return

        print("正在发送失败通知...")
        send_failure_notification(
            smtp_host=env["smtp_host"],
            smtp_port=env["smtp_port"],
            smtp_user=env["smtp_user"],
            smtp_pass=env["smtp_pass"],
            error_detail=error_detail,
        )
    except Exception as e:
        print(f"[!] 失败通知发送异常: {e}")


def _validate_config() -> int:
    """验证配置是否正确，返回退出码 (0=通过, 1=失败)"""
    from src.utils import load_env

    errors: list[str] = []
    warnings: list[str] = []

    # 1. 检查 .env 文件
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("[FAIL] .env 文件不存在，请复制 .env.example 并填写配置")
        return 1

    # 2. 加载并验证环境变量
    try:
        env = load_env()
    except (FileNotFoundError, ValueError) as e:
        print(f"[FAIL] 配置加载失败: {e}")
        return 1
    except Exception as e:
        print(f"[FAIL] 配置加载异常: {e}")
        return 1

    print("[OK] .env 文件已加载")

    # 3. API 配置检查
    api_url = env.get("api_url", "")
    if not api_url:
        errors.append("API_URL 未配置")
    else:
        print(f"[OK] API_URL = {api_url}")

    model = env.get("model_name", "")
    if not model:
        warnings.append("MODEL_NAME 未配置，将使用默认值")
    else:
        print(f"[OK] MODEL_NAME = {model}")

    # 4. SMTP 配置检查 (如果启用)
    if env.get("smtp_enabled"):
        print("[--] SMTP 已启用，检查配置...")
        smtp_ok = True

        if not env.get("smtp_host"):
            errors.append("SMTP_HOST 未配置")
            smtp_ok = False
        if not env.get("smtp_user"):
            errors.append("SMTP_USER 未配置")
            smtp_ok = False
        if not env.get("smtp_pass"):
            errors.append("SMTP_PASS 未配置 (授权码)")
            smtp_ok = False
        if not env.get("smtp_to"):
            errors.append("SMTP_TO 未配置 (收件地址)")
            smtp_ok = False

        if smtp_ok:
            print(f"[OK] SMTP_HOST = {env['smtp_host']}")
            print(f"[OK] SMTP_PORT = {env['smtp_port']}")
            print(f"[OK] SMTP_USER = {env['smtp_user']}")
            print(f"[OK] SMTP_TO = {env['smtp_to']}")
    else:
        print("[--] SMTP 未启用 (跳过邮件配置检查)")

    # 5. 代理配置检查
    proxy = env.get("https_proxy", "")
    if proxy:
        print(f"[OK] HTTPS_PROXY = {proxy}")

    # 6. 汇总结果
    print()
    if errors:
        print(f"[FAIL] 发现 {len(errors)} 个错误:")
        for err in errors:
            print(f"  - {err}")
        return 1

    if warnings:
        print(f"[WARN] 发现 {len(warnings)} 个警告:")
        for w in warnings:
            print(f"  - {w}")

    print("[PASS] 配置验证通过")
    return 0


def main():
    """主入口函数"""

    # Windows 终端 UTF-8 支持
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="PrismLens - 私人国际局势观察系统",
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
        default=500,
        help="最大新闻数量 (默认 500)",
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
    parser.add_argument(
        "--validate",
        action="store_true",
        help="仅验证配置是否正确，不运行完整 pipeline",
    )
    parser.add_argument(
        "--pipeline",
        type=str,
        choices=["v2", "v3", "shadow"],
        default="v3",
        help="Pipeline 版本: v2=三阶段(旧), v3=单阶段(新·默认), shadow=v2+v3对比",
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="使用缓存数据快速重建 HTML (跳过抓取和 AI，用于测试 UI 修改)",
    )

    args = parser.parse_args()

    # 配置验证模式
    if args.validate:
        sys.exit(_validate_config())

    # 缓存模式：使用上次 AI 生成的 summary 快速重建 HTML
    if args.cache:
        cache_path = PROJECT_ROOT / "cache" / "last_summary.md"
        if not cache_path.exists():
            print(f"错误: 缓存文件不存在: {cache_path}")
            print("请先完整运行一次 (python run.py --no-open) 生成缓存")
            sys.exit(1)
        mock_summary = cache_path.read_text(encoding="utf-8")
        print(f"[缓存] 使用缓存摘要 ({len(mock_summary)} 字符)，跳过抓取和 AI 生成")
        args.skip_fetch = True
        args.skip_ai = True
    else:
        mock_summary = ""

    # 读取模拟总结文件 (如果指定)
    if args.mock_summary:
        mock_path = Path(args.mock_summary)
        if mock_path.exists():
            with open(mock_path, encoding="utf-8") as f:
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
            pipeline=args.pipeline,
        )

    except KeyboardInterrupt:
        print("\n用户中断，正在退出...")
        sys.exit(0)
    except SystemExit as e:
        # run() 内部失败时的 sys.exit(1) 会抛出 SystemExit
        if e.code != 0 and args.email:
            _send_failure_notify("运行流程失败，请检查日志")  # env 此时不可用，内部自行加载
        sys.exit(e.code)
    except Exception as e:
        print(f"\n运行出错: {e}")
        print("请查看日志文件获取详细信息。")

        # 运行失败时发送通知
        if args.email:
            _send_failure_notify(f"运行异常: {e}")  # env 此时不可用，内部自行加载
        sys.exit(1)

    # 邮件发送
    if args.email:
        from src.mailer import send_report
        from src.utils import load_env

        env = load_env()
        if not env.get("smtp_enabled"):
            print("\n[!] 邮件发送已跳过: .env 中 SMTP_ENABLED 未设为 true")
            sys.exit(0)

        if not all([env.get("smtp_user"), env.get("smtp_pass"), env.get("smtp_to")]):
            print("\n[!] 邮件发送失败: .env 中 SMTP 账号、密码或收件地址未完整配置")
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
            # 发送失败通知到发件人邮箱 (复用已加载的 env，避免重复 I/O)
            print("[X] 日报发送失败，正在发送失败通知...")
            _send_failure_notify("日报邮件发送失败，请检查日志", env=env)
            sys.exit(1)


if __name__ == "__main__":
    main()
