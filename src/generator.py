"""
HTML 报告生成模块

生成精美的离线可查看 HTML 国际局势日报。
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown

from src.utils import (
    get_output_dir,
    get_today_str,
    get_today_filename,
)

logger = logging.getLogger("global_news.generator")


# ─────────────────────────────────────────────────
# Markdown 到 HTML 转换
# ─────────────────────────────────────────────────

def markdown_to_html(md_text: str, lang: str = "zh") -> str:
    """
    将 Markdown 转换为 HTML

    使用 python-markdown 的 toc 扩展自动为标题生成 ID。
    """
    # 预处理：确保列表前有空行，避免被当作段落内容
    # 只在普通文本行（非标题、非空行、非列表项）后添加空行
    md_text = re.sub(r'([^\n#*\-])\n(- )', r'\1\n\n\2', md_text)

    extensions = [
        "markdown.extensions.tables",
        "markdown.extensions.toc",
        "markdown.extensions.fenced_code",
    ]

    html = markdown.markdown(md_text, extensions=extensions)

    # 后处理流水线
    html = _fix_inline_lists(html)
    html = _fix_broken_table_rows(html)
    html = _flatten_li_paragraphs(html)
    html = _apply_signal_badges(html, lang=lang)
    html = _normalize_separators(html)
    html = _transform_risk_cards(html, lang=lang)
    return html


def _fix_inline_lists(html: str) -> str:
    """修复被 markdown 解析器错误放在 <p> 内的列表项"""
    def split_list(match):
        content = match.group(1)
        # 检查是否包含列表项
        if '\n- ' not in content and not content.startswith('- '):
            return match.group(0)
        # 分割：段落文本 和 列表项
        parts = re.split(r'\n(?=- )', content)
        para_parts = []
        list_items = []
        in_list = False
        for part in parts:
            stripped = part.strip()
            if stripped.startswith('- '):
                in_list = True
                list_items.append(stripped[2:])
            elif in_list:
                # 列表项中间的续行
                if list_items:
                    list_items[-1] += ' ' + stripped
            else:
                para_parts.append(stripped)
        result = ''
        if para_parts:
            text = '<br>\n'.join(p for p in para_parts if p)
            result += f'<p>{text}</p>\n'
        if list_items:
            result += '<ul>\n'
            for item in list_items:
                result += f'<li>{item}</li>\n'
            result += '</ul>\n'
        return result

    # 逐个匹配 <p>...</p>，避免跨标签匹配
    html = re.sub(r'<p>((?:[^<]|<(?!/p>))*)</p>', split_list, html)
    return html


def _fix_broken_table_rows(html: str) -> str:
    """
    修复被 markdown 解析器错误放在 <p> 内的表格行。

    当 AI 生成的 markdown 中表格行与主体表格之间有格式偏差时，
    这些行会被渲染为 <p>| col1 | col2 | ... |</p> 而非 <tr>。
    本函数将这些游离行合并回前方最近的 <table>。
    """

    def _to_table_row(content: str) -> str:
        """将 markdown 表格行文本转为 HTML <tr>"""
        cells = [c.strip() for c in content.strip('|').split('|')]
        cells_html = ''.join(f'<td>{c}</td>' for c in cells if c)
        return f'<tr>{cells_html}</tr>'

    # 第一步：将 <p>| ... |</p> 转为 <tr>...</tr>
    def convert_para_row(m):
        content = m.group(1).strip()
        if content.startswith('|') and content.count('|') >= 2:
            return _to_table_row(content)
        return m.group(0)

    html = re.sub(r'<p>(.*?)</p>', convert_para_row, html, flags=re.DOTALL)

    # 第二步：收集 </table> 后面紧跟的孤立 <tr>，合并回表格
    # 匹配模式：</tbody></table>（或 </table>）后跟 <hr /> 和孤立 <tr>
    pattern = re.compile(
        r'(</tbody>\s*</table>|</table>)((?:\s*(?:<hr\s*/?>)?\s*<tr>.*?</tr>)+)',
        re.DOTALL
    )

    def merge_orphans(m):
        table_close = m.group(1)
        orphans_block = m.group(2)
        orphan_rows = ''.join(re.findall(r'<tr>.*?</tr>', orphans_block, re.DOTALL))
        # 插入到 </tbody> 前（或 </table> 前）
        if '</tbody>' in table_close:
            return table_close.replace('</tbody>', orphan_rows + '</tbody>')
        return table_close.replace('</table>', orphan_rows + '</table>')

    html = pattern.sub(merge_orphans, html)

    return html


# ─────────────────────────────────────────────────
# 后处理：视觉优化
# ─────────────────────────────────────────────────

def _flatten_li_paragraphs(html: str) -> str:
    """将 <li><p>...</p></li> 中多余的 <p> 标签去掉，减少列表间距"""
    # 多轮处理，处理嵌套和跨行情况
    html = re.sub(r'<li>\s*<p>(.*?)</p>\s*</li>', r'<li>\1</li>', html, flags=re.DOTALL)
    html = re.sub(r'<li>\n<p>(.*?)</p>', r'<li>\1', html, flags=re.DOTALL)
    return html


def _apply_signal_badges(html: str, lang: str = "zh") -> str:
    """将纯文本的信号等级转为左侧色带样式，跳过 7.2 信号变化检测

    中文: S级/A级/B级/C级
    英文: S-level/A-level/B-level/C-level

    使用占位符策略避免双重替换：
    1. 先将 <strong>X级/X-level</strong> 替换为占位符
    2. 再将纯文本 X级/X-level 替换为占位符
    3. 最后统一将占位符转为 <span>
    """
    _PLACEHOLDER = '\x00SIG{level}\x00'

    def to_placeholder(m):
        level = m.group(1)
        return _PLACEHOLDER.format(level=level)

    if lang == "en":
        def to_span(m):
            level = m.group(1)
            return f'<span class="sig sig-{level}">{level}-level</span>'
        # 英文信号标签匹配
        strong_pattern = r'<strong>([SABC])-level</strong>'
        text_pattern = r'(?<![<a-zA-Z\x00])([SABC])-level(?![a-zA-Z>\x00])'
        skip_section = r'7\.2'
    else:
        def to_span(m):
            level = m.group(1)
            return f'<span class="sig sig-{level}">{level}级</span>'
        strong_pattern = r'<strong>([SABC])级</strong>'
        text_pattern = r'(?<![<a-zA-Z\x00])([SABC])级(?![a-zA-Z>\x00])'
        skip_section = r'8\.2'

    # 按 h3 拆分，跳过信号变化检测部分
    parts = re.split(r'(<h3[^>]*>.*?</h3>)', html, flags=re.DOTALL)
    result = []
    skip_next = False
    for part in parts:
        # 检查是否是信号变化检测的 h3 标题
        if re.match(rf'<h3[^>]*>.*?{skip_section}.*?</h3>', part, re.DOTALL):
            skip_next = True
            result.append(part)
            continue
        # 遇到下一个 h3 时重置跳过标记
        if re.match(r'<h3[^>]*>', part):
            skip_next = False
            result.append(part)
            continue
        if skip_next:
            result.append(part)
        else:
            # 步骤1: <strong>X级/X-level</strong> → 占位符
            part = re.sub(strong_pattern, to_placeholder, part)
            # 步骤2: 纯文本 X级/X-level → 占位符（仅匹配未被标签包裹的）
            part = re.sub(text_pattern, to_placeholder, part)
            # 步骤3: 占位符 → <span>
            part = re.sub(r'\x00SIG([SABC])\x00', to_span, part)
            result.append(part)
    return ''.join(result)


def _normalize_separators(html: str) -> str:
    """
    统一 <hr /> 分割线规则：
    - 移除所有现有的 <hr />（markdown 自动生成的不可控）
    - 在 h3 前插入分割线，但 h3 紧跟在 h2 后面时不插入
    """
    # 移除所有现有 hr
    html = re.sub(r'\s*<hr\s*/?>\s*', '\n', html)

    # 在 h3 前插入分割线，但排除紧跟 </h2> 后的 h3
    # 策略：先保护 "h2闭合+h3" 的模式，再统一插入，再恢复
    html = re.sub(
        r'(</h2>\s*\n\s*)(<h3)',
        r'\1<!--NOSEP-->\2',
        html
    )
    html = re.sub(r'\n\s*(?=<(?:h3)[\s>])', '\n<hr class="section-sep">\n', html)
    html = html.replace('<!--NOSEP-->', '')

    return html


def _transform_risk_cards(html: str, lang: str = "zh") -> str:
    """
    将"最高优先级风险/Top Priority Risks"部分的平铺<p>转换为卡片结构。

    转换规则：
    1. 检测包含风险项关键词的连续<p>标签
    2. 解析每个字段
    3. 生成卡片HTML结构
    """
    # 关键国家列表（用于高亮）
    KEY_COUNTRIES = {'美国', '俄罗斯', '中国', '伊朗', '以色列', '乌克兰', '朝鲜',
                     'United States', 'Russia', 'China', 'Iran', 'Israel', 'Ukraine', 'North Korea'}

    def parse_risk_fields(p_content: str) -> dict:
        """从<p>内容中解析风险字段（支持中英文）"""
        fields = {}
        # 匹配 <strong>字段名</strong>：值 的模式（中英文）
        pattern = r'<strong>(风险项|信号等级|置信度|风险来源|升级触发|缓和触发|涉及国家|潜在演化|Risk Item|Signal Level|Confidence|Risk Source|Escalation Trigger|De-escalation Trigger|Countries Involved|Potential Evolution)</strong>\s*[：:]\s*'
        matches = list(re.finditer(pattern, p_content))

        for i, match in enumerate(matches):
            field_name = match.group(1)
            start = match.end()
            # 找到下一个字段的开始位置
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(p_content)
            value = p_content[start:end].strip()
            # 清理值中的HTML标签（保留纯文本）
            value = re.sub(r'<[^>]+>', '', value).strip()
            fields[field_name] = value

        return fields

    def build_country_tags(countries_str: str) -> str:
        """生成国家标签HTML"""
        countries = [c.strip() for c in countries_str.replace('，', ',').split(',') if c.strip()]
        tags_html = '<div class="country-tags">'
        for country in countries:
            if country in KEY_COUNTRIES:
                tags_html += f'<span class="country-tag key-country">{country}</span>'
            else:
                tags_html += f'<span class="country-tag">{country}</span>'
        tags_html += '</div>'
        return tags_html

    def build_risk_card(fields: dict) -> str:
        """生成风险卡片HTML（支持中英文）"""
        signal_level = fields.get('信号等级', fields.get('Signal Level', 'B'))
        title = fields.get('风险项', fields.get('Risk Item', 'Unknown Risk'))
        confidence = fields.get('置信度', fields.get('Confidence', 'Medium'))

        # 卡片类名（根据信号等级）
        card_class = 'risk-card'
        if signal_level == 'A':
            card_class += ' level-A'

        # 构建卡片HTML
        card = f'<div class="{card_class}">\n'

        # 检测是否为英文（通过字段名判断）
        is_en = 'Risk Item' in fields

        # 头部：信号badge + 标题 + 置信度
        card += '  <div class="risk-header">\n'
        if is_en:
            card += f'    <span class="sig sig-{signal_level}">{signal_level}-level</span>\n'
            card += f'    <span class="risk-title">{title}</span>\n'
            card += f'    <span class="field-label">Confidence: {confidence}</span>\n'
        else:
            card += f'    <span class="sig sig-{signal_level}">{signal_level}级</span>\n'
            card += f'    <span class="risk-title">{title}</span>\n'
            card += f'    <span class="field-label">置信度: {confidence}</span>\n'
        card += '  </div>\n'

        # 风险来源
        src_key = 'Risk Source' if is_en else '风险来源'
        if src_key in fields:
            card += '  <div class="risk-field">\n'
            card += f'    <span class="field-label">{"Risk Source" if is_en else "风险来源"}</span>\n'
            card += f'    <span class="field-value">{fields[src_key]}</span>\n'
            card += '  </div>\n'

        # 升级/缓和触发（对比布局）
        esc_key = 'Escalation Trigger' if is_en else '升级触发'
        deesc_key = 'De-escalation Trigger' if is_en else '缓和触发'
        if esc_key in fields or deesc_key in fields:
            card += '  <div class="risk-triggers">\n'
            if esc_key in fields:
                card += '    <div class="trigger-box escalation">\n'
                card += f'      <div class="trigger-label">↑ {"Escalation Trigger" if is_en else "升级触发"}</div>\n'
                card += f'      <div>{fields[esc_key]}</div>\n'
                card += '    </div>\n'
            if deesc_key in fields:
                card += '    <div class="trigger-box de-escalation">\n'
                card += f'      <div class="trigger-label">↓ {"De-escalation Trigger" if is_en else "缓和触发"}</div>\n'
                card += f'      <div>{fields[deesc_key]}</div>\n'
                card += '    </div>\n'
            card += '  </div>\n'

        # 涉及国家（标签样式）
        cty_key = 'Countries Involved' if is_en else '涉及国家'
        if cty_key in fields:
            card += '  <div class="risk-field">\n'
            card += f'    <span class="field-label">{"Countries Involved" if is_en else "涉及国家"}</span>\n'
            card += f'    <span class="field-value">{build_country_tags(fields[cty_key])}</span>\n'
            card += '  </div>\n'

        # 潜在演化
        evo_key = 'Potential Evolution' if is_en else '潜在演化'
        if evo_key in fields:
            card += '  <div class="risk-field">\n'
            card += f'    <span class="field-label">{"Potential Evolution" if is_en else "潜在演化"}</span>\n'
            card += f'    <span class="field-value">{fields[evo_key]}</span>\n'
            card += '  </div>\n'

        card += '</div>'
        return card

    # 查找"最高优先级风险"部分
    # 策略：找到包含"风险项"的连续<p>标签块
    def transform_risk_content(section_html: str) -> str:
        """转换风险部分内容"""
        # 按<p>标签分割
        parts = re.split(r'(<p>.*?</p>)', section_html, flags=re.DOTALL)

        result_parts = []
        risk_paragraphs = []

        for part in parts:
            # 检查是否是包含风险项的<p>（中英文）
            if '<p>' in part and (('风险项' in part and '信号等级' in part) or ('Risk Item' in part and 'Signal Level' in part)):
                risk_paragraphs.append(part)
            else:
                # 如果之前有积累的风险段落，先处理
                if risk_paragraphs:
                    for p in risk_paragraphs:
                        # 提取<p>内容
                        p_content = re.search(r'<p>(.*?)</p>', p, re.DOTALL)
                        if p_content:
                            fields = parse_risk_fields(p_content.group(1))
                            if fields:
                                result_parts.append(build_risk_card(fields))
                            else:
                                result_parts.append(p)
                    risk_paragraphs = []
                result_parts.append(part)

        # 处理剩余的风险段落
        if risk_paragraphs:
            for p in risk_paragraphs:
                p_content = re.search(r'<p>(.*?)</p>', p, re.DOTALL)
                if p_content:
                    fields = parse_risk_fields(p_content.group(1))
                    if fields:
                        result_parts.append(build_risk_card(fields))
                    else:
                        result_parts.append(p)

        return ''.join(result_parts)

    # 匹配"最高优先级风险/Top Priority Risks"到下一个<h3>之间的内容
    def replace_risk_section(match):
        """替换风险部分"""
        h3_tag = match.group(1)
        content = match.group(2)
        return h3_tag + transform_risk_content(content)

    # 匹配模式：<h3>最高优先级风险/Top Priority Risks</h3>后的内容，直到下一个<h3>或<hr>
    pattern = r'(<h3[^>]*>(?:最高优先级风险|Top Priority Risks)</h3>)(.*?)(?=<h3|<hr class="section-sep">)'
    html = re.sub(pattern, replace_risk_section, html, flags=re.DOTALL)

    return html


# ─────────────────────────────────────────────────
# 标题 ID 统一处理
# ─────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """
    将标题文本转换为 URL 安全的 ID

    保留中文字符，将空格和特殊字符替换为连字符。
    """
    # 去除 HTML 标签
    clean = re.sub(r'<[^>]+>', '', text).strip()
    # 保留中文、字母、数字，其余替换为 -
    slug = re.sub(r'[^\w一-鿿]+', '-', clean)
    # 去除首尾连字符
    slug = slug.strip('-').lower()
    return slug


def normalize_heading_ids(html_content: str) -> tuple[str, list[dict]]:
    """
    统一处理所有 h2 标题的 ID，并提取目录。

    Args:
        html_content: 带有 markdown 生成的 ID 的 HTML

    Returns:
        (处理后的 HTML, 目录列表)
    """
    toc = []

    def replace_h2(match):
        inner = match.group(1)
        # 提取纯文本标题
        title_text = re.sub(r'<[^>]+>', '', inner).strip()
        section_id = _slugify(title_text)
        if section_id:
            toc.append({"id": section_id, "title": title_text})
        return f'<h2 id="{section_id}">{inner}</h2>'

    # 匹配所有 h2 标签 (无论是否已有 id 属性)
    result = re.sub(
        r'<h2(?:\s[^>]*)?>(.*?)</h2>',
        replace_h2,
        html_content,
        flags=re.DOTALL
    )

    return result, toc


# ─────────────────────────────────────────────────
# 链接后处理
# ─────────────────────────────────────────────────

def clean_news_links(html_content: str, lang: str = "zh") -> str:
    """
    清洗新闻链接格式：
    1. 把 [阅读原文]/[Read Full Article](URL) 转为 <a> 超链接
    2. 把裸露长 URL 替换为 <a> 超链接
    3. 把 <a> 标签中链接文字是长 URL 的替换为"阅读原文/Read Full Article"
    4. 把发布时间包装为 <span class="link-date">
    """
    link_text = "Read Full Article" if lang == "en" else "阅读原文"
    # 1. markdown 格式的 [阅读原文]/[Read Full Article](URL) 转 HTML
    html_content = re.sub(
        r'\[(?:阅读原文|Read Full Article)\]\((https?://[^)]+)\)',
        rf'<a href="\1" class="link-action">{link_text}</a>',
        html_content
    )

    # 4. 包装发布时间（支持全角和半角括号）
    html_content = re.sub(
        r'[（(](\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})[）)]',
        r'<span class="link-date">(\1)</span>',
        html_content
    )

    # 2. <a> 标签中链接文字是长 URL 的，替换
    def fix_link(match):
        href = match.group(1)
        text = match.group(2)
        if text.startswith("http") or len(text) > 80:
            return f'<a href="{href}" class="link-action">{link_text}</a>'
        return match.group(0)

    html_content = re.sub(
        r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>',
        fix_link,
        html_content,
        flags=re.DOTALL
    )

    # 3. 裸露长 URL 转超链接
    def wrap_bare(url):
        return f'<a href="{url}" class="link-action">{link_text}</a>'

    html_content = re.sub(
        r'(?<!href=")(?<!src=")(?<!">)(https?://[^\s<>"\']{30,})',
        wrap_bare,
        html_content
    )

    return html_content


# ─────────────────────────────────────────────────
# 报告生成器
# ─────────────────────────────────────────────────

class ReportGenerator:
    """HTML 报告生成器"""

    def __init__(self):
        self.output_dir = get_output_dir()

        # 确保输出目录存在
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        markdown_content: str,
        source_count: int = 0,
        news_count: int = 0,
        lang: str = "zh",
    ) -> Path:
        """
        生成 HTML 报告

        Args:
            markdown_content: AI 生成的 Markdown 分析报告
            source_count: 新闻源数量
            news_count: 新闻条数
            lang: 语言 (zh/en)

        Returns:
            生成的 HTML 文件路径
        """
        logger.info("开始生成 HTML 报告...")

        # 转换 Markdown 到 HTML
        html_content = markdown_to_html(markdown_content, lang=lang)

        # 统一标题 ID 并提取目录
        html_content, toc = normalize_heading_ids(html_content)

        # 清洗新闻链接格式
        html_content = clean_news_links(html_content, lang=lang)

        # 将内容按 h2 拆分为幻灯片
        slides = self._split_into_slides(html_content)

        # 构建侧边栏目录和幻灯片 HTML
        sidebar_html = ""
        slides_html = ""
        for i, (section_id, section_title, slide_body) in enumerate(slides):
            active = " active" if i == 0 else ""
            sec_num = f'{i + 1:02d}'
            # 侧边栏：编号 + 标题文字（固定200px）
            sidebar_html += f'        <a class="nav-item{active}" data-slide="{i}" href="#{section_id}"><span class="nav-idx">{sec_num}</span><span class="nav-text">{section_title}</span></a>\n'
            # 幻灯片：自然文档流标题
            slides_html += f'    <section class="slide" id="{section_id}" data-index="{i}">\n'
            slides_html += f'      <div class="slide-inner">\n'
            slides_html += f'        <h2 class="chapter-title" id="{section_id}"><span class="ch-num">{sec_num}</span>{section_title}</h2>\n'
            slides_html += f'        <div class="slide-body">{slide_body}</div>\n'
            slides_html += f'      </div>\n'
            slides_html += f'    </section>\n'

        # 渲染模板（根据语言选择本地化文本）
        now = datetime.now()
        hour = now.hour
        generated_at = now.strftime("%H:%M:%S")

        if lang == "en":
            period = "AM" if hour < 12 else "PM"
            title = f"Global Intelligence Briefing - {get_today_str()} {period}"
            date_str = now.strftime("%B %d, %Y")
            subtitle = f"Multi-Camp Narrative Analysis · Event Clustering · Risk Signal Extraction · {period}"
            template = _get_localized_template("en")
        else:
            period = "上午版" if hour < 12 else "下午版"
            title = f"国际局势日报 - {get_today_str()} {period}"
            date_str = now.strftime("%Y年%m月%d日")
            subtitle = f"多阵营叙事分析 · 事件聚类 · 风险信号提炼 · {period}"
            template = _get_localized_template("zh")

        rendered = template.format(
            title=title,
            subtitle=subtitle,
            date=date_str,
            source_count=source_count,
            news_count=news_count,
            generated_at=generated_at,
            year=now.year,
            sidebar_items=sidebar_html,
            slides=slides_html,
            disclaimer_index=len(slides) + 1,
            total_slides=len(slides) + 2,  # title slide + content slides + disclaimer
        )

        # 写入文件
        output_file = self.output_dir / get_today_filename(output_dir=str(self.output_dir), lang=lang)
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(rendered)

        file_size = output_file.stat().st_size
        logger.info(f"HTML 报告已生成: {output_file} ({file_size / 1024:.1f} KB)")

        return output_file

    @staticmethod
    def _split_into_slides(html_content: str) -> list[tuple[str, str, str]]:
        """
        将 HTML 内容按 <h2> 标签拆分为幻灯片。

        Returns:
            [(section_id, section_title, slide_body), ...]
        """
        import re
        slides = []
        # 在 h2 处分割，保留 h2 标签
        parts = re.split(r'(<h2[^>]*>.*?</h2>)', html_content, flags=re.DOTALL)

        current_body = ""
        for part in parts:
            h2_match = re.match(r'<h2\s+id="([^"]*)">(.*?)</h2>', part, re.DOTALL)
            if h2_match:
                # 遇到新 h2 时，把已积累的 body 存入上一个 slide
                if slides:
                    slides[-1] = (slides[-1][0], slides[-1][1], current_body)
                section_id = h2_match.group(1)
                section_title = h2_match.group(2).strip()
                slides.append((section_id, section_title, ""))
                current_body = ""
            else:
                current_body += part

        # 最后一个 slide：把剩余 body 存入
        if slides:
            slides[-1] = (slides[-1][0], slides[-1][1], current_body)

        # 如果没有 h2（整篇只有一个 section），把全部内容作为一个 slide
        if not slides:
            slides.append(("", "简报内容", html_content))

        return slides


# ═══════════════════════════════════════════════════
# HTML 模板 — 文档长文风格
# ═══════════════════════════════════════════════════

_HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        /* ═══════════════════════════════════════════
           Design System v6 — PaperLens Inspired
           白色背景 · 圆角卡片 · 柔和阴影 · 蓝色渐变强调色
           ═══════════════════════════════════════════ */

        :root {{
            --bg: #FFFFFF;
            --surface: #F5F5F7;
            --surface-elevated: #FFFFFF;
            --ink: #1D1D1F;
            --ink-sec: #6E6E73;
            --ink-muted: #AEAEB2;
            --primary: #007AFF;
            --primary-hover: #0051D5;
            --primary-light: rgba(0, 122, 255, 0.08);
            --cta-gradient: linear-gradient(135deg, #007AFF 0%, #5856D6 100%);
            --red: #FF3B30;
            --amber: #FF9500;
            --blue: #007AFF;
            --green: #34C759;
            --border: rgba(0, 0, 0, 0.06);
            --border-strong: rgba(0, 0, 0, 0.1);
            --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.04);
            --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.08);
            --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.12);
            --glass-bg: rgba(255, 255, 255, 0.72);
            --glass-blur: saturate(180%) blur(20px);
            --font: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text',
                    'Helvetica Neue', 'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC',
                    sans-serif;
            --mono: 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace;
            --radius-sm: 8px;
            --radius-md: 16px;
            --radius-lg: 24px;
            --transition-fast: 200ms cubic-bezier(0.25, 0.1, 0.25, 1);
            --transition-normal: 400ms cubic-bezier(0.25, 0.1, 0.25, 1);
            --sidebar-w: 48px;
            --sidebar-w-hover: 220px;
            --content-max: 900px;
        }}

        /* ═══════════════════════════════════════════
           DARK MODE — 暗色模式（手动切换）
           ═══════════════════════════════════════════ */
        [data-theme="dark"] {{
            --bg: #1C1C1E;
            --surface: #2C2C2E;
            --surface-elevated: #3A3A3C;
            --ink: #F5F5F7;
            --ink-sec: #AEAEB2;
            --ink-muted: #6E6E73;
            --primary: #0A84FF;
            --primary-hover: #409CFF;
            --primary-light: rgba(10, 132, 255, 0.15);
            --cta-gradient: linear-gradient(135deg, #0A84FF 0%, #5E5CE6 100%);
            --red: #FF453A;
            --amber: #FF9F0A;
            --blue: #0A84FF;
            --green: #30D158;
            --border: rgba(255, 255, 255, 0.1);
            --border-strong: rgba(255, 255, 255, 0.15);
            --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.3);
            --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.4);
            --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.5);
            --glass-bg: rgba(28, 28, 30, 0.85);
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html {{ scroll-behavior: smooth; scroll-padding-top: 16px; -webkit-text-size-adjust: 100%; }}

        body {{
            font-family: var(--font);
            background: var(--bg);
            color: var(--ink);
            line-height: 1.7;
            font-size: 16px;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            margin-left: var(--sidebar-w);
            overflow-x: hidden;
        }}

        ::selection {{ background: rgba(0, 122, 255, 0.15); color: var(--ink); }}

        /* 可访问性：focus样式 */
        :focus-visible {{
            outline: 2px solid var(--primary);
            outline-offset: 2px;
        }}
        .nav-item:focus-visible,
        .theme-toggle:focus-visible,
        .hamburger:focus-visible,
        .back-to-top:focus-visible {{
            outline: 2px solid var(--primary);
            outline-offset: 2px;
        }}

        /* ═══════════════════════════════════════════
           SCROLL PROGRESS — 顶部渐变细线
           ═══════════════════════════════════════════ */
        .scroll-progress {{
            position: fixed;
            top: 0; left: var(--sidebar-w); right: 0;
            height: 2px;
            z-index: 300;
            background: transparent;
        }}
        .scroll-progress-bar {{
            height: 100%;
            background: var(--cta-gradient);
            width: 0%;
            transition: width 0.1s linear;
            border-radius: 0 1px 1px 0;
        }}

        /* ═══════════════════════════════════════════
           SIDEBAR — 桌面端默认收起，hover展开
           ═══════════════════════════════════════════ */
        .sidebar {{
            position: fixed;
            left: 0; top: 0;
            width: var(--sidebar-w);
            height: 100vh;
            background: var(--glass-bg);
            backdrop-filter: var(--glass-blur);
            -webkit-backdrop-filter: var(--glass-blur);
            border-right: 0.5px solid var(--border);
            display: flex;
            flex-direction: column;
            z-index: 100;
            transition: width 0.3s cubic-bezier(0.25, 0.1, 0.25, 1);
            overflow: hidden;
        }}

        .sidebar:hover {{
            width: var(--sidebar-w-hover);
        }}

        .sidebar-header {{
            padding: 16px 12px;
            border-bottom: 0.5px solid var(--border);
            flex-shrink: 0;
            white-space: nowrap;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .sidebar-header .logo {{
            font-size: 11px;
            font-weight: 700;
            color: var(--ink);
            letter-spacing: 0.3px;
        }}

        .theme-toggle {{
            background: none;
            border: none;
            font-size: 16px;
            cursor: pointer;
            padding: 4px;
            border-radius: 4px;
            transition: background var(--transition-fast);
            line-height: 1;
        }}
        .theme-toggle:hover {{
            background: var(--primary-light);
        }}

        .sidebar-nav {{
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
            padding: 8px 0;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .sidebar-nav::-webkit-scrollbar {{ width: 0; }}

        .nav-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 7px 12px;
            color: var(--ink-muted);
            text-decoration: none;
            font-size: 11px;
            font-weight: 500;
            border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
            transition: all var(--transition-fast);
            cursor: pointer;
            white-space: nowrap;
            overflow: hidden;
            margin-right: 4px;
        }}

        .nav-item:hover {{
            color: var(--ink);
            background: var(--primary-light);
        }}

        .nav-item.active {{
            color: var(--primary);
            font-weight: 600;
            background: var(--primary-light);
        }}

        .nav-idx {{
            font-family: var(--mono);
            font-size: 10px;
            color: var(--ink-muted);
            min-width: 18px;
            flex-shrink: 0;
            text-align: center;
        }}

        .nav-item.active .nav-idx {{ color: var(--primary); }}

        .nav-text {{
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .mobile-topbar, .mobile-toc-overlay, .mobile-toc {{ display: none; }}

        /* ═══════════════════════════════════════════
           CONTENT WRAPPER
           ═══════════════════════════════════════════ */
        .content-wrapper {{
            max-width: var(--content-max);
            margin: 0 auto;
            padding: 0 40px;
        }}

        /* ═══════════════════════════════════════════
           TITLE SLIDE — 白色封面 + 蓝色渐变
           ═══════════════════════════════════════════ */
        .title-slide {{
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            text-align: center;
            padding: 120px 40px 80px;
            background: var(--bg);
        }}

        .title-slide .label {{
            font-family: var(--mono);
            font-size: 11px;
            letter-spacing: 3px;
            text-transform: uppercase;
            color: var(--primary);
            margin-bottom: 28px;
            font-weight: 600;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 16px;
            background: var(--primary-light);
            border-radius: 20px;
        }}

        .title-slide .label::before {{
            content: '';
            width: 6px; height: 6px;
            border-radius: 50%;
            background: var(--green);
            animation: pulse-dot 2s ease-in-out infinite;
        }}

        @keyframes pulse-dot {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.4; }}
        }}

        .title-slide h1 {{
            font-size: clamp(2em, 5vw, 3em);
            font-weight: 700;
            color: var(--ink);
            letter-spacing: -0.02em;
            margin-bottom: 12px;
            line-height: 1.15;
        }}

        .title-slide .subtitle {{
            font-size: 17px;
            color: var(--ink-sec);
            letter-spacing: 0.2px;
            margin-bottom: 32px;
            font-weight: 400;
            line-height: 1.6;
        }}

        .title-slide .date {{
            font-size: 18px;
            color: var(--ink);
            font-weight: 600;
            margin-bottom: 8px;
        }}

        .title-slide .meta {{
            font-family: var(--mono);
            font-size: 12px;
            color: var(--ink-muted);
            letter-spacing: 0.2px;
        }}

        .title-slide .accent-line {{
            width: 40px;
            height: 3px;
            background: var(--cta-gradient);
            border-radius: 2px;
            margin: 32px auto 0;
        }}

        .signal-legend {{
            display: flex;
            gap: 20px;
            margin-top: 32px;
            flex-wrap: wrap;
            justify-content: center;
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: var(--ink-sec);
            font-weight: 500;
        }}

        .title-disclaimer {{
            margin-top: 32px;
            font-size: 12px;
            color: var(--ink-muted);
            max-width: 440px;
            line-height: 1.7;
            letter-spacing: 0.2px;
        }}

        /* ═══════════════════════════════════════════
           CHAPTER TITLE — 大号标题 + 淡色下划线
           ═══════════════════════════════════════════ */
        .chapter-title {{
            font-size: 28px;
            font-weight: 700;
            color: var(--ink);
            margin: 0 0 6px;
            padding: 48px 0 12px;
            border-bottom: 2px solid var(--border);
            line-height: 1.3;
            letter-spacing: -0.02em;
        }}

        .ch-num {{
            font-family: var(--mono);
            font-size: 13px;
            font-weight: 600;
            color: var(--primary);
            margin-right: 10px;
            vertical-align: middle;
            padding: 2px 8px;
            background: var(--primary-light);
            border-radius: var(--radius-sm);
        }}

        /* ═══════════════════════════════════════════
           SLIDES — 滚动文档模式
           ═══════════════════════════════════════════ */
        .slide {{ position: static; }}

        .slide-inner {{
            min-width: 0;
            padding: 0;
            margin: 0 auto;
        }}

        .slide-body {{
            padding: 8px 0 48px;
        }}

        /* ═══════════════════════════════════════════
           BACK TO TOP
           ═══════════════════════════════════════════ */
        .back-to-top {{
            position: fixed;
            bottom: 28px; right: 28px;
            width: 40px; height: 40px;
            border-radius: 50%;
            background: var(--surface-elevated);
            color: var(--ink-sec);
            display: flex; align-items: center; justify-content: center;
            cursor: pointer;
            opacity: 0; visibility: hidden;
            transition: all var(--transition-fast);
            z-index: 200;
            border: 0.5px solid var(--border);
            font-size: 16px;
            box-shadow: var(--shadow-md);
        }}
        .back-to-top.visible {{ opacity: 1; visibility: visible; }}
        .back-to-top:hover {{ color: var(--primary); border-color: var(--primary); transform: translateY(-2px); box-shadow: var(--shadow-lg); }}

        /* ═══════════════════════════════════════════
           CONTENT — 排版
           ═══════════════════════════════════════════ */
        h3 {{
            font-size: 17px;
            font-weight: 700;
            color: var(--ink);
            margin: 24px 0 8px;
            padding-bottom: 0;
            border-bottom: none;
        }}

        h4 {{
            font-size: 15px;
            font-weight: 600;
            color: var(--ink-sec);
            margin: 16px 0 4px;
        }}

        p {{ margin: 8px 0; line-height: 1.75; color: var(--ink-sec); }}
        ul, ol {{ margin: 6px 0 12px 20px; }}
        li {{ margin: 3px 0; line-height: 1.7; color: var(--ink-sec); }}
        li p {{ margin: 0; }}
        strong {{ font-weight: 600; color: var(--ink); }}
        em {{ color: var(--ink-muted); font-style: normal; }}
        a {{ color: var(--primary); text-decoration: none; transition: color var(--transition-fast); }}
        a:hover {{ color: var(--primary-hover); }}

        hr {{ display: none; }}
        hr.section-sep {{
            display: block;
            border: none;
            height: 1px;
            background: var(--border);
            margin: 24px 0 20px;
        }}

        /* ═══════════════════════════════════════════
           TABLES — 简洁 · 有呼吸感 · hover高亮
           ═══════════════════════════════════════════ */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0;
            font-size: 14px;
            border-radius: var(--radius-md);
            overflow: hidden;
        }}

        thead {{
            background: var(--surface);
        }}

        th {{
            padding: 12px 16px;
            text-align: left;
            font-weight: 600;
            font-size: 12px;
            color: var(--ink-sec);
            letter-spacing: 0.3px;
            white-space: nowrap;
            border-bottom: 1px solid var(--border);
            text-transform: uppercase;
        }}

        td {{
            padding: 10px 16px;
            border-bottom: 1px solid var(--border);
            vertical-align: top;
            line-height: 1.6;
            color: var(--ink-sec);
        }}

        tbody tr:nth-child(even) {{ background: rgba(0,0,0,0.03); }}
        tbody tr:hover {{ background: var(--primary-light); }}
        tbody tr:last-child td {{ border-bottom: none; }}

        td:empty {{ display: none; }}

        td .sig {{ font-size: 0.92em; }}

        /* ═══════════════════════════════════════════
           RISK CARDS — 核心风险卡片样式
           ═══════════════════════════════════════════ */
        .risk-card {{
            border-left: 3px solid var(--red);
            padding: 16px 20px;
            margin: 16px 0;
            background: var(--surface);
            border-radius: var(--radius-sm);
            transition: box-shadow var(--transition-fast);
        }}
        .risk-card:hover {{
            box-shadow: var(--shadow-md);
        }}
        .risk-card.level-A {{
            border-left-color: var(--amber);
        }}

        .risk-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }}
        .risk-header .risk-title {{
            font-size: 16px;
            font-weight: 700;
            color: var(--ink);
            flex: 1;
            min-width: 200px;
        }}

        .risk-field {{
            display: grid;
            grid-template-columns: 100px 1fr;
            gap: 4px 16px;
            margin: 6px 0;
            align-items: baseline;
        }}
        .risk-field .field-label {{
            font-size: 13px;
            font-weight: 600;
            color: var(--ink-muted);
            white-space: nowrap;
        }}
        .risk-field .field-value {{
            font-size: 14px;
            color: var(--ink-sec);
            line-height: 1.6;
        }}

        /* 升级/缓和触发对比 */
        .risk-triggers {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin: 12px 0;
        }}
        .trigger-box {{
            padding: 10px 14px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            line-height: 1.6;
        }}
        .trigger-box.escalation {{
            border-left: 3px solid var(--red);
            background: rgba(255, 59, 48, 0.04);
        }}
        .trigger-box.escalation .trigger-label {{
            color: var(--red);
            font-weight: 600;
            font-size: 12px;
            margin-bottom: 4px;
        }}
        .trigger-box.de-escalation {{
            border-left: 3px solid var(--green);
            background: rgba(52, 199, 89, 0.04);
        }}
        .trigger-box.de-escalation .trigger-label {{
            color: var(--green);
            font-weight: 600;
            font-size: 12px;
            margin-bottom: 4px;
        }}

        /* 国家标签 */
        .country-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin: 4px 0;
        }}
        .country-tag {{
            display: inline-block;
            padding: 2px 10px;
            background: var(--primary-light);
            border-radius: 12px;
            font-size: 12px;
            color: var(--primary);
            font-weight: 500;
        }}
        .country-tag.key-country {{
            background: rgba(255, 59, 48, 0.08);
            color: var(--red);
            font-weight: 600;
        }}

        /* ═══════════════════════════════════════════
           SIGNAL — 小色块badge
           ═══════════════════════════════════════════ */
        .sig {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-family: var(--mono);
            font-size: 14px;
            font-weight: 700;
            line-height: 1.5;
            vertical-align: middle;
            white-space: nowrap;
            padding: 3px 10px 3px 8px;
            border-radius: 6px;
            border: 1px solid transparent;
        }}

        .sig::before {{
            content: "";
            display: inline-block;
            width: 10px; height: 10px;
            border-radius: 50%;
            flex-shrink: 0;
        }}

        .sig-S {{ color: var(--red); background: rgba(255, 59, 48, 0.1); border-color: rgba(255, 59, 48, 0.3); }}
        .sig-S::before {{ background: var(--red); }}
        .sig-A {{ color: #D48806; background: rgba(255, 149, 0, 0.1); border-color: rgba(255, 149, 0, 0.3); }}
        .sig-A::before {{ background: var(--amber); }}
        .sig-B {{ color: #5856D6; background: rgba(88, 86, 214, 0.08); border-color: rgba(88, 86, 214, 0.2); }}
        .sig-B::before {{ background: #5856D6; }}
        .sig-C {{ color: var(--ink-muted); background: rgba(0,0,0,0.04); }}
        .sig-C::before {{ background: var(--ink-muted); }}

        /* 表格中的信号badge */
        td .sig {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 2px 8px 2px 6px;
            border-radius: 6px;
            font-size: 12px;
        }}

        td .sig::before {{
            width: 4px;
            height: 14px;
            border-radius: 2px;
        }}

        td .sig-S {{ background: rgba(255, 59, 48, 0.08); color: var(--red); }}
        td .sig-S::before {{ background: var(--red); }}
        td .sig-A {{ background: rgba(255, 149, 0, 0.08); color: var(--amber); }}
        td .sig-A::before {{ background: var(--amber); }}
        td .sig-B {{ background: rgba(0, 122, 255, 0.08); color: var(--blue); }}
        td .sig-B::before {{ background: var(--blue); }}
        td .sig-C {{ background: rgba(0,0,0,0.04); color: var(--ink-muted); }}
        td .sig-C::before {{ background: var(--ink-muted); }}

        /* ═══════════════════════════════════════════
           LISTS — 清爽列表
           ═══════════════════════════════════════════ */
        .slide-body ul {{ list-style: none; margin: 4px 0 14px 0; padding: 0; }}
        .slide-body ul li {{
            padding: 4px 0 4px 16px;
            border-left: 2px solid var(--border);
            line-height: 1.7;
            font-size: 15px;
            margin: 0;
            transition: border-color var(--transition-fast);
        }}
        .slide-body ul li:hover {{
            border-left-color: var(--primary);
        }}
        .slide-body p + ul {{ margin-top: 4px; }}

        /* ═══════════════════════════════════════════
           WATERMARK — 降低透明度到0.08
           ═══════════════════════════════════════════ */
        .watermark-strip {{
            position: fixed;
            top: -120px;
            bottom: -120px;
            z-index: 0;
            pointer-events: none;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            gap: 100px;
            user-select: none;
        }}
        .watermark-strip.left {{
            left: var(--sidebar-w);
            width: calc(50vw - var(--sidebar-w) / 2 - min(30vw, 500px));
        }}
        .watermark-strip.right {{
            right: 0;
            width: calc(50vw - var(--sidebar-w) / 2 - min(30vw, 500px));
        }}
        .wm-line {{
            font-family: var(--font);
            font-size: 12px;
            font-weight: 500;
            color: rgba(0, 0, 0, 0.08);
            white-space: nowrap;
            transform: rotate(-22deg);
            letter-spacing: 4px;
        }}

        /* ═══════════════════════════════════════════
           DISCLAIMER
           ═══════════════════════════════════════════ */
        .disclaimer-slide .slide-body {{ font-size: 14px; }}
        .disclaimer-content h3 {{
            font-size: 15px;
            margin: 16px 0 6px;
            color: var(--ink);
        }}
        .disclaimer-content p {{ margin: 4px 0; line-height: 1.7; font-size: 14px; }}
        .disclaimer-update {{
            margin-top: 20px !important;
            font-family: var(--mono);
            font-size: 12px;
            color: var(--ink-muted);
            letter-spacing: 0.3px;
        }}

        /* ═══════════════════════════════════════════
           LINK DATE
           ═══════════════════════════════════════════ */
        .link-date {{
            font-family: var(--mono);
            font-size: 12px;
            color: var(--ink-muted);
        }}
        .link-action {{
            font-size: 14px;
        }}

        /* ═══════════════════════════════════════════
           SCROLL REVEAL ANIMATION
           ═══════════════════════════════════════════ */
        .reveal {{
            opacity: 0;
            transform: translateY(20px);
            transition: opacity 0.6s cubic-bezier(0.25, 0.1, 0.25, 1),
                        transform 0.6s cubic-bezier(0.25, 0.1, 0.25, 1);
        }}
        .reveal.visible {{
            opacity: 1;
            transform: translateY(0);
        }}

        /* 无障碍：减少动画 */
        @media (prefers-reduced-motion: reduce) {{
            .reveal {{
                opacity: 1;
                transform: none;
                transition: none;
            }}
            .title-slide .label::before {{
                animation: none;
            }}
        }}

        /* ═══════════════════════════════════════════
           RESPONSIVE: MOBILE (<=900px)
           ═══════════════════════════════════════════ */
        @media (max-width: 900px) {{
            body {{ margin-left: 0; font-size: 15px; }}

            .sidebar, .watermark-strip {{
                display: none !important;
            }}

            .scroll-progress {{ left: 0; }}

            /* ── 顶部导航条 (Glass morphism) ── */
            .mobile-topbar {{
                position: fixed; top: 0; left: 0; right: 0;
                z-index: 50;
                height: 48px;
                background: var(--glass-bg);
                backdrop-filter: var(--glass-blur);
                -webkit-backdrop-filter: var(--glass-blur);
                border-bottom: 0.5px solid var(--border);
                padding: 0 16px;
                display: flex;
                align-items: center;
                gap: 12px;
            }}
            .mobile-topbar .logo-text {{
                font-size: 14px;
                font-weight: 700;
                color: var(--ink);
                letter-spacing: -0.01em;
                flex: 1;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .mobile-topbar .page-badge {{
                font-family: var(--mono);
                font-size: 11px;
                color: var(--ink-muted);
                font-weight: 500;
                white-space: nowrap;
                padding: 2px 8px;
                background: var(--surface);
                border-radius: 6px;
            }}
            .mobile-topbar .hamburger {{
                width: 44px; height: 44px;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                gap: 5px;
                cursor: pointer;
                flex-shrink: 0;
                padding: 10px;
            }}
            .mobile-topbar .hamburger span {{
                display: block;
                height: 1.5px;
                background: var(--ink-sec);
                border-radius: 1px;
                transition: all 0.2s;
            }}
            .hamburger.open span:nth-child(1) {{ transform: translateY(6.5px) rotate(45deg); }}
            .hamburger.open span:nth-child(2) {{ opacity: 0; }}
            .hamburger.open span:nth-child(3) {{ transform: translateY(-6.5px) rotate(-45deg); }}

            /* ── 目录抽屉 ── */
            .mobile-toc-overlay {{
                display: block;
                position: fixed; inset: 0;
                background: rgba(0,0,0,0.3);
                z-index: 60;
                opacity: 0; visibility: hidden;
                transition: all 0.2s;
            }}
            .mobile-toc-overlay.open {{ opacity: 1; visibility: visible; }}
            .mobile-toc {{
                display: flex;
                position: fixed;
                top: 0; bottom: 0; left: 0;
                width: 72vw; max-width: 280px;
                background: var(--surface-elevated);
                z-index: 61;
                transform: translateX(-100%);
                transition: transform 0.25s cubic-bezier(0.4,0,0.2,1);
                flex-direction: column;
                box-shadow: var(--shadow-lg);
                border-radius: 0 var(--radius-lg) var(--radius-lg) 0;
            }}
            .mobile-toc.open {{ transform: translateX(0); }}
            .mobile-toc-header {{
                padding: 16px 20px;
                border-bottom: 0.5px solid var(--border);
                font-size: 15px;
                font-weight: 700;
                color: var(--ink);
            }}
            .mobile-toc-nav {{
                flex: 1;
                overflow-y: auto;
                -webkit-overflow-scrolling: touch;
                padding: 8px 0;
            }}
            .mobile-toc-nav .toc-item {{
                display: block;
                padding: 10px 20px;
                color: var(--ink-sec);
                font-size: 14px;
                font-weight: 400;
                text-decoration: none;
                border-left: 2px solid transparent;
                cursor: pointer;
                transition: all var(--transition-fast);
            }}
            .mobile-toc-nav .toc-item:active {{
                background: var(--primary-light);
                color: var(--primary);
                border-left-color: var(--primary);
            }}

            /* ── 内容区域 ── */
            .content-wrapper {{ padding: 0 20px; }}
            .slide-inner {{ padding: 0; }}

            .title-slide {{ padding: 80px 20px 60px; }}
            .title-slide .label {{ font-size: 10px; letter-spacing: 2px; margin-bottom: 20px; }}
            .title-slide h1 {{ font-size: 1.5em; letter-spacing: -0.01em; margin-bottom: 10px; }}
            .title-slide .subtitle {{ font-size: 15px; margin-bottom: 20px; }}
            .title-slide .date {{ font-size: 16px; margin-bottom: 4px; }}
            .title-slide .meta {{ font-size: 11px; }}
            .title-slide .accent-line {{ margin: 20px auto 0; }}
            .signal-legend {{ gap: 12px; margin-top: 24px; }}
            .legend-item {{ font-size: 12px; gap: 6px; }}
            .title-disclaimer {{ margin-top: 20px; font-size: 11px; max-width: 90%; }}

            .chapter-title {{ font-size: 22px; padding: 32px 0 10px; margin-bottom: 4px; }}

            h3 {{ font-size: 16px; margin: 18px 0 6px; }}
            h4 {{ font-size: 14px; margin: 12px 0 3px; }}
            p {{ margin: 6px 0; }}

            .table-scroll {{
                width: 100%;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                margin: 10px 0;
                border: 1px solid var(--border);
                border-radius: var(--radius-md);
                position: relative;
            }}
            .table-scroll::after {{
                content: '';
                position: absolute;
                right: 0; top: 0; bottom: 0;
                width: 32px;
                background: linear-gradient(to right, transparent, var(--bg));
                pointer-events: none;
                z-index: 3;
                border-radius: 0 var(--radius-md) var(--radius-md) 0;
            }}
            .table-scroll table {{
                margin: 0;
                min-width: 480px;
                white-space: nowrap;
                font-size: 13px;
            }}
            .table-scroll th {{ padding: 8px 12px; font-size: 11px; }}
            .table-scroll td {{ padding: 8px 12px; }}
            .table-scroll th:first-child,
            .table-scroll td:first-child {{
                position: sticky;
                left: 0;
                background: inherit;
                z-index: 1;
            }}
            .table-scroll th:first-child {{ background: var(--surface); z-index: 2; }}
            .table-scroll tbody tr:nth-child(even) td:first-child {{ background: rgba(0,0,0,0.01); }}
            .table-scroll tbody tr:hover td:first-child {{ background: var(--primary-light); }}

            table {{ font-size: 13px; border-radius: var(--radius-md); }}
            th, td {{ padding: 8px 12px; }}

            .sig {{ font-size: 11px; }}

            .slide-body ul li {{
                font-size: 14px;
                padding: 3px 0 3px 14px;
                line-height: 1.65;
            }}

            /* 风险卡片移动端 */
            .risk-card {{
                padding: 12px 14px;
                margin: 12px 0;
            }}
            .risk-header .risk-title {{
                font-size: 15px;
                min-width: 150px;
            }}
            .risk-field {{
                grid-template-columns: 80px 1fr;
                gap: 2px 10px;
            }}
            .risk-field .field-label {{
                font-size: 12px;
            }}
            .risk-field .field-value {{
                font-size: 13px;
            }}
            .risk-triggers {{
                grid-template-columns: 1fr;
                gap: 8px;
            }}

            .disclaimer-slide .slide-body {{ font-size: 13px; }}

            .back-to-top {{ bottom: env(safe-area-inset-bottom, 20px); right: 20px; width: 48px; height: 48px; font-size: 14px; }}
        }}

        /* ── 小屏 (< 400px) ── */
        @media (max-width: 400px) {{
            .title-slide h1 {{ font-size: 1.3em; }}
            .title-slide .subtitle {{ font-size: 14px; }}
            .chapter-title {{ font-size: 19px; }}
            body {{ font-size: 14px; }}
            .content-wrapper {{ padding: 0 14px; }}
        }}

        /* ═══════════════════════════════════════════
           PRINT
           ═══════════════════════════════════════════ */
        @media print {{
            .sidebar, .watermark-strip, .title-disclaimer,
            .mobile-topbar, .mobile-toc-overlay, .mobile-toc,
            .scroll-progress, .back-to-top {{ display: none !important; }}
            body {{ margin-left: 0; font-size: 10pt; }}
            .chapter-title {{ border-bottom: 1px solid #ccc; }}
            .slide-inner {{ max-width: 100%; padding: 0; }}
            .slide-body {{ padding: 8px 0; }}
            table {{ font-size: 0.8em; }}
            thead {{ background: var(--surface); }}
            th {{ border-bottom: 1px solid #ccc; color: var(--ink-sec); }}
            .title-slide {{ background: none; }}
            .title-slide h1 {{ color: var(--ink); }}
            .title-slide .label, .title-slide .subtitle {{ color: var(--ink-sec); }}
            .title-slide .meta {{ color: var(--ink-muted); }}
        }}
    </style>
</head>
<body>

    <div class="watermark-strip left">
        <span class="wm-line">OSINT · 公开信息分析</span>
        <span class="wm-line">非官方情报</span>
        <span class="wm-line">AI 辅助生成</span>
        <span class="wm-line">仅供参考 · 研究用途</span>
        <span class="wm-line">非事实认定</span>
        <span class="wm-line">OSINT · 公开信息分析</span>
        <span class="wm-line">非官方情报</span>
        <span class="wm-line">AI 辅助生成</span>
        <span class="wm-line">仅供参考 · 研究用途</span>
        <span class="wm-line">非事实认定</span>
    </div>
    <div class="watermark-strip right">
        <span class="wm-line">OSINT · 公开信息分析</span>
        <span class="wm-line">非官方情报</span>
        <span class="wm-line">AI 辅助生成</span>
        <span class="wm-line">仅供参考 · 研究用途</span>
        <span class="wm-line">非事实认定</span>
        <span class="wm-line">OSINT · 公开信息分析</span>
        <span class="wm-line">非官方情报</span>
        <span class="wm-line">AI 辅助生成</span>
        <span class="wm-line">仅供参考 · 研究用途</span>
        <span class="wm-line">非事实认定</span>
    </div>

    <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <div class="logo">国际局势日报</div>
            <button class="theme-toggle" id="themeToggle" title="切换暗色模式">🌙</button>
        </div>
        <nav class="sidebar-nav" role="navigation" aria-label="章节导航">
{sidebar_items}        </nav>
    </aside>

    <div class="mobile-topbar" id="mobileTopbar">
        <div class="hamburger" id="hamburger" role="button" aria-label="打开目录导航" aria-expanded="false" tabindex="0">
            <span></span><span></span><span></span>
        </div>
        <div class="logo-text">国际局势日报</div>
        <div class="page-badge" id="mobilePageBadge">1/{total_slides}</div>
    </div>

    <div class="mobile-toc-overlay" id="mobileTocOverlay"></div>
    <nav class="mobile-toc" id="mobileToc">
        <div class="mobile-toc-header">目录导航</div>
        <div class="mobile-toc-nav" id="mobileTocNav"></div>
    </nav>

    <div class="scroll-progress" id="scrollProgress"><div class="scroll-progress-bar" id="scrollProgressBar"></div></div>

    <div class="content-wrapper" id="slidesWrapper">

        <section class="title-slide" data-index="0">
            <div class="label">Strategic Intelligence Briefing</div>
            <h1>国际局势日报</h1>
            <div class="subtitle">{subtitle}</div>
            <div class="date">{date}</div>
            <div class="meta">{source_count} sources · {news_count} articles · generated {generated_at}</div>
            <div class="accent-line"></div>
            <div class="signal-legend">
                <span class="legend-item"><span class="sig sig-S">S级</span> 全球战略级变化</span>
                <span class="legend-item"><span class="sig sig-A">A级</span> 重大区域风险</span>
                <span class="legend-item"><span class="sig sig-B">B级</span> 持续观察信号</span>
                <span class="legend-item"><span class="sig sig-C">C级</span> 短期噪音</span>
            </div>
            <div class="title-disclaimer">本报告基于公开信息（OSINT）由 AI 辅助生成，仅用于研究参考，不构成事实认定或专业建议。</div>
        </section>

{slides}
    <section class="slide disclaimer-slide" id="disclaimer" data-index="{disclaimer_index}">
      <div class="slide-inner">
        <h2 class="chapter-title" id="disclaimer"><span class="ch-num">附</span>免责声明</h2>
        <div class="slide-body">
          <div class="disclaimer-content">
            <p>本系统为基于公开信息（OSINT）的自动化国际局势观察与风险分析工具，内容由 AI 模型结合公开新闻、政府声明、智库报告及市场信息生成，<strong>仅用于研究、学习与信息参考用途</strong>。</p>
            <h3>信息性质声明</h3>
            <p>系统输出可能包含未经官方确认的信息、媒体推测与市场传闻、AI 模型的聚类、归因与趋势推演，以及对风险等级、升级信号与事件演化的概率性判断。<strong>上述内容不构成事实认定、官方立场或任何形式的专业建议。</strong></p>
            <h3>不确定性声明</h3>
            <p>系统可能包含未确认消息、媒体推测、模型推演或不完整信息。所有"风险等级""升级信号""趋势判断"均属于概率性分析，不代表事件必然发生。</p>
            <h3>媒体叙事偏差声明</h3>
            <p>不同媒体、政府与智库可能存在叙事偏差、信息筛选与立场差异。本系统旨在比较不同叙事框架，不代表认同任何特定国家、机构或意识形态立场。</p>
            <h3>投资免责声明</h3>
            <p>系统涉及的金融市场、行业、供应链与资产相关分析，仅用于宏观研究与风险观察，<strong>不构成证券、期货、基金或其他金融产品的投资建议</strong>。</p>
            <h3>AI 生成标记</h3>
            <p>本报告内容由 AI 模型自动生成，并经过规则化结构处理。使用者应结合独立信息来源进行判断，并自行承担因使用本系统内容产生的相关风险。</p>
            <p class="disclaimer-update">报告日期：{date} · 生成时间：{generated_at} UTC</p>
          </div>
        </div>
      </div>
    </section>
    </div>

    <button class="back-to-top" id="backToTop" title="回到顶部">↑</button>

    <script>
    (function() {{
        const S = [...document.querySelectorAll('.title-slide, .slide')];
        const N = [...document.querySelectorAll('.nav-item')];
        const progressBar = document.getElementById('scrollProgressBar');
        const backToTop = document.getElementById('backToTop');
        const isMobile = window.matchMedia('(max-width: 900px)').matches;

        // ── 暗色模式切换 ──
        const themeToggle = document.getElementById('themeToggle');
        const savedTheme = localStorage.getItem('theme') || 'light';
        if (savedTheme === 'dark') {{
            document.documentElement.setAttribute('data-theme', 'dark');
            if (themeToggle) themeToggle.textContent = '☀️';
        }}
        if (themeToggle) {{
            themeToggle.addEventListener('click', function() {{
                const current = document.documentElement.getAttribute('data-theme');
                const next = current === 'dark' ? 'light' : 'dark';
                document.documentElement.setAttribute('data-theme', next);
                localStorage.setItem('theme', next);
                this.textContent = next === 'dark' ? '☀️' : '🌙';
            }});
        }}

        // ── 滚动进度条 ──
        function updateProgress() {{
            const scrollTop = window.scrollY;
            const docHeight = document.body.scrollHeight - window.innerHeight;
            const pct = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;
            if (progressBar) progressBar.style.width = pct + '%';
        }}
        window.addEventListener('scroll', updateProgress, {{ passive: true }});

        // ── 回到顶部按钮 ──
        if (backToTop) {{
            window.addEventListener('scroll', function() {{
                backToTop.classList.toggle('visible', window.scrollY > 300);
            }}, {{ passive: true }});
            backToTop.addEventListener('click', function() {{
                window.scrollTo({{ top: 0, behavior: 'smooth' }});
            }});
        }}

        // ── 章节高亮（IntersectionObserver）──
        var observer = new IntersectionObserver(function(entries) {{
            entries.forEach(function(entry) {{
                if (entry.isIntersecting) {{
                    var idx = parseInt(entry.target.getAttribute('data-index'));
                    if (!isNaN(idx)) {{
                        N.forEach(function(n, j) {{ n.classList.toggle('active', j === idx - 1); }});
                    }}
                }}
            }});
        }}, {{ threshold: 0.2 }});
        S.forEach(function(s) {{ observer.observe(s); }});

        // ── Scroll Reveal（IntersectionObserver）──
        var revealObserver = new IntersectionObserver(function(entries) {{
            entries.forEach(function(entry) {{
                if (entry.isIntersecting) {{
                    entry.target.classList.add('visible');
                    revealObserver.unobserve(entry.target);
                }}
            }});
        }}, {{ threshold: 0.12 }});

        document.querySelectorAll('.slide-body').forEach(function(el) {{
            el.classList.add('reveal');
            revealObserver.observe(el);
        }});

        // ── sidebar nav 点击 → scrollTo ──
        N.forEach(function(n, i) {{
            n.addEventListener('click', function(e) {{
                e.preventDefault();
                var target = S[i + 1];
                if (target) {{
                    target.scrollIntoView({{ behavior: 'smooth' }});
                }}
            }});
        }});

        // ── 键盘导航 ──
        document.addEventListener('keydown', function(e) {{
            if (e.key === 'ArrowDown') {{ e.preventDefault(); window.scrollBy(0, window.innerHeight * 0.8); }}
            else if (e.key === 'ArrowUp') {{ e.preventDefault(); window.scrollBy(0, -window.innerHeight * 0.8); }}
            else if (e.key === 'Home') {{ e.preventDefault(); window.scrollTo(0, 0); }}
            else if (e.key === 'End') {{ e.preventDefault(); window.scrollTo(0, document.body.scrollHeight); }}
        }});

        updateProgress();

        // ═══════════════════════════════════════════
        //  移动端
        // ═══════════════════════════════════════════
        if (isMobile) initMobile();

        function initMobile() {{
            wrapTablesForMobile();
            buildMobileToc();
            setupHamburger();
        }}

        function wrapTablesForMobile() {{
            var tables = document.querySelectorAll('.slide-body table');
            tables.forEach(function(table) {{
                if (table.parentElement && table.parentElement.classList.contains('table-scroll')) return;
                var cols = table.querySelectorAll('tr:first-child th, tr:first-child td').length;
                if (cols <= 4) return;
                var wrapper = document.createElement('div');
                wrapper.className = 'table-scroll';
                table.parentNode.insertBefore(wrapper, table);
                wrapper.appendChild(table);
            }});
        }}

        function buildMobileToc() {{
            var tocNav = document.getElementById('mobileTocNav');
            if (!tocNav) return;
            var html = '';
            html += '<div class="toc-item" data-target="0">封面</div>';
            S.forEach(function(s, i) {{
                if (i === 0) return;
                var h2 = s.querySelector('.chapter-title');
                var title = h2 ? h2.textContent.trim() : '章节 ' + i;
                html += '<div class="toc-item" data-target="' + i + '">' + title + '</div>';
            }});
            tocNav.innerHTML = html;

            tocNav.querySelectorAll('.toc-item').forEach(function(item) {{
                item.addEventListener('click', function() {{
                    var target = parseInt(this.getAttribute('data-target'));
                    if (!isNaN(target)) {{
                        var el = S[target];
                        if (el) el.scrollIntoView({{ behavior: 'smooth' }});
                        closeMobileToc();
                    }}
                }});
            }});
        }}

        function setupHamburger() {{
            var hamburger = document.getElementById('hamburger');
            var overlay = document.getElementById('mobileTocOverlay');
            var toc = document.getElementById('mobileToc');
            if (!hamburger || !overlay || !toc) return;

            hamburger.addEventListener('click', function(e) {{
                e.stopPropagation();
                if (toc.classList.contains('open')) closeMobileToc();
                else openMobileToc();
            }});

            overlay.addEventListener('click', closeMobileToc);
            overlay.addEventListener('touchmove', function(e) {{ e.preventDefault(); }});
        }}

        function openMobileToc() {{
            document.getElementById('hamburger').classList.add('open');
            document.getElementById('mobileToc').classList.add('open');
            document.getElementById('mobileTocOverlay').classList.add('open');
        }}

        function closeMobileToc() {{
            document.getElementById('hamburger').classList.remove('open');
            document.getElementById('mobileToc').classList.remove('open');
            document.getElementById('mobileTocOverlay').classList.remove('open');
        }}
    }})();
    </script>

</body>
</html>'''


def _get_localized_template(lang: str) -> str:
    """根据语言返回本地化的 HTML 模板"""
    if lang == "en":
        # 英文版：对中文模板做定向替换
        replacements = {
            '<html lang="zh-CN">': '<html lang="en">',
            # sidebar
            '>国际局势日报</div>': '>Daily Briefing</div>',
            'title="切换暗色模式"': 'title="Toggle Dark Mode"',
            # title slide
            '>Strategic Intelligence Briefing<': '>Strategic Intelligence Briefing<',
            '>国际局势日报</h1>': '>Global Intelligence Briefing</h1>',
            # signal legend
            '全球战略级变化': 'Global Strategic Shift',
            '重大区域风险': 'Major Regional Risk',
            '持续观察信号': 'Sustained Observation',
            '短期噪音': 'Short-Term Noise',
            # title disclaimer
            '本报告基于公开信息（OSINT）由 AI 辅助生成，仅用于研究参考，不构成事实认定或专业建议。':
                'This report is generated with AI assistance based on open source information (OSINT). For research reference only — does not constitute factual determination or professional advice.',
            # watermark
            'OSINT · 公开信息分析': 'OSINT · Open Source Intelligence',
            '非官方情报': 'Unofficial Intelligence',
            'AI 辅助生成': 'AI-Assisted',
            '仅供参考 · 研究用途': 'For Reference · Research Only',
            '非事实认定': 'Not Factual Determination',
            # mobile topbar
            'class="logo-text">国际局势日报': 'class="logo-text">Daily Briefing',
            '>目录导航</div>': '>Table of Contents</div>',
            # disclaimer chapter
            '>免责声明</h2>': '>Disclaimer</h2>',
            '>附</span>免责声明': '>App</span>Disclaimer',
            # disclaimer content
            '本系统为基于公开信息（OSINT）的自动化国际局势观察与风险分析工具，内容由 AI 模型结合公开新闻、政府声明、智库报告及市场信息生成，<strong>仅用于研究、学习与信息参考用途</strong>。':
                'This system is an automated international situation observation and risk analysis tool based on open source information (OSINT). Content is generated by AI models combining public news, government statements, think tank reports, and market information. <strong>For research, study, and informational reference only</strong>.',
            '>信息性质声明</h3>': '>Nature of Information</h3>',
            '系统输出可能包含未经官方确认的信息、媒体推测与市场传闻、AI 模型的聚类、归因与趋势推演，以及对风险等级、升级信号与事件演化的概率性判断。<strong>上述内容不构成事实认定、官方立场或任何形式的专业建议。</strong>':
                'System output may contain unconfirmed information, media speculation and market rumors, AI model clustering, attribution and trend projections, and probabilistic judgments on risk levels, escalation signals, and event evolution. <strong>The above does not constitute factual determination, official stance, or any form of professional advice.</strong>',
            '>不确定性声明</h3>': '>Uncertainty Statement</h3>',
            '系统可能包含未确认消息、媒体推测、模型推演或不完整信息。所有"风险等级""升级信号""趋势判断"均属于概率性分析，不代表事件必然发生。':
                'The system may contain unconfirmed reports, media speculation, model projections, or incomplete information. All "risk levels", "escalation signals", and "trend assessments" are probabilistic analyses and do not represent inevitable events.',
            '>媒体叙事偏差声明</h3>': '>Media Narrative Bias Statement</h3>',
            '不同媒体、政府与智库可能存在叙事偏差、信息筛选与立场差异。本系统旨在比较不同叙事框架，不代表认同任何特定国家、机构或意识形态立场。':
                'Different media, governments, and think tanks may have narrative bias, information filtering, and stance differences. This system aims to compare different narrative frameworks and does not represent endorsement of any specific country, institution, or ideological position.',
            '>投资免责声明</h3>': '>Investment Disclaimer</h3>',
            '系统涉及的金融市场、行业、供应链与资产相关分析，仅用于宏观研究与风险观察，<strong>不构成证券、期货、基金或其他金融产品的投资建议</strong>。':
                'Financial market, industry, supply chain, and asset-related analyses are for macro research and risk observation only. <strong>Does not constitute investment advice for securities, futures, funds, or other financial products.</strong>',
            '>AI 生成标记</h3>': '>AI Generation Notice</h3>',
            '本报告内容由 AI 模型自动生成，并经过规则化结构处理。使用者应结合独立信息来源进行判断，并自行承担因使用本系统内容产生的相关风险。':
                'This report is automatically generated by AI models and processed through rule-based structuring. Users should exercise judgment in conjunction with independent information sources and bear the risks associated with using this system\'s content.',
            '报告日期': 'Report Date',
            '生成时间': 'Generated at',
        }
        result = _HTML_TEMPLATE
        for zh, en in replacements.items():
            result = result.replace(zh, en)
        return result
    return _HTML_TEMPLATE

