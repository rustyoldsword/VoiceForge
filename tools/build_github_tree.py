# Copies the publishable source into a clean tree for GitHub (default F:/development/VoiceForge), keeping its .git.
# Refuses on control characters or any string from tools/private_strings.txt. Rerunnable.
# Run: .venv/Scripts/python.exe tools/build_github_tree.py [target]
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('F:/development/VoiceForge')
FILES = ['.gitignore', 'LICENSE', 'README.md', 'main.py', 'requirements.txt', 'tests/test_core.py']
DIRS = ['voiceforge', 'tools', 'packaging', 'templates']
SKIP = {'__pycache__', 'private_strings.txt', 'FishAIFree.spec'}


def main():
    private = [w.strip().encode() for w in (ROOT / 'tools' / 'private_strings.txt').read_text(encoding='utf-8').splitlines()
               if w.strip()]
    sources = [ROOT / f for f in FILES]
    for d in DIRS:
        sources += [p for p in (ROOT / d).rglob('*') if p.is_file() and not SKIP & set(p.relative_to(ROOT).parts)]
    problems = []
    for src in sources:
        rel = src.relative_to(ROOT)
        data = src.read_bytes()
        if src.suffix.lower() in ('.py', '.md', '.txt', '.csv', '.json', '.pas', '.spec', '.qss', '.svg') or src.name == '.gitignore':
            bad = sorted({c for c in data if c < 32 and c not in (9, 10, 13)})
            if bad:
                problems.append(f'{rel}: control bytes {bad}')
        problems += [f'{rel}: {w.decode()}' for w in private if w.lower() in data.lower()]
    if problems:
        raise SystemExit('refused:\n' + '\n'.join(problems))
    TARGET.mkdir(parents=True, exist_ok=True)
    for child in TARGET.iterdir():
        if child.name != '.git':
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    for src in sources:
        dst = TARGET / src.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    print(len(sources), 'files ->', TARGET)


if __name__ == '__main__':
    main()
