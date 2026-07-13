import sys
sys.path.insert(0, '.')
from src.generator import _transform_watch_cards

html = '<h3>未来48小时关键观察哨</h3>\n'
html += '<p><strong>观察项</strong>: 霍尔木兹船袭频率<br>\n'
html += '<strong>当前状态</strong>: 24小时内3起袭击<br>\n'
html += '<strong>升级触发</strong>: 若布伦特突破$100/桶<br>\n'
html += '<strong>缓和触发</strong>: 若袭击频率降至0<br>\n'
html += '<strong>监控方式</strong>: UKMTO</p>\n'
html += '<p><strong>观察项</strong>: 北约峰会措辞<br>\n'
html += '<strong>当前状态</strong>: 草案讨论中<br>\n'
html += '<strong>升级触发</strong>: 若联合公报点名中国<br>\n'
html += '<strong>监控方式</strong>: 北约官网</p>\n'

result = _transform_watch_cards(html, lang='zh')
print('Grid:', 'watch-grid' in result)
print('Cards:', result.count('watch-card'))
print(result[:800])
