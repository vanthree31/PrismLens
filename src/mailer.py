"""
邮件发送模块

通过 SMTP 将 HTML 日报发送到指定邮箱。
支持 QQ 邮箱、163 邮箱、Gmail 等常见 SMTP 服务。

邮件策略：
- 邮件正文：纯文本摘要（兼容所有邮件客户端）
- 附件：完整 HTML 文件（用浏览器打开查看）
- 失败通知：运行失败时发送到发件人邮箱
"""

import logging
import re
import smtplib
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

logger = logging.getLogger("global_news.mailer")


def _extract_plain_text(html_content: str) -> str:
    """
    从 HTML 中提取纯文本摘要用于邮件正文

    简单策略：提取标题和关键段落，去掉所有标签
    """
    # 提取 h2 标题
    h2_titles = re.findall(r'<h2[^>]*>(.*?)</h2>', html_content, re.DOTALL)
    # 去除 HTML 标签
    clean_titles = [re.sub(r'<[^>]+>', '', t).strip() for t in h2_titles]

    # 提取前几个段落文本
    paragraphs = re.findall(r'<p>(.*?)</p>', html_content, re.DOTALL)
    clean_paras = []
    for p in paragraphs:
        text = re.sub(r'<[^>]+>', '', p).strip()
        if len(text) > 20 and len(text) < 500:  # 过滤太短或太长的
            clean_paras.append(text)

    # 构建纯文本摘要
    lines = []
    lines.append("=" * 50)
    lines.append("国际局势日报 - 内容摘要")
    lines.append("=" * 50)
    lines.append("")

    if clean_titles:
        lines.append("【主要章节】")
        for i, title in enumerate(clean_titles[:10], 1):
            lines.append(f"  {i}. {title}")
        lines.append("")

    if clean_paras:
        lines.append("【内容摘录】")
        for p in clean_paras[:5]:
            lines.append(f"  • {p[:100]}...")
        lines.append("")

    lines.append("=" * 50)
    lines.append("完整报告请查看附件 HTML 文件")
    lines.append("（用浏览器打开即可查看完整内容）")
    lines.append("=" * 50)

    return "\n".join(lines)


def send_report(
    report_path: Path,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    smtp_to: str,
    lang: str = "zh",
) -> bool:
    """
    发送日报邮件（HTML 作为附件）

    Args:
        report_path: HTML 报告文件路径
        smtp_host: SMTP 服务器地址
        smtp_port: SMTP 端口 (465=SSL, 587=STARTTLS)
        smtp_user: 发件人邮箱
        smtp_pass: SMTP 授权码
        smtp_to: 收件人邮箱

    Returns:
        True 发送成功，False 发送失败
    """
    if not report_path.exists():
        logger.error(f"报告文件不存在: {report_path}")
        return False

    # 读取 HTML 内容
    try:
        html_content = report_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"读取报告文件失败: {e}")
        return False

    # 提取纯文本摘要
    plain_text = _extract_plain_text(html_content)

    # 构建邮件
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"Global Intelligence Briefing — {today}" if lang == "en" else f"全球局势日报 — {today}"

    # 创建多部分邮件
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = smtp_to

    # 添加纯文本正文
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))

    # 添加 HTML 文件作为附件
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(html_content.encode("utf-8"))
    encoders.encode_base64(attachment)

    # 使用报告文件名作为附件名
    filename = report_path.name
    attachment.add_header(
        "Content-Disposition",
        "attachment",
        filename=("utf-8", "", filename)
    )
    msg.attach(attachment)

    # 发送
    try:
        if smtp_port == 465:
            # SSL 连接
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, smtp_to, msg.as_string())
        else:
            # STARTTLS 连接
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, smtp_to, msg.as_string())

        logger.info(f"日报已发送至 {smtp_to}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP 认证失败，请检查邮箱地址和授权码")
        return False
    except smtplib.SMTPConnectError:
        logger.error(f"无法连接到 SMTP 服务器 {smtp_host}:{smtp_port}")
        return False
    except Exception as e:
        logger.error(f"邮件发送失败: {type(e).__name__}: {e}")
        return False


def send_failure_notification(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    error_detail: str,
) -> bool:
    """
    发送失败通知邮件到发件人邮箱

    Args:
        smtp_host: SMTP 服务器地址
        smtp_port: SMTP 端口
        smtp_user: 发件人邮箱（同时也是收件人）
        smtp_pass: SMTP 授权码
        error_detail: 错误详情

    Returns:
        True 发送成功，False 发送失败
    """
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = f"⚠️ 全球局势日报生成失败 - {today}"

    # 构建纯文本邮件
    body = f"""全球局势日报 - 运行失败通知
{'=' * 50}

失败时间: {today}
错误详情:
{error_detail}

{'=' * 50}

请检查以下内容:
1. 日志文件: logs/briefing_{datetime.now().strftime('%Y%m%d')}.log
2. 运行状态: data/runtime/current_run.json
3. 网络连接和 API 配置

--
Global News Briefing System
"""

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = smtp_user  # 发给自己

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, smtp_user, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, smtp_user, msg.as_string())

        logger.info(f"失败通知已发送至 {smtp_user}")
        print(f"[OK] 失败通知已发送至 {smtp_user}")
        return True

    except Exception as e:
        logger.error(f"失败通知发送失败: {type(e).__name__}: {e}")
        print(f"[X] 失败通知发送失败: {e}")
        return False
