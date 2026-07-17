"""Static deployment audit. Run: python audit_commands.py"""
from pathlib import Path
import re, py_compile
ROOT = Path(__file__).resolve().parent
for file in ROOT.glob('*.py'):
    py_compile.compile(str(file), doraise=True)
main = (ROOT/'main.py').read_text(encoding='utf-8')
flex = (ROOT/'flex_ui.py').read_text(encoding='utf-8')
buttons = sorted(set(re.findall(r'_message_action\([^,]+,\s*["\'](![^"\']+)', flex)))
allowed_prefixes = ('私訊開始狂歡 ', '切換群組 ', '查看成員ID ', 'VIP操作ID ', '設定VIPID ', '延長VIPID ', '移除VIPID ', '管理商品 ', '成員頁 ')
missing = [cmd for cmd in buttons if cmd[1:] not in main and not cmd[1:].startswith(allowed_prefixes)]
assert not missing, f'Unmapped Flex commands: {missing}'
for required in ('APP_VERSION = "V5.1.1 Permission Fix"', '"群規": "查看群規"', '"等級排行榜": "排行榜資料"', 'if text == "查看群規"', 'if text == "排行榜資料"'):
    assert required in main, f'Missing required route: {required}'
print(f'PASS: compiled all Python files; audited {len(buttons)} fixed Flex commands; no unmapped buttons.')
