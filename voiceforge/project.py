from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path, PureWindowsPath

MODEL = 's2.1-pro-free'
DEFAULT_TOOLS = ''  # Choose your own tools directory in Settings.
DEFAULT_SECRET = ''  # Use FISH_API_KEY from the environment or select a local secret file.


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def read_refs(path):
    """ref_candidates.json keyed by lowercase VoiceType (job VoiceTypes are lowercased on import)."""
    refs = {}
    for vt, clips in (read_json(path) if path else {}).items():
        if vt.lower() in refs:
            raise ValueError(f'Duplicate VoiceType in references: {vt}')
        refs[vt.lower()] = clips
    return refs


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def relative(value):
    value = str(value).replace('\\', '/')
    win = PureWindowsPath(value)
    if win.drive or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError(f'Unsafe relative path: {value}')
    if any(':' in p or p.endswith((' ', '.')) for p in value.split('/')):
        raise ValueError(f'Invalid Windows path: {value}')
    return value


@lru_cache(maxsize=64)
def _resolved(root):
    return Path(root).resolve()


def contained(root, value):
    # relative() already refuses drives, absolute paths and '', '.', '..' parts, so the joined path
    # cannot leave root. No per-path resolve(): on Windows it walks the file system each time, and
    # opening a project with 190,000 FUZ targets took two minutes.
    return _resolved(str(root)) / relative(value)


def validate_jobs(jobs):
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('Manifest must contain a nonempty list of jobs')
    identifiers, targets = set(), {}
    for job in jobs:
        ident = str(job['id'])
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,180}', ident) or ident.lower() in identifiers:
            raise ValueError(f'Invalid or duplicate job id: {ident}')
        identifiers.add(ident.lower())
        if not isinstance(job.get('text'), str) or not job['text'].strip():
            raise ValueError(f'{ident}: empty text')
        if not isinstance(job.get('vt'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', job['vt']):
            raise ValueError(f'{ident}: missing or invalid VoiceType')
        if not isinstance(job.get('targets'), list) or not job['targets']:
            raise ValueError(f'{ident}: no FUZ targets')
        local = set()
        for target in job['targets']:
            clean = relative(target)
            if not clean.lower().endswith('.fuz'):
                raise ValueError(f'{ident}: target must be .fuz')
            key = clean.casefold()
            if key in local or key in targets:
                raise ValueError(f'Duplicate FUZ destination: {target}')
            local.add(key)
            targets[key] = ident
    return jobs


def manifest_hash(jobs):
    return hashlib.sha256(json.dumps(jobs, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def csv_rows(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(stream, dialect=dialect))


def jobs_from_csv(lines_file, paths_file):
    lines = {}
    for row in csv_rows(lines_file):
        key = (row['info_formid'].casefold(), str(int(row['response_number'])))
        if key in lines:
            raise ValueError(f'Duplicate INFO response in lines CSV: {key}')
        lines[key] = row
    grouped, skipped = {}, []
    for row in csv_rows(paths_file):
        key = (row['info_formid'].casefold(), str(int(row['response_number'])))
        if key not in lines:
            raise ValueError(f'Path has no matching INFO response: {key}')
        line = lines[key]
        target = relative(row['game_path (relative to Data)'])
        if not target.lower().startswith('sound/voice/') or len(target.split('/')) != 5:
            raise ValueError(f'Expected Sound/Voice/Plugin/VoiceType/file.fuz: {target}')
        vt = row['voice_type'].lower()
        if target.split('/')[-2].casefold() != vt.casefold():
            raise ValueError(f'VoiceType does not match path: {target}')
        if line.get('needs_voice', 'yes').lower() not in ('yes', 'true', '1'):
            skipped.append(target)
            continue
        text = line.get('voice_text (stage directions removed)', line.get('text', ''))
        if not text.strip():
            raise ValueError(f'{key}: empty spoken text')
        grouped.setdefault((vt, text), set()).add(target)
    jobs = []
    for (vt, text), targets in sorted(grouped.items()):
        digest = hashlib.sha256((vt + '\0' + text).encode()).hexdigest()[:20]
        jobs.append(dict(id=f'{vt}_{digest}', vt=vt, text=text, targets=sorted(targets)))
    return validate_jobs(jobs), skipped


@contextmanager
def connection(project):
    db = sqlite3.connect(str(Path(project) / 'studio.sqlite'), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=30000')
    try:
        with db:
            yield db
    finally:
        db.close()


def create_project(root, name, jobs, *, references='', raw_source='', target_prefix='', skipped=None):
    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError('Choose a new empty project directory')
    # Imported manifests are copied; source files and old outputs are never edited.
    normalized = []
    for job in jobs:
        item = {k: job[k] for k in ('id', 'vt', 'text', 'targets')}
        item['vt'] = item['vt'].lower()
        item['targets'] = [relative(f'{target_prefix}/{t}' if target_prefix else t) for t in item['targets']]
        normalized.append(item)
    validate_jobs(normalized)
    root.mkdir(parents=True, exist_ok=True)
    for folder in ('wav_raw', 'cache', 'output', 'reports', 'backups'):
        (root / folder).mkdir(exist_ok=True)
    config = dict(version=1, name=name, model=MODEL, tools=DEFAULT_TOOLS, secret=DEFAULT_SECRET,
                  references=str(Path(references).resolve()) if references else '',
                  raw_source=str(Path(raw_source).resolve()) if raw_source else '',
                  fish_workers=3, lip_workers=4, xwm_workers=4, cpu_limit=75, temp_limit=0,
                  auto_retries=1, pad_min_duration=0.5, created=time.time(), manifest_sha256=manifest_hash(normalized))
    write_json(root / 'project.json', config)
    write_json(root / 'jobs.json', normalized)
    write_json(root / 'reports' / 'import.json', dict(skipped=skipped or [], jobs=len(normalized),
               targets=sum(len(j['targets']) for j in normalized), source_wavs_trusted=bool(raw_source)))
    with connection(root) as db:
        db.executescript('''CREATE TABLE tasks(id TEXT PRIMARY KEY, vt TEXT, text TEXT, status TEXT,
            stage TEXT, error TEXT, attempts INTEGER DEFAULT 0, updated REAL, qa TEXT);
            CREATE INDEX task_status ON tasks(status);
            CREATE TABLE events(time REAL, job TEXT, message TEXT);''')
        db.executemany('INSERT INTO tasks(id,vt,text,status,updated) VALUES(?,?,?,?,?)',
                       [(j['id'], j['vt'], j['text'], 'pending', time.time()) for j in normalized])
    return root


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = read_json(self.root / 'project.json')
        if self.config.get('model') != MODEL:
            raise ValueError('Only the explicitly selected free model is supported')
        for key, low, high in [('fish_workers', 1, 3), ('lip_workers', 1, 32), ('xwm_workers', 1, 32),
                              ('cpu_limit', 10, 100), ('auto_retries', 0, 3), ('temp_limit', 0, 100)]:
            if not low <= int(self.config.get(key, low)) <= high:
                raise ValueError(f'Invalid setting: {key}')
        self.jobs = validate_jobs(read_json(self.root / 'jobs.json'))
        if manifest_hash(self.jobs) != self.config.get('manifest_sha256'):
            raise ValueError('Manifest changed after import. Import revised input into a new project.')
        self.by_id = {j['id']: j for j in self.jobs}
        # Prevent changing text under an existing cache identity.
        with connection(self.root) as db:
            stored = {r['id']: (r['vt'], r['text']) for r in db.execute('SELECT id,vt,text FROM tasks')}
        if len(stored) != len(self.jobs) or any(stored.get(j['id']) != (j['vt'], j['text']) for j in self.jobs):
            raise ValueError('Manifest changed after import. Create a new project for revised text.')
        for j in self.jobs:
            for target in j['targets']:
                contained(self.root / 'output', target)

    def save(self):
        write_json(self.root / 'project.json', self.config)

    def statuses(self):
        with connection(self.root) as db:
            return {r['id']: dict(r) for r in db.execute('SELECT id,status,stage,error,qa FROM tasks')}

    def counts(self):
        with connection(self.root) as db:
            return dict(db.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status').fetchall())

    def set(self, ident, **fields):
        allowed = {'status', 'stage', 'error', 'qa', 'attempts'}
        if not set(fields) <= allowed:
            raise ValueError('Unknown task field')
        fields['updated'] = time.time()
        with connection(self.root) as db:
            db.execute('UPDATE tasks SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?',
                       (*fields.values(), ident))

    def event(self, ident, message):
        with connection(self.root) as db:
            db.execute('INSERT INTO events VALUES(?,?,?)', (time.time(), ident, message))

    def wav(self, job):
        own = self.root / 'wav_raw' / (job['id'] + '.wav')
        if own.exists() or not self.config.get('raw_source') or job['id'] in self.config.get('ignore_source_wavs', []):
            return own
        return Path(self.config['raw_source']) / own.name
