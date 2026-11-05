# Lists every string literal with Cyrillic in the UI sources: the set the English table must cover.
# Run: python -I tools/extract_ru.py
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CYR = re.compile('[Ѐ-ӿ]')


def literals(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CYR.search(node.value):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            parts = [v.value for v in node.values if isinstance(v, ast.Constant)]
            if any(CYR.search(p) for p in parts):
                yield node.lineno, 'F-STRING: ' + ' | '.join(parts)


if __name__ == '__main__':
    for name in ('voiceforge/ui.py', 'voiceforge/engine.py', 'voiceforge/project.py', 'voiceforge/fuz_backend.py'):
        for line, text in literals(ROOT / name):
            print(f'{name}:{line}: {text!r}')
