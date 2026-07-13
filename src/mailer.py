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
import os
import re
import smtplib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger("global_news.mailer")


def _extract_plain_text(html_content: str) -> str:
    """
    从 HTML 中提取纯文本摘要用于邮件正文

    简单策略：提取标题和关键段落，去掉所有标签
    """
    # 提取 h2 标题
    h2_titles = re.findall(r"<h2[^>]*>(.*?)</h2>", html_content, re.DOTALL)
    # 去除 HTML 标签
    clean_titles = [re.sub(r"<[^>]+>", "", t).strip() for t in h2_titles]

    # 提取前几个段落文本
    paragraphs = re.findall(r"<p>(.*?)</p>", html_content, re.DOTALL)
    clean_paras = []
    for p in paragraphs:
        text = re.sub(r"<[^>]+>", "", p).strip()
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

    # Dry-run 安全检查（借鉴 HN Briefing Agent 模式）
    delivery_mode = os.environ.get("AGENTSCOUT_DELIVERY", "").lower()
    if delivery_mode != "live":
        logger.info("[DRY-RUN] 邮件未实际发送（设置 AGENTSCOUT_DELIVERY=live 以启用真实发送）")
        logger.info(f"[DRY-RUN] 收件人: {smtp_to}, 报告: {report_path.name}")
        return True  # dry-run 成功，不阻塞流程

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
    subject = (
        f"Global Intelligence Briefing — {today}" if lang == "en" else f"全球局势日报 — {today}"
    )

    # 创建多部分邮件
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = smtp_to

    # 添加纯文本正文
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))

    # 添加 HTML 文件作为附件（检查大小）
    html_bytes = html_content.encode("utf-8")
    max_attachment_bytes = 45 * 1024 * 1024  # 45MB，留余量避免触发服务器限制
    if len(html_bytes) > max_attachment_bytes:
        logger.error(
            f"附件过大: {len(html_bytes) / 1024 / 1024:.1f}MB，超过 45MB 限制。"
            f"请减少报告内容或拆分发送。"
        )
        return False

    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(html_bytes)
    encoders.encode_base64(attachment)

    # 使用报告文件名作为附件名
    filename = report_path.name
    attachment.add_header("Content-Disposition", "attachment", filename=("utf-8", "", filename))
    msg.attach(attachment)

    # 发送（smtp_to 可能是逗号分隔的多收件人，需转为列表）
    recipients = [addr.strip() for addr in smtp_to.split(",") if addr.strip()]

    try:
        _smtp_send(smtp_host, smtp_port, smtp_user, smtp_pass, recipients, msg.as_string())
        logger.info(f"日报已发送至 {smtp_to}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            f"SMTP 认证失败 [{smtp_host}:{smtp_port}]。"
            f"请检查: 1) 邮箱地址是否正确 2) 是否使用了授权码而非登录密码 "
            f"3) QQ邮箱需在设置>账户中开启SMTP并生成授权码"
        )
        return False
    except smtplib.SMTPConnectError:
        logger.error(
            f"无法连接到 SMTP 服务器 {smtp_host}:{smtp_port}，已重试3次。请检查服务器地址和端口"
        )
        return False
    except Exception as e:
        logger.error(f"邮件发送失败 [{smtp_host}:{smtp_port}]: {e}", exc_info=True)
        return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(
        (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError, TimeoutError)
    ),
    reraise=True,
)
def _smtp_send(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    recipients: list[str],
    msg_string: str,
) -> None:
    """SMTP 发送核心逻辑，带自动重试（仅对瞬时错误重试）"""
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg_string)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, recipients, msg_string)


def _sanitize_error_detail(error_detail: str, max_len: int = 300) -> str:
    """截断并清理错误详情，避免泄露内部路径和堆栈信息"""
    # 移除常见的文件路径模式
    sanitized = re.sub(r"[A-Za-z]:\\[^\s,;)]+", "[path]", error_detail)
    sanitized = re.sub(r"/[a-zA-Z_][\w/]*/[^\s,;)]+", "[path]", sanitized)
    # 截断过长内容
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len] + "...(已截断)"
    return sanitized


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
    subject = f"全球局势日报生成失败 - {today}"

    # 清理错误详情，避免泄露内部实现细节
    safe_error = _sanitize_error_detail(error_detail)

    # 构建纯文本邮件
    body = f"""全球局势日报 - 运行失败通知
{"=" * 50}

失败时间: {today}
错误详情:
{safe_error}

{"=" * 50}

请检查以下内容:
1. 查看系统日志获取详细错误信息
2. 检查运行状态
3. 网络连接和 API 配置

--
Global News Briefing System
"""

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = smtp_user  # 发给自己

    try:
        _smtp_send(smtp_host, smtp_port, smtp_user, smtp_pass, [smtp_user], msg.as_string())
        logger.info("失败通知已发送")
        return True

    except Exception as e:
        logger.error(f"失败通知发送失败 [{smtp_host}:{smtp_port}]: {e}", exc_info=True)
        return False
