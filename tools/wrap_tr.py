# One-off: wraps every plain Cyrillic string literal in voiceforge/ui.py with tr(...).
# f-strings are left alone (they are converted by hand to tr('template {x}', x=...)).
# Rerunnable: literals already inside tr(...) are skipped.
import ast
import pathlib
import re

PATH = pathlib.Path(__file__).resolve().parent.parent / 'voiceforge' / 'ui.py'
CYR = re.compile('[Ѐ-ӿ]')

source = PATH.read_text(encoding='utf-8')
tree = ast.parse(source)
inside = set()
for node in ast.walk(tree):
    if isinstance(node, ast.JoinedStr):
        inside.update(id(v) for v in ast.walk(node))
    if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'tr':
        inside.update(id(a) for a in node.args)

lines = source.splitlines(keepends=True)
offsets = [0]
for line in lines:
    offsets.append(offsets[-1] + len(line.encode('utf-8')))
raw = source.encode('utf-8')
spans = []
for node in ast.walk(tree):
    if (isinstance(node, ast.Constant) and isinstance(node.value, str) and CYR.search(node.value)
            and id(node) not in inside):
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
        spans.append((start, end))
for start, end in sorted(spans, reverse=True):
    raw = raw[:start] + b'tr(' + raw[start:end] + b')' + raw[end:]
PATH.write_bytes(raw)
print(len(spans), 'literals wrapped')
