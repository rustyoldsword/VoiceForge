# Release screenshots of every page on a fictional demo project.
# Qt settings are redirected to a temporary INI file, so the user's real recent projects and monitor paths never
# appear in the pictures. Task states are set directly in the demo database to show a run in progress.
# Rerunnable: an existing demo project is reused.
# Run: .venv\Scripts\python.exe tools\screenshots.py <empty demo dir> <output dir> [en|ru]
import os
import random
import sqlite3
import struct
import sys
import tempfile
import time
from pathlib import Path

LANG = sys.argv[3] if len(sys.argv) > 3 else 'en'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore  # noqa: E402

INI = Path(tempfile.mkdtemp()) / 'settings.ini'


class IsolatedSettings(QtCore.QSettings):
    def __init__(self, *_args):
        super().__init__(str(INI), QtCore.QSettings.Format.IniFormat)


QtCore.QSettings = IsolatedSettings
IsolatedSettings().setValue('language', LANG)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from voiceforge import ui  # noqa: E402
from voiceforge.project import create_project, write_json  # noqa: E402

LINES = {
    'maleguard': ['Stay out of trouble, citizen.', 'I used to be a wanderer, until the road got too long.',
                  'Keep your weapon sheathed inside the city walls.', 'The Jarl pays well for news of bandits.',
                  'Another quiet night. I like it that way.', 'Move along. Nothing to see here.',
                  'You look like you can handle yourself.', 'Report any strange lights near the old mine.',
                  'The gate closes at dusk. Be inside by then.', 'Watch the bridge. The ice is thin this year.'],
    'femalenord': ['Fresh bread from the mill, still warm.', 'My brother went north and never wrote back.',
                   'The tavern fire is lit, if you need warming.', 'Snow again. The goats will hate it.',
                   'Have you seen the merchant from Riverwood?', 'Mind the dog. She bites strangers.',
                   'There is honey mead for anyone who sings.', 'The roads are safer since the patrol came.',
                   'Bring me three wolf pelts and I will pay you.'],
    'maleelfhaughty': ['Your accent betrays a provincial upbringing.', 'Knowledge is wasted on the impatient.',
                       'I have read every scroll in this library twice.', 'Do not touch the crystals. They remember.',
                       'A fair price, for those who know its worth.', 'The college does not welcome idle visitors.',
                       'Speak plainly or not at all.', 'Time has been kinder to my kind than to yours.'],
    'femalecommander': ['Form up! We march at first light.', 'Every soldier comes home. That is the order.',
                        'Scouts report movement on the eastern ridge.', 'Sharpen your blades, the pass is not ours yet.',
                        'You did well today. Rest while you can.', 'Hold the line until the signal fire burns.',
                        'Supplies arrive tomorrow. Ration until then.'],
}
STATES = ['done'] * 22 + ['running'] * 3 + ['wav_ready'] * 2 + ['failed'] + ['pending'] * 6


def silent_wav(path, seconds=1.5, rate=44100):
    frames = int(seconds * rate)
    data = b'\0\0' * frames
    path.write_bytes(b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVEfmt ' +
                     struct.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16) + b'data' + struct.pack('<I', len(data)) + data)


def build_demo(root):
    refs_dir = root.parent / 'Voice Library'
    if refs_dir.exists():
        raise SystemExit(f'{refs_dir} already exists: choose a new demo location')
    refs = {}
    for vt in LINES:
        (refs_dir / 'refs' / vt).mkdir(parents=True)
        refs[vt] = []
        for n in (1, 2, 3):
            name = f'{vt}_reference_{n:02d}.wav'
            silent_wav(refs_dir / 'refs' / vt / name)
            refs[vt].append(dict(file=f'refs/{vt}/{name}', text=f'Exact transcript of {vt} reference {n}.'))
    write_json(refs_dir / 'ref_candidates.json', refs)
    jobs = []
    for vt, lines in LINES.items():
        for n, text in enumerate(lines, 1):
            local = 0x800 + n
            jobs.append(dict(id=f'{vt}_{n:03d}', vt=vt, text=text,
                             targets=[f'Sound/Voice/ExampleVoicePack.esp/{vt}/examplequest_dialogue_{local:08x}_1.fuz']))
    create_project(root, 'Example Voice Pack', jobs, references=str(refs_dir / 'ref_candidates.json'))
    rng = random.Random(7)
    states = STATES[:]
    rng.shuffle(states)
    now = time.time()
    with sqlite3.connect(root / 'studio.sqlite') as db:
        for job, state in zip(jobs, states):
            stage = {'done': 'fuz', 'running': 'fish', 'wav_ready': 'lip', 'failed': 'fish', 'pending': ''}[state]
            error = 'HTTP 429 rate limit - retry scheduled' if state == 'failed' else None
            qa = '{"duration": 2.4, "lip": "ok"}' if state == 'done' else None
            db.execute('UPDATE tasks SET status=?, stage=?, error=?, qa=?, updated=? WHERE id=?',
                       (state, stage, error, qa, now - rng.random() * 600, job['id']))
    return root


def main():
    demo, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    if not (demo / 'project.json').is_file():  # reuse a demo this script made earlier
        build_demo(demo)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    ui.apply_theme(app)
    window = ui.Window(demo)
    window.resize(1440, 900)
    window.show()
    window.load_project(demo)
    for message in ('Fish: 3 parallel requests, model s2.1-pro-free',
                    'LIP: 4 workers, private LipGenerator copies', 'Verified 22 FUZ files: 0 problems'):
        window.add_log(message)
    pages = ['overview', 'queue', 'references', 'monitor', 'settings']
    shots = []

    def shoot(index=0):
        if index:
            for task in ('status_task', 'monitor_task'):
                t = getattr(window, task, None)
                if t:
                    t.wait(10000)
            app.processEvents()
            path = out / f'voiceforge-{LANG}-{index}-{pages[index - 1]}.png'
            window.grab().save(str(path))
            shots.append(path)
        if index == len(pages):
            window.timer.stop()
            app.quit()
            return
        window.nav.setCurrentRow(index)
        if pages[index] == 'references':
            window.show_references()
        QTimer.singleShot(1800, lambda: shoot(index + 1))

    QTimer.singleShot(5000, shoot)
    app.exec()
    for path in shots:
        print(path)


if __name__ == '__main__':
    main()
