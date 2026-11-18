"""The application owns its queue; no Codex/Claude/MCP process is used at runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from contextlib import closing
from pathlib import Path

import msgpack
from dotenv import dotenv_values

from . import fuz_backend as fuz
from .project import MAX_FISH_WORKERS, MODEL, Project, connection, contained, project_refs, read_json, references_path, relative, write_json

API = 'https://api.fish.audio/v1/tts'


class Halt(Exception):
    pass


class FatalAPI(Exception):
    pass


class ProjectLock:
    """OS lock released on crash; prevents two application instances sharing one queue."""
    def __init__(self, root):
        self.path = Path(root) / 'run.lock'

    def __enter__(self):
        import msvcrt
        self.stream = self.path.open('a+b')
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.stream.close()
            raise RuntimeError('This project is already running in another instance') from None
        return self

    def __exit__(self, *args):
        import msvcrt
        self.stream.seek(0)
        msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()


def valid_wav(path):
    try:
        info = fuz.parse_wav(str(path))
        return bool(info and info['size'] > 0 and info['rate'] > 0 and info['channels'] > 0)
    except (OSError, ValueError, ZeroDivisionError):
        return False


class FishClient:
    def __init__(self, config, stop, emit):
        self.config, self.stop, self.emit = config, stop, emit
        self.gate = threading.Semaphore(max(1, min(MAX_FISH_WORKERS, int(config['fish_workers']))))
        self.cache, self.lock = {}, threading.Lock()
        self.key = None
        self.key_lock = threading.Lock()
        self.refs = None

    def references(self, vt):
        with self.lock:
            if self.refs is None:
                self.refs = project_refs(self.config)
            if vt in self.cache:
                return self.cache[vt]
            candidates = self.refs.get(vt)
            if not candidates:
                raise ValueError(f'No references for {vt}')
            result = []
            root = references_path(self.config).parent / 'refs'
            for candidate in candidates:
                if self.stop.is_set():
                    raise Halt()
                source = contained(root, relative(candidate.get('source_voice_type', vt)) + '/' + relative(candidate['file']))
                if not candidate.get('text', '').strip():
                    raise ValueError(f'Reference transcript missing: {vt}')
                completed = subprocess.run([str(Path(self.config['tools']) / 'ffmpeg.exe'), '-v', 'error',
                    '-i', str(source), '-f', 'mp3', '-b:a', '128k', 'pipe:1'], capture_output=True,
                    timeout=60, creationflags=fuz.CREATE_NO_WINDOW)
                if completed.returncode or not completed.stdout:
                    raise RuntimeError(f'Cannot decode reference for {vt}')
                result.append(dict(audio=completed.stdout, text=candidate['text']))
            self.cache[vt] = result
            return result

    def generate(self, job, output):
        # Read once, under the lock, and publish only the finished value: requests that start together must not
        # see the empty placeholder while the first one is still reading the key file.
        with self.key_lock:
            if self.key is None:
                key = os.environ.get('FISH_API_KEY', '')
                if not key and self.config.get('secret'):
                    key = dotenv_values(self.config['secret']).get('FISH_API_KEY', '') or ''
                self.key = key
        if not self.key:
            raise FatalAPI('FISH_API_KEY is missing; select the existing .env.local in Settings')
        # The model cannot be overridden by an environment variable or a failed request.
        with self.gate:
            if self.stop.is_set():
                raise Halt()
            payload = dict(text=job['text'], format='wav', sample_rate=44100, normalize=False,
                           latency='normal', temperature=0.7, top_p=0.7)
            reference_id = self.config.get('voice_ids', {}).get(job['vt'])
            if reference_id:
                payload['reference_id'] = reference_id
            else:
                payload['references'] = self.references(job['vt'])
            body = msgpack.packb(payload, use_bin_type=True)
            for attempt in range(7):
                if self.stop.is_set():
                    raise Halt()
                request = urllib.request.Request(API, data=body, headers={
                    'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/msgpack', 'model': MODEL})
                try:
                    with urllib.request.urlopen(request, timeout=180) as response:
                        data = response.read(100 * 1024 * 1024 + 1)
                    if len(data) > 100 * 1024 * 1024 or not data.startswith(b'RIFF'):
                        raise RuntimeError('Fish returned a non-WAV or oversized response')
                    temp = Path(str(output) + '.part')
                    temp.write_bytes(data)
                    if not valid_wav(temp):
                        raise RuntimeError('Fish returned an empty or invalid PCM WAV')
                    temp.replace(output)
                    return
                except urllib.error.HTTPError as error:
                    code = error.code
                    error.close()
                    # Do not log response bodies: they can include private request text.
                    if code in (401, 402):
                        self.stop.set()
                        raise FatalAPI(f'HTTP {code}: generation stopped; model remains {MODEL}') from None
                    if code != 429 and code < 500:
                        raise RuntimeError(f'Fish HTTP {code}') from None
                    last = f'HTTP {code}'
                except (OSError, TimeoutError):
                    last = 'Network error or timeout'
                if attempt < 6:
                    delay = min(60, 2 ** attempt) + random.random()
                    self.emit(dict(kind='log', message=f'{job["id"]}: {last}; retry {attempt+1}/6'))
                    if self.stop.wait(delay):
                        raise Halt()
            raise RuntimeError(f'Fish retries exhausted: {last}')


def builder_args(project):
    c = project.config
    return argparse.Namespace(tools=c['tools'], lip_workers=int(c['lip_workers']),
        xwm_workers=int(c['xwm_workers']), workers=int(c['lip_workers']) + int(c['xwm_workers']),
        priority='below_normal', cache=str(project.root / 'cache'), no_link=True, keep_cache=False,
        lip_timeout=60, xwm_timeout=120, lip_original_attempts=2, lip_sanitized_attempts=3,
        xwm_attempts=3, pad_min_duration=float(c.get('pad_min_duration', 0.5)))


def finished_on_disk(project, jobs):
    """Ids of jobs the queue already marks done whose FUZ files are all still there.

    Only a stat per target, no file is opened: reopening every finished WAV and FUZ on a restart kept 63,000
    finished lines busy for about ten minutes before the first new request. Their content is checked by the
    verification at the end of a full run and by Verify all FUZ."""
    status = project.statuses()
    out = project.root / 'output'
    finished = set()
    for job in jobs:
        if status.get(job['id'], {}).get('status') != 'done':
            continue
        try:
            if all(os.stat(contained(out, t)).st_size > 12 for t in job['targets']):
                finished.add(job['id'])
        except OSError:
            pass
    return finished


def preflight(project, jobs, mode):
    tools = Path(project.config['tools'])
    required = ['ffmpeg.exe'] if mode == 'samples' else ['ffmpeg.exe', 'LipGenerator.exe', 'xWMAEncode.exe', 'FonixData.cdf']
    missing = [name for name in required if not (tools / name).is_file()]
    if missing:
        if not project.config['tools']:
            raise ValueError('Choose the tools folder (LipGenerator, xWMAEncode, ffmpeg) in 05 Settings and press Save settings')
        raise ValueError(f"Missing in the tools folder {tools}: {', '.join(missing)}. Fix it in 05 Settings")
    pending = [j for j in jobs if not valid_wav(project.wav(j)) and not (
        mode != 'samples' and all(fuz.target_ok(str(contained(project.root / 'output', t))) for t in j['targets']))]
    if pending:
        inline = [j for j in pending if not project.config.get('voice_ids', {}).get(j['vt'])]
        if inline and not references_path(project.config).is_file():
            raise ValueError(f'Reference library not found: {references_path(project.config)}. '
                             'Choose ref_candidates.json in 03 Voice references')
        refs = project_refs(project.config) if inline else {}
        absent = sorted({j['vt'] for j in inline if not refs.get(j['vt'])})
        if absent:
            raise ValueError('VoiceTypes without references: ' + ', '.join(absent))
        root = references_path(project.config).parent / 'refs'
        for vt in {j['vt'] for j in inline}:
            for ref in refs[vt]:
                path = contained(root, relative(ref.get('source_voice_type', vt)) + '/' + relative(ref['file']))
                if not path.is_file() or not ref.get('text', '').strip():
                    raise ValueError(f'{vt}: reference audio or transcript missing')
    free = shutil.disk_usage(project.root).free
    if free < 512 * 1024 * 1024:
        raise ValueError('Less than 512 MB of free disk space')


class Pipeline:
    def __init__(self, project, emit=lambda value: None):
        self.project, self.emit = project, emit
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.fatal = None
        self.finished = 0
        self.started = 0

    def log(self, message, ident=''):
        self.project.event(ident, message)
        self.emit(dict(kind='log', message=message))

    def checkpoint(self):
        while self.paused.is_set() and not self.stop.is_set():
            self.stop.wait(0.2)
        if self.stop.is_set():
            raise Halt()

    def process(self, job, mode, client, builder):
        p, ident = self.project, job['id']
        try:
            self.checkpoint()
            # Every success is reconciled against disk, rather than trusting a counter.
            if mode != 'samples' and all(fuz.target_ok(str(contained(p.root / 'output', t))) for t in job['targets']):
                p.set(ident, status='done', stage='complete', error=None)
                return
            if mode == 'build' and not valid_wav(p.wav(job)):
                raise RuntimeError('Missing valid WAV; choose Full pipeline')
            if not valid_wav(p.wav(job)):
                p.set(ident, status='running', stage='fish', error=None)
                client.generate(job, p.root / 'wav_raw' / (ident + '.wav'))
            if mode == 'samples':
                p.set(ident, status='wav_ready', stage='wav', error=None,
                      qa=json.dumps(fuz.quality(str(p.wav(job)), job['text'])))
                return
            self.checkpoint()
            p.set(ident, status='running', stage='lip_xwm', error=None)
            mod = dict(name='project', raw=str(p.wav(job).parent), destination=str(p.root / 'output'))
            result = builder.process(mod, job)
            if result == 'failed':
                row = builder.state.query('SELECT error FROM jobs WHERE mod=? AND id=?', ('project', ident))
                raise RuntimeError(row[0][0] if row else 'FUZ assembly failed')
            qa_row = builder.state.query('SELECT duration,wps,lead,tail,peak,flags FROM qa WHERE mod=? AND id=?', ('project', ident))
            qa = dict(zip(('duration', 'wps', 'lead', 'tail', 'peak', 'flags'), qa_row[0])) if qa_row else {}
            if isinstance(qa.get('flags'), str):
                qa['flags'] = json.loads(qa['flags'])
            p.set(ident, status='done', stage='complete', error=None, qa=json.dumps(qa))
        except Halt:
            p.set(ident, status='pending', stage=None)
        except FatalAPI as error:
            self.fatal = str(error)
            self.stop.set()
            p.set(ident, status='failed', stage='fish', error=str(error))
            self.log(str(error), ident)
        except Exception as error:
            message = f'{type(error).__name__}: {error}'
            p.set(ident, status='failed', error=message)
            self.log(f'{ident}: {message}', ident)
        finally:
            self.emit(dict(kind='progress', id=ident))

    def run(self, mode='full', selected=None):
        p = self.project
        with ProjectLock(p.root):
            with connection(p.root) as db:
                db.execute("UPDATE tasks SET status='pending' WHERE status='running'")
            jobs = [p.by_id[x] for x in selected] if selected is not None else p.jobs
            if mode != 'samples':
                self.log(f'Checking {len(jobs)} lines against the output folder')
                finished = finished_on_disk(p, jobs)
                if finished:
                    jobs = [j for j in jobs if j['id'] not in finished]
                    self.log(f'{len(finished)} lines are already finished; {len(jobs)} left')
                    if not jobs:
                        self.log('Nothing left to do')
                        return
            preflight(p, jobs, mode)
            self.log(f'Start {mode}: {len(jobs)} jobs; model={MODEL}')
            self.started = time.monotonic()
            args = builder_args(p)
            state = fuz.State(str(p.root / 'backend.sqlite'))
            try:
                builder = fuz.Builder(args, state) if mode != 'samples' else None
            except Exception:
                state.close()
                raise
            monitor = fuz.Monitor(builder.heavy if builder else fuz.Gate(1), float(p.config['cpu_limit']),
                float(p.config.get('temp_limit', 0)) or None, state.commit)
            client = FishClient(p.config, self.stop, self.emit)
            monitor.start()
            try:
                for cycle in range(int(p.config.get('auto_retries', 1)) + 1):
                    if cycle:
                        status = p.statuses()
                        jobs = [j for j in jobs if status[j['id']]['status'] == 'failed']
                        if not jobs:
                            break
                        self.log(f'Automatic retry {cycle}: {len(jobs)} jobs')
                    # Room for every Fish request plus the LipGenerator/xWMAEncode workers, so neither side starves the other.
                    count = max(1, int(p.config['fish_workers']) + args.lip_workers + args.xwm_workers)
                    with ThreadPoolExecutor(max_workers=count) as executor:
                        pending = set()
                        for job in jobs:
                            try:
                                self.checkpoint()
                            except Halt:
                                break
                            pending.add(executor.submit(self.process, job, mode, client, builder))
                            if len(pending) >= count:
                                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                                for future in done:
                                    future.result()
                        for future in pending:
                            future.result()
                    if self.stop.is_set():
                        break
                if not self.stop.is_set() and mode != 'samples':
                    self.log('File verification started')
                    report = verify(p, self.emit, self.stop)
                    self.log('Verification: ' + json.dumps(report['counts']))
            finally:
                monitor.stop.set()
                monitor.join(timeout=10)
                state.close()
                if builder:
                    builder.close()
            self.log(self.fatal or ('Stopped; queue saved' if self.stop.is_set() else 'Run finished'))


def verify(project, emit=lambda value: None, stop=None):
    expected = {t.casefold(): t for j in project.jobs for t in j['targets']}
    actual, invalid, linked, partials = set(), [], [], []
    root = project.root / 'output'
    for index, path in enumerate(root.rglob('*')):
        if stop and stop.is_set():
            raise Halt()
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix().casefold()
        if rel.endswith('.part'):
            partials.append(rel)
        if rel.endswith('.fuz'):
            actual.add(rel)
            if not fuz.target_ok(str(path)):
                invalid.append(rel)
            if path.stat().st_nlink > 1:
                linked.append(rel)
        if index % 2000 == 0:
            emit(dict(kind='log', message=f'QA: scanned {index} filesystem entries'))
    details = dict(missing=sorted(set(expected) - actual), unexpected=sorted(actual - set(expected)),
                   invalid=invalid, hardlinked=linked, partial=partials)
    counts = {k: len(v) for k, v in details.items()}
    report = dict(time=time.time(), expected=len(expected), actual=len(actual), counts=counts,
                  clean=not any(counts.values()), details=details, note='Container checks do not certify speech accuracy or in-game playback.')
    write_json(project.root / 'reports' / 'verification.json', report)
    return report


def regenerate(project, identifiers):
    """Preserve former assets in backups before selective regeneration."""
    with ProjectLock(project.root):
        stamp = str(time.time_ns())
        backup = project.root / 'backups' / stamp
        for ident in identifiers:
            job = project.by_id[ident]
            own = project.root / 'wav_raw' / (ident + '.wav')
            items = [(own, Path('wav_raw') / own.name)]
            items += [(contained(project.root / 'output', t), Path('output') / t) for t in job['targets']]
            cache = project.root / 'cache' / 'project' / ident
            if cache.exists():
                items.append((cache, Path('cache') / ident))
            for source, rel in items:
                if source.exists():
                    dest = backup / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(dest))
            project.set(ident, status='pending', stage=None, error=None, qa=None)
        # Per-job bypass avoids reusing an imported old WAV for regenerated jobs.
        ignored = set(project.config.get('ignore_source_wavs', [])) | set(identifiers)
        project.config['ignore_source_wavs'] = sorted(ignored)
        project.save()


def legacy_snapshot(path):
    """Read external live states using mode=ro (including their WAL); never recover or mutate them."""
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        counts = db.execute('SELECT mod,status,COUNT(*) FROM jobs GROUP BY mod,status').fetchall()
        errors = db.execute("SELECT id,error FROM jobs WHERE status='failed' LIMIT 20").fetchall()
        recent = db.execute('SELECT MAX(updated) FROM jobs').fetchone()[0]
    return dict(counts=counts, errors=errors, updated=recent)
