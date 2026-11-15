# Builds the public release: runs packaging/VoiceForge.spec, assembles dist/VoiceForge with docs, templates, the
# xEdit exporter and licenses, refuses on any private string, then zips it to dist/VoiceForge <version>.zip.
# Rerunnable: dist/VoiceForge is rebuilt by PyInstaller each time; the zip is rewritten.
# Run from the project root: .venv/Scripts/python.exe tools/build_release.py
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voiceforge import __version__  # noqa: E402

XEDIT_SOURCE = ROOT / 'packaging' / 'xedit'
XEDIT_FILES = ['FishAI - Export VoiceForge CSV.pas', 'FishAIExportCore.pas']
# Strings that must never ship (one per line, local only, not in git): tools/private_strings.txt
PRIVATE_FILE = ROOT / 'tools' / 'private_strings.txt'
TEXT = ('.txt', '.md', '.csv', '.json', '.pas', '.qss', '.svg', '.py')

THIRD_PARTY = '''VoiceForge bundles these components:

Qt for Python (PySide6, Shiboken6) 6.12 - LGPLv3. https://www.qt.io/qt-for-python
  Source and license: https://code.qt.io/cgit/pyside/pyside-setup.git ; the Qt libraries are loaded as separate DLLs
  in _internal and can be replaced.
psutil - BSD-3-Clause. https://github.com/giampaolo/psutil
msgpack - Apache-2.0. https://github.com/msgpack/msgpack-python
python-dotenv - BSD-3-Clause. https://github.com/theskumar/python-dotenv
PyInstaller bootloader - GPLv2 with the bootloader exception. https://pyinstaller.org
Atelier UI font - Inter 4.1 renamed, SIL Open Font License 1.1 (AtelierUI-font-OFL.txt).

voices\\ holds short clips of the vanilla Skyrim Special Edition voice types (Bethesda Game Studios), used only as
cloning references for Fish Audio, each with its exact transcript.

Not included and not affiliated: Fish Audio (online service, its own terms), LipGenerator and xWMAEncode
(Bethesda Creation Kit tools), ffmpeg.
'''

# The bundled reference library: the maintainer's verified library (ref_candidates.json + refs\, WAV) converted to
# MP3 192 kbps. The engine re-encodes every reference to MP3 128 kbps before sending it, so nothing is lost.
LOCAL_CONFIG = ROOT / 'tools' / 'voiceforge-project.local.json'
VOICE_CACHE = ROOT / 'build' / 'voices-mp3'


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest().upper()


def bundle_voices(out):
    r"""voices\ref_candidates.json + voices\refs\<vt>\*.mp3 from the local WAV library; converted files are cached."""
    source = Path(json.loads(LOCAL_CONFIG.read_text(encoding='utf-8-sig'))['references'])
    library = json.loads(source.read_text(encoding='utf-8-sig'))
    target = out / 'voices'
    manifest = {}
    for vt, clips in sorted(library.items()):
        entries = []
        for clip in clips:
            folder = clip.get('source_voice_type', vt)
            wav = source.parent / 'refs' / folder / clip['file']
            name = Path(clip['file']).with_suffix('.mp3').name
            cached = VOICE_CACHE / folder / name
            if not cached.is_file() or cached.stat().st_mtime < wav.stat().st_mtime:
                cached.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-b:a', '192k', str(cached)], check=True)
            dest = target / 'refs' / folder / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached, dest)
            entry = {k: v for k, v in clip.items() if k in ('file', 'text', 'formid', 'response', 'seconds',
                                                              'source_voice_type')}
            entry['file'] = name
            entries.append(entry)
        manifest[vt] = entries
    (target / 'ref_candidates.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    files = list((target / 'refs').rglob('*.mp3'))
    print(f'voices: {len(manifest)} voice types, {sum(map(len, manifest.values()))} entries, {len(files)} MP3, '
          f'{sum(f.stat().st_size for f in files) / 1e6:.1f} MB')


def main():
    subprocess.run([sys.executable, '-m', 'PyInstaller', str(ROOT / 'packaging' / 'VoiceForge.spec'), '--noconfirm',
                    '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build')], check=True, cwd=ROOT)
    out = ROOT / 'dist' / 'VoiceForge'
    shutil.copyfile(ROOT / 'README.md', out / 'README.md')
    for name in ('START-EN.txt', 'START-RU.txt'):
        shutil.copyfile(ROOT / 'packaging' / name, out / name)
    shutil.copytree(ROOT / 'templates', out / 'templates', dirs_exist_ok=True)
    xedit = out / 'xEdit Scripts'
    xedit.mkdir(exist_ok=True)
    for name in XEDIT_FILES:
        shutil.copyfile(XEDIT_SOURCE / name, xedit / name)
    for name in ('README.md', 'README-RU.md'):
        shutil.copyfile(ROOT / 'packaging' / 'xedit' / name, xedit / name)
    bundle_voices(out)
    lic = out / 'licenses'
    lic.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'voiceforge' / 'assets' / 'font' / 'OFL.txt', lic / 'AtelierUI-font-OFL.txt')
    (lic / 'THIRD-PARTY.txt').write_text(THIRD_PARTY, encoding='utf-8')

    if not PRIVATE_FILE.is_file():
        raise SystemExit(f'missing {PRIVATE_FILE}: list the private strings to scan for')
    private = [line.strip().encode() for line in PRIVATE_FILE.read_text(encoding='utf-8').splitlines() if line.strip()]
    leaks = []
    for path in out.rglob('*'):
        if path.is_file() and (path.suffix.lower() in TEXT or path.name == 'VoiceForge.exe'
                               or path.parent.name == 'voiceforge'):
            data = path.read_bytes()
            leaks += [f'{path.relative_to(out)}: {p.decode()}' for p in private if p in data]
    if leaks:
        raise SystemExit('private strings found:\n' + '\n'.join(leaks))

    zpath = ROOT / 'dist' / f'VoiceForge {__version__}.zip'
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(out.rglob('*')):
            if path.is_file():
                z.write(path, Path('VoiceForge') / path.relative_to(out))
    print(f'{out}  exe MD5 {md5(out / "VoiceForge.exe")}')
    print(f'{zpath}  {zpath.stat().st_size / 1e6:.1f} MB  MD5 {md5(zpath)}')


if __name__ == '__main__':
    main()
