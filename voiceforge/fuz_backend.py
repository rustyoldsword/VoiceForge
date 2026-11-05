"""FUZ generator v2: pack ready Fish WAVs into Skyrim FUZ (LIP + XWM) for one or more mods.

Per job: check every target -> restore missing targets from an existing valid FUZ -> prepare WAV
(ffmpeg only when it is not mono/PCM16/44100) -> streaming QA -> LipGenerator -> xWMAEncode ->
FUZE container -> atomic writes (.part + os.replace) to exactly the targets[] paths.

Durable state lives in SQLite (WAL). Intermediate files are cached per job in --cache until the
job is done, so a crash resumes from the next unfinished stage. Standard library only.

    python fuzgen.py run --config mods.json [--lip-workers 4 --xwm-workers 4 ...]
    python fuzgen.py verify --config mods.json
    python fuzgen.py status --config mods.json
"""
import argparse
import array
import ctypes
import json
import os
import queue
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOOLS = ''  # Configure the tools directory explicitly.
XWM_BITRATE = '48000'
LOUD = 1000
STAGE_DIRECTIONS = re.compile(r'\[[^\]]*(?:\]|$)')
PRIORITY_CLASSES = {'idle': 0x40, 'below_normal': 0x4000, 'normal': 0x20}
CREATE_NO_WINDOW = 0x08000000
FONIX_MUTEX_NAME = b'FonixMemoryMutex'
MIN_SECONDS = 0.4  # LipGenerator writes no .lip for shorter clips
# Leading silence tried in order until the lip does not start late. LipGenerator is not
# deterministic, so values repeat. LipGen and xWMAEncode always get the same padded clip.
LEAD_MS = (150, 120, 180, 100, 150, 200, 80, 150)


# --------------------------------------------------------------------------- Windows helpers

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]


kernel32.GetCurrentProcess.restype = wintypes.HANDLE
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
kernel32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def peak_rss(handle=None):
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    handle = kernel32.GetCurrentProcess() if handle is None else handle
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return 0
    return counters.PeakWorkingSetSize


def system_times():
    """(idle, total) in 100 ns ticks, system-wide."""
    idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
    return ticks(idle), ticks(kern) + ticks(user)  # kernel time already includes idle


def configure_process(priority, affinity_mask):
    """Children inherit both the affinity mask and an idle/below-normal priority class."""
    me = kernel32.GetCurrentProcess()
    if affinity_mask and not kernel32.SetProcessAffinityMask(me, affinity_mask):
        raise OSError(ctypes.get_last_error(), f'SetProcessAffinityMask(0x{affinity_mask:x}) failed')
    if not kernel32.SetPriorityClass(me, PRIORITY_CLASSES[priority]):
        raise OSError(ctypes.get_last_error(), 'SetPriorityClass failed')


# --------------------------------------------------------------------------- FUZ / WAV helpers

def fuz_header_ok(header, size):
    if len(header) < 12 or size < 13:
        return False
    version, lip_size = struct.unpack('<II', header[4:12])
    return header[:4] == b'FUZE' and version == 1 and 0 < lip_size < size - 12


def target_ok(path):
    """Valid FUZ container that is a regular single-link file."""
    try:
        info = os.stat(path)
        if info.st_nlink > 1:
            return False
        with open(path, 'rb') as stream:
            header = stream.read(12)
    except OSError:
        return False
    return fuz_header_ok(header, info.st_size)


def write_atomic(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.part'
    with open(temporary, 'wb') as stream:
        stream.write(blob)
    os.replace(temporary, path)


def nonempty(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def parse_wav(path):
    """Read the RIFF header without trusting chunk sizes.

    Fish writes streaming WAVs whose RIFF/data sizes are placeholders (0xFFFFFF00); xWMAEncode then
    tries to allocate 4 GB and fails. Returns dict(channels, width, rate, offset, size, exact) where
    size is clamped to the real file length and exact says whether the header is already truthful,
    or None if the file is not plain PCM / extensible-PCM WAV.
    """
    try:
        file_size = os.path.getsize(path)
        with open(path, 'rb') as stream:
            head = stream.read(12)
            if len(head) < 12 or head[:4] != b'RIFF' or head[8:12] != b'WAVE':
                return None
            exact = struct.unpack('<I', head[4:8])[0] == file_size - 8
            fmt = None
            while True:
                chunk = stream.read(8)
                if len(chunk) < 8:
                    return None
                chunk_id, size = chunk[:4], struct.unpack('<I', chunk[4:])[0]
                if chunk_id == b'fmt ':
                    body = stream.read(size + (size & 1))
                    tag, channels, rate, _rate, _align, bits = struct.unpack('<HHIIHH', body[:16])
                    if tag == 0xFFFE and len(body) >= 40:
                        tag = struct.unpack('<H', body[24:26])[0]
                    if tag != 1:
                        return None
                    fmt = (channels, bits // 8, rate)
                elif chunk_id == b'data':
                    if fmt is None:
                        return None
                    offset = stream.tell()
                    real = file_size - offset
                    exact = exact and size == real
                    return {'channels': fmt[0], 'width': fmt[1], 'rate': fmt[2], 'offset': offset,
                            'size': min(size, real) // (fmt[0] * fmt[1]) * (fmt[0] * fmt[1]), 'exact': exact}
                else:
                    stream.seek(size + (size & 1), os.SEEK_CUR)
    except (OSError, struct.error):
        return None


def wav_format(info):
    return (info['channels'], info['width'], info['rate']) if info else None


def write_canonical_wav(source, info, destination):
    """Copy the PCM payload under a truthful 44-byte header; returns bytes written."""
    channels, width, rate, size = info['channels'], info['width'], info['rate'], info['size']
    header = (b'RIFF' + struct.pack('<I', 36 + size) + b'WAVEfmt ' +
              struct.pack('<IHHIIHH', 16, 1, channels, rate, rate * channels * width, channels * width, width * 8) +
              b'data' + struct.pack('<I', size))
    with open(source, 'rb') as reader, open(destination, 'wb') as writer:
        writer.write(header)
        reader.seek(info['offset'])
        remaining = size
        while remaining:
            data = reader.read(min(remaining, 1 << 20))
            if not data:
                raise RuntimeError('WAV shorter than its data chunk')
            writer.write(data)
            remaining -= len(data)
    return 44 + size


def quality(path, text):
    """Streaming QA: never holds more than one block of samples; trusts the real file length."""
    info = parse_wav(path)
    if not info or info['width'] != 2:
        raise RuntimeError('QA needs a PCM16 WAV')
    channels, rate = info['channels'], info['rate']
    frames = info['size'] // (2 * channels)
    first = last = -1
    peak = position = 0
    with open(path, 'rb') as stream:
        stream.seek(info['offset'])
        remaining = info['size']
        while remaining:
            data = stream.read(min(remaining, 65536 * 2 * channels))
            if not data:
                break
            remaining -= len(data)
            block = array.array('h')
            block.frombytes(data[:len(data) - len(data) % 2])
            if sys.byteorder != 'little':
                block.byteswap()
            if channels > 1:
                block = block[::channels]
            block_peak = max(max(block), -min(block)) if block else 0
            peak = max(peak, block_peak)
            if block_peak > LOUD:
                if first < 0:
                    first = position + next(i for i, v in enumerate(block) if abs(v) > LOUD)
                last = position + next(i for i in range(len(block) - 1, -1, -1) if abs(block[i]) > LOUD)
            position += len(block)
    duration = frames / rate if rate else 0
    lead, tail = (first / rate, (frames - last) / rate) if first >= 0 else (duration, duration)
    peak /= 32768
    wps = len(text.split()) / duration if duration else 999
    flags = []
    if not 0.8 <= wps <= 5.0:
        flags.append(f'wps {wps:.1f}')
    if lead > 0.8 or tail > 1.2:
        flags.append(f'silence {lead:.1f}/{tail:.1f}')
    if peak < 0.12 or peak > 0.999:
        flags.append(f'peak {peak:.2f}')
    return {'duration': round(duration, 3), 'wps': round(wps, 2), 'lead': round(lead, 3),
            'tail': round(tail, 3), 'peak': round(peak, 4), 'flags': flags}


def sanitized(text):
    """Text without [stage directions]. LipGenerator hangs forever on an unclosed '[', so a line that is
    entirely one unclosed direction keeps its words and loses only the brackets (never falls back to text)."""
    return (' '.join(STAGE_DIRECTIONS.sub(' ', text).split()) or
            ' '.join(text.replace('[', ' ').replace(']', ' ').split()) or text)


def late_start(lip):
    """True for the late-start LIP variant: the mouth stays still for about a second.

    LipGenerator writes it when speech begins on the first sample (Fish clips have no leading
    pause). A normal lip has a negative int32 start offset before the 10 00 marker; the late
    variant has flag 4 set (6 or 7) and no offset. The marker moves for longer key counts.
    """
    for marker in range(14, min(24, len(lip) - 1)):
        if lip[marker:marker + 2] == b'\x10\x00':
            return marker >= 2 and lip[marker - 2] in (6, 7) and lip[marker - 1] == 0
    return True


def lead_wav(source, destination, lead_ms, min_seconds):
    """Copy a canonical mono PCM16 WAV with lead_ms of leading silence, padded with trailing
    silence to at least min_seconds + lead. Returns the total duration in seconds."""
    with wave.open(source) as stream:
        params, data = stream.getparams(), stream.readframes(stream.getnframes())
    frame = params.nchannels * params.sampwidth
    lead = int(round(lead_ms / 1000 * params.framerate))
    total = max(lead + params.nframes, int(round((min_seconds + lead_ms / 1000) * params.framerate)))
    tail = total - lead - params.nframes
    with wave.open(destination, 'wb') as stream:
        stream.setparams(params)
        stream.writeframes(b'\0' * (lead * frame) + data + b'\0' * (tail * frame))
    return total / params.framerate


def build_lipgen_pool(tools, pool_parent, count):
    """Private LipGenerator copies, one per worker, each in its own folder.

    LipGenerator resamples every clip to tmp16khz.wav in its own folder, so parallel runs of one
    executable analyse each other's audio and write lips for the wrong line. The executable also
    holds the machine-wide FonixMemoryMutex, which serializes parallel runs. Each copy gets a
    same-length unique mutex name. The original executable and FonixData.cdf are never modified.
    """
    source_exe = os.path.join(tools, 'LipGenerator.exe')
    source_cdf = os.path.join(tools, 'FonixData.cdf')
    with open(source_exe, 'rb') as stream:
        image = stream.read()
    if image.count(FONIX_MUTEX_NAME) != 1:
        raise RuntimeError('Unknown LipGenerator.exe build: mutex signature not found exactly once')
    if not os.path.isfile(source_cdf):
        raise RuntimeError('FonixData.cdf is missing next to LipGenerator.exe')
    os.makedirs(pool_parent, exist_ok=True)
    pool_dir = tempfile.mkdtemp(prefix=f'lipgen_pool_{os.getpid()}_', dir=pool_parent)
    executables = []
    try:
        for index in range(max(1, count)):
            worker_dir = os.path.join(pool_dir, f'worker_{index:03d}')
            os.makedirs(worker_dir)
            mutex_name = f'Vf{os.getpid():08x}{index:06x}'.encode('ascii')
            assert len(mutex_name) == len(FONIX_MUTEX_NAME)
            worker_exe = os.path.join(worker_dir, 'LipGenerator.exe')
            with open(worker_exe, 'wb') as stream:
                stream.write(image.replace(FONIX_MUTEX_NAME, mutex_name, 1))
            shutil.copyfile(source_cdf, os.path.join(worker_dir, 'FonixData.cdf'))
            executables.append(worker_exe)
    except Exception:
        shutil.rmtree(pool_dir, ignore_errors=True)
        raise
    return executables, pool_dir


# --------------------------------------------------------------------------- scheduling

class Gate:
    """Counting semaphore whose capacity can shrink/grow at runtime (thermal/CPU throttle)."""

    def __init__(self, limit):
        self.limit = self.cap = max(1, limit)
        self.active = 0
        self.cv = threading.Condition()

    def set_cap(self, cap):
        with self.cv:
            self.cap = max(1, min(self.limit, cap))
            self.cv.notify_all()

    def __enter__(self):
        with self.cv:
            while self.active >= self.cap:
                self.cv.wait()
            self.active += 1

    def __exit__(self, *_exc):
        with self.cv:
            self.active -= 1
            self.cv.notify_all()


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}

    def add(self, key, amount=1):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + amount

    def snapshot(self):
        with self.lock:
            return dict(self.values)


def read_temperature():
    """Max CPU temperature in °C from LibreHardwareMonitor/OpenHardwareMonitor WMI or ACPI; None if absent."""
    queries = [
        "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop | "
        "Where-Object { $_.SensorType -eq 'Temperature' -and $_.Identifier -like '*cpu*' } | "
        "Measure-Object Value -Maximum | ForEach-Object Maximum",
        "Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor -ErrorAction Stop | "
        "Where-Object { $_.SensorType -eq 'Temperature' -and $_.Identifier -like '*cpu*' } | "
        "Measure-Object Value -Maximum | ForEach-Object Maximum",
        "Get-CimInstance -Namespace root/WMI -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
        "Measure-Object CurrentTemperature -Maximum | ForEach-Object { $_.Maximum / 10 - 273.15 }",
    ]
    for query in queries:
        try:
            output = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', query],
                                    capture_output=True, text=True, timeout=20,
                                    creationflags=CREATE_NO_WINDOW).stdout.strip()
            value = float(output.replace(',', '.'))
            if 0 < value < 150:
                return value
        except (ValueError, OSError, subprocess.SubprocessError):
            continue
    return None


class Monitor(threading.Thread):
    """Samples system CPU (and optionally temperature) and adjusts the heavy-process gate."""

    def __init__(self, gate, max_cpu, max_temp, on_tick):
        super().__init__(daemon=True)
        self.gate, self.max_cpu, self.max_temp, self.on_tick = gate, max_cpu, max_temp, on_tick
        self.stop = threading.Event()
        self.cpu = 0.0
        self.temp = None
        self.temp_available = max_temp is not None
        self.cpu_sum = self.cpu_samples = 0

    def run(self):
        idle0, total0 = system_times()
        last_temp = 0.0
        while not self.stop.wait(3):
            idle1, total1 = system_times()
            if total1 > total0:
                sample = 100.0 * (1 - (idle1 - idle0) / (total1 - total0))
                self.cpu = sample if not self.cpu_samples else 0.6 * self.cpu + 0.4 * sample
                self.cpu_sum += sample
                self.cpu_samples += 1
            idle0, total0 = idle1, total1
            if self.temp_available and time.monotonic() - last_temp > 30:
                last_temp = time.monotonic()
                self.temp = read_temperature()
                if self.temp is None:
                    self.temp_available = False
                    log('temperature sensor not available; thermal limit disabled')
            hot = self.temp is not None and self.temp >= self.max_temp
            if self.max_cpu and self.cpu > self.max_cpu or hot:
                self.gate.set_cap(self.gate.cap - 1)
            elif ((not self.max_cpu or self.cpu < self.max_cpu - 10) and
                  (self.temp is None or self.temp < self.max_temp - 5)):
                self.gate.set_cap(self.gate.cap + 1)
            self.on_tick()

    def average_cpu(self):
        return self.cpu_sum / self.cpu_samples if self.cpu_samples else None


# --------------------------------------------------------------------------- state

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
    mod TEXT NOT NULL, id TEXT NOT NULL, status TEXT NOT NULL, outcome TEXT, stage TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, targets INTEGER, written INTEGER, error TEXT,
    t_prep REAL, t_lip REAL, t_xwm REAL, t_total REAL, updated REAL, PRIMARY KEY(mod, id));
CREATE TABLE IF NOT EXISTS qa(
    mod TEXT NOT NULL, id TEXT NOT NULL, duration REAL, wps REAL, lead REAL, tail REAL, peak REAL,
    source_format TEXT, transcoded INTEGER, padded_ms INTEGER, lip_text TEXT, lip_attempts INTEGER,
    flags TEXT, PRIMARY KEY(mod, id));
CREATE TABLE IF NOT EXISTS runs(started REAL, finished REAL, argv TEXT, summary TEXT);
"""


class State:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript(SCHEMA)
        self.db.execute('BEGIN')
        self.dirty = 0

    def execute(self, sql, params=()):
        with self.lock:
            self.db.execute(sql, params)
            self.dirty += 1
            if self.dirty >= 500:
                self._commit()

    def query(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params).fetchall()

    def _commit(self):
        self.db.execute('COMMIT')
        self.db.execute('BEGIN')
        self.dirty = 0

    def commit(self):
        with self.lock:
            self._commit()

    def close(self):
        with self.lock:
            self.db.execute('COMMIT')
            self.db.close()

    def recover(self):
        with self.lock:
            count = self.db.execute("UPDATE jobs SET status='pending' WHERE status='running'").rowcount
            self._commit()
            return count

    def set(self, mod, job_id, **fields):
        fields['updated'] = time.time()
        columns = ', '.join(fields)
        updates = ', '.join(f'{name}=excluded.{name}' for name in fields)
        self.execute(f'INSERT INTO jobs(mod, id, {columns}) VALUES (?, ?, {", ".join("?" * len(fields))}) '
                     f'ON CONFLICT(mod, id) DO UPDATE SET {updates}', (mod, job_id, *fields.values()))

    def running(self, mod, job_id, targets):
        self.execute("INSERT INTO jobs(mod, id, status, targets, attempts, updated) VALUES (?, ?, 'running', ?, 1, ?) "
                     "ON CONFLICT(mod, id) DO UPDATE SET status='running', targets=excluded.targets, "
                     "attempts=attempts+1, error=NULL, updated=excluded.updated",
                     (mod, job_id, targets, time.time()))

    def save_qa(self, mod, job_id, qa):
        self.execute('INSERT OR REPLACE INTO qa VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                     (mod, job_id, qa['duration'], qa['wps'], qa['lead'], qa['tail'], qa['peak'],
                      qa.get('source_format'), int(qa.get('transcoded', 0)), qa.get('padded_ms', 0),
                      qa.get('lip_text'), qa.get('lip_attempts'), json.dumps(qa['flags'])))


# --------------------------------------------------------------------------- builder

def log(message):
    print(f'{time.strftime("%H:%M:%S")} {message}', flush=True)


class Builder:
    def __init__(self, args, state):
        self.args, self.state = args, state
        self.tools = args.tools
        self.counters = Counters()
        self.lip_gate, self.xwm_gate = Gate(args.lip_workers), Gate(args.xwm_workers)
        self.heavy = Gate(args.workers or args.lip_workers + args.xwm_workers)
        self.child_flags = CREATE_NO_WINDOW | PRIORITY_CLASSES[args.priority]
        executables, self.lip_pool_dir = build_lipgen_pool(self.tools, args.cache, args.lip_workers)
        self.lip_pool = queue.Queue()
        for executable in executables:
            self.lip_pool.put(executable)

    def close(self):
        if self.lip_pool_dir:
            shutil.rmtree(self.lip_pool_dir, ignore_errors=True)
            self.lip_pool_dir = None

    def tool(self, name, argv, timeout, cwd=None):
        """Return code, or None if the process hung and was killed (subprocess.run kills on timeout)."""
        self.counters.add('launch_' + name)
        try:
            return subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout,
                                  creationflags=self.child_flags).returncode
        except subprocess.TimeoutExpired:
            self.counters.add('timeout_' + name)
            return None

    def temp_bytes(self, path):
        try:
            self.counters.add('temp_bytes', os.path.getsize(path))
        except OSError:
            pass

    # -- stages

    def prepare_wav(self, raw, work, qa):
        wav = os.path.join(work, 'src.wav')
        info = parse_wav(raw)
        source_format = wav_format(info)
        qa['source_format'] = '{}ch/{}bit/{}Hz{}'.format(
            source_format[0], source_format[1] * 8, source_format[2],
            '' if info['exact'] else ' streaming-header') if source_format else 'unknown'
        qa['transcoded'] = source_format != (1, 2, 44100)
        cached = parse_wav(wav)
        if wav_format(cached) == (1, 2, 44100) and cached['exact']:
            return wav
        temporary = os.path.join(work, 'src.part.wav')
        if os.path.exists(temporary):
            os.remove(temporary)
        if not qa['transcoded'] and not info['exact']:
            self.counters.add('wav_header_fixed')
            self.counters.add('temp_bytes', write_canonical_wav(raw, info, temporary))
        elif not qa['transcoded']:
            try:
                if self.args.no_link:
                    raise OSError
                os.link(raw, temporary)  # cache-only hardlink; final FUZ are always fresh files
                self.counters.add('wav_linked')
            except OSError:
                shutil.copyfile(raw, temporary)
                self.counters.add('wav_copied')
                self.temp_bytes(temporary)
        else:
            with self.heavy:
                code = self.tool('ffmpeg', [os.path.join(self.tools, 'ffmpeg.exe'), '-y', '-loglevel', 'error',
                                            '-i', raw, '-ac', '1', '-ar', '44100', '-sample_fmt', 's16', temporary],
                                 self.args.xwm_timeout)
            converted = parse_wav(temporary)
            if code != 0 or wav_format(converted) != (1, 2, 44100) or not converted['exact']:
                raise RuntimeError(f'ffmpeg failed ({code})')
            self.temp_bytes(temporary)
        os.replace(temporary, wav)
        return wav

    def run_lip(self, wav, text):
        """LipGenerator writes <wav stem>.lip next to the WAV. Each run uses a private executable
        copy from the pool, so its tmp16khz.wav and its Fonix mutex are not shared.
        Returns (lip path or None, hung)."""
        produced = os.path.splitext(wav)[0] + '.lip'
        if os.path.exists(produced):
            os.remove(produced)
        # Take the stage-specific slot first. Otherwise threads waiting for a LIP slot
        # consume every shared heavy slot and starve the XWM stage at unequal limits.
        with self.lip_gate, self.heavy:
            executable = self.lip_pool.get()
            try:
                code = self.tool('lip', [executable, wav, text, '-Language:USEnglish', '-Automated'],
                                 self.args.lip_timeout, cwd=os.path.dirname(executable))
            finally:
                self.lip_pool.put(executable)
        return (produced if code == 0 and nonempty(produced) else None), code is None

    def make_lip(self, work, wav, text, qa):
        """Build out.lip and audio.wav (the clip it was made from; xWMAEncode encodes the same clip).

        Every attempt prepends leading silence: a clip whose speech starts on the first sample gets a
        late-start lip. Attempts walk LEAD_MS; the text falls back to the sanitized version after the
        configured original attempts or after a hang. A late lip is kept only as a last resort."""
        lip, audio = os.path.join(work, 'out.lip'), os.path.join(work, 'audio.wav')
        clean = sanitized(text)
        min_seconds = max(MIN_SECONDS, self.args.pad_min_duration or 0)
        texts = [(text, 'original')] * self.args.lip_original_attempts
        texts += [(clean, 'sanitized')] * self.args.lip_sanitized_attempts
        fallback = None
        hung_on_original = False
        attempts = 0
        for index, (lip_text, kind) in enumerate(texts * 2):
            if kind == 'original' and hung_on_original:
                continue  # a hang on the raw text repeats; go straight to the sanitized text
            lead = LEAD_MS[index % len(LEAD_MS)]
            candidate = os.path.join(work, f'lead{lead}.wav')
            lead_wav(wav, candidate, lead, min_seconds)
            attempts += 1
            produced, hung = self.run_lip(candidate, lip_text)
            hung_on_original |= hung and kind == 'original'
            if produced:
                late = late_start(open(produced, 'rb').read())
                if not late or fallback is None:
                    if fallback:
                        os.remove(fallback[0])
                    fallback = (produced + '.keep', candidate, kind, lead, late)
                    os.replace(produced, fallback[0])
                if not late:
                    break
            time.sleep(0.1)
        if fallback is None:
            raise RuntimeError(f'LipGenerator failed after {attempts} attempts')
        kept, source, kind, lead, late = fallback
        os.replace(kept, lip)
        os.replace(source, audio)
        self.temp_bytes(lip)
        qa.update(lip_text=kind, lip_attempts=attempts, padded_ms=lead)
        if late:
            qa['flags'].append('late lip')
        xwm = os.path.join(work, 'out.xwm')
        if os.path.exists(xwm):
            os.remove(xwm)  # audio must match the LIP timeline
        return audio

    def make_xwm(self, work, wav):
        xwm, temporary = os.path.join(work, 'out.xwm'), os.path.join(work, 'tmp.xwm')
        for _attempt in range(self.args.xwm_attempts):
            if os.path.exists(temporary):
                os.remove(temporary)
            with self.xwm_gate, self.heavy:
                code = self.tool('xwm', [os.path.join(self.tools, 'xWMAEncode.exe'), '-b', XWM_BITRATE,
                                         wav, temporary], self.args.xwm_timeout)
            if code == 0 and nonempty(temporary):
                os.replace(temporary, xwm)
                self.temp_bytes(xwm)
                return
            time.sleep(0.25)
        raise RuntimeError('xWMAEncode failed')

    # -- job

    def process(self, mod, job):
        started = time.perf_counter()
        job_id = job['id']
        paths = [os.path.join(mod['destination'], *target.split('/')) for target in job['targets']]
        valid = [target_ok(path) for path in paths]
        missing = [path for path, ok in zip(paths, valid) if not ok]
        if not missing:
            self.state.set(mod['name'], job_id, status='done', outcome='skipped', targets=len(paths), written=0)
            return 'skipped'
        self.state.running(mod['name'], job_id, len(paths))
        stage = 'restore'
        try:
            if any(valid):
                with open(paths[valid.index(True)], 'rb') as stream:
                    blob = stream.read()
                if not fuz_header_ok(blob[:12], len(blob)):
                    raise RuntimeError('source FUZ changed while restoring')
                for path in missing:
                    write_atomic(path, blob)
                self.state.set(mod['name'], job_id, status='done', outcome='restored', stage=None,
                               written=len(missing), t_total=time.perf_counter() - started)
                return 'restored'

            stage = 'prep'
            raw = os.path.join(mod['raw'], job_id + '.wav')
            if not os.path.exists(raw):
                raise RuntimeError('missing raw WAV')
            work = os.path.join(self.args.cache, mod['name'], job_id)
            os.makedirs(work, exist_ok=True)
            qa = {'padded_ms': 0}
            wav = self.prepare_wav(raw, work, qa)
            qa.update(quality(wav, job['text']))
            t_prep = time.perf_counter() - started

            stage = 'lip'
            tick = time.perf_counter()
            lip, xwm = os.path.join(work, 'out.lip'), os.path.join(work, 'out.xwm')
            audio = os.path.join(work, 'audio.wav')
            if nonempty(lip) and nonempty(audio):
                qa.update(lip_text='cached', lip_attempts=0)
            else:
                audio = self.make_lip(work, wav, job['text'], qa)
            t_lip = time.perf_counter() - tick

            stage = 'xwm'
            tick = time.perf_counter()
            if not nonempty(xwm):
                self.make_xwm(work, audio)
            t_xwm = time.perf_counter() - tick

            stage = 'write'
            with open(lip, 'rb') as stream:
                lip_bytes = stream.read()
            with open(xwm, 'rb') as stream:
                xwm_bytes = stream.read()
            if not lip_bytes or not xwm_bytes:
                raise RuntimeError('empty LIP/XWM')
            blob = b'FUZE' + struct.pack('<II', 1, len(lip_bytes)) + lip_bytes + xwm_bytes
            for path in missing:
                write_atomic(path, blob)
            self.state.save_qa(mod['name'], job_id, qa)
            self.state.set(mod['name'], job_id, status='done', outcome='built', stage=None, written=len(missing),
                           t_prep=t_prep, t_lip=t_lip, t_xwm=t_xwm, t_total=time.perf_counter() - started)
            if not self.args.keep_cache:
                shutil.rmtree(work, ignore_errors=True)
            return 'built'
        except Exception as error:  # noqa: BLE001 - every failure is recorded and the queue continues
            self.state.set(mod['name'], job_id, status='failed', outcome=None, stage=stage,
                           error=f'{type(error).__name__}: {error}', t_total=time.perf_counter() - started)
            return 'failed'


# --------------------------------------------------------------------------- commands

def load_config(path):
    with open(path, encoding='utf-8') as stream:
        config = json.load(stream)
    base = os.path.dirname(os.path.abspath(path))
    resolve = lambda value: os.path.normpath(os.path.join(base, value))  # noqa: E731
    for mod in config['mods']:
        mod['jobs'], mod['raw'], mod['destination'] = (resolve(mod[key]) for key in ('jobs', 'raw', 'destination'))
    config.setdefault('tools', DEFAULT_TOOLS)
    return config


def load_jobs(mod):
    with open(mod['jobs'], encoding='utf-8') as stream:
        return [{'id': job['id'], 'text': job['text'], 'targets': job['targets']} for job in json.load(stream)]


def round_robin(mods, jobs_by_mod):
    iterators = [(mod, iter(jobs_by_mod[mod['name']])) for mod in mods]
    while iterators:
        alive = []
        for mod, iterator in iterators:
            job = next(iterator, None)
            if job is not None:
                alive.append((mod, iterator))
                yield mod, job
        iterators = alive


def command_run(args, config):
    args.tools = args.tools or config['tools']
    args.state = args.state or os.path.join(HERE, 'state.sqlite')
    args.cache = os.path.abspath(args.cache or os.path.join(HERE, 'cache'))  # tools run with cwd inside it
    configure_process(args.priority, int(args.affinity_mask, 0) if args.affinity_mask else 0)
    mods = [mod for mod in config['mods'] if not args.mod or mod['name'] in args.mod]
    if args.out_root:
        for mod in mods:
            mod['destination'] = os.path.join(os.path.abspath(args.out_root), mod['name'])

    state = State(args.state)
    recovered = state.recover()
    skip = set()
    if args.skip_failed:
        skip = {tuple(row) for row in state.query("SELECT mod, id FROM jobs WHERE status='failed'")}
    jobs_by_mod = {mod['name']: load_jobs(mod) for mod in mods}
    total = sum(len(jobs) for jobs in jobs_by_mod.values())
    if args.limit:
        total = min(total, args.limit)
    log(f'mods={",".join(m["name"] for m in mods)} jobs={total} recovered_running={recovered} '
        f'lip={args.lip_workers} xwm={args.xwm_workers} heavy={args.workers or args.lip_workers + args.xwm_workers} '
        f'io={args.io_workers} priority={args.priority} affinity={args.affinity_mask or "all"}')

    builder = Builder(args, state)
    outcomes = Counters()
    started = time.perf_counter()
    started_wall = time.time()
    last_report = [0.0]

    def report(force=False):
        now = time.perf_counter()
        if not force and now - last_report[0] < args.report_every:
            return
        last_report[0] = now
        done = outcomes.snapshot()
        finished = sum(done.values())
        rate = done.get('built', 0) / max(now - started, 1e-9) * 60
        temp = f' temp={monitor.temp:.0f}C' if monitor.temp is not None else ''
        log(f'{finished}/{total} built={done.get("built", 0)} restored={done.get("restored", 0)} '
            f'skipped={done.get("skipped", 0)} failed={done.get("failed", 0)} | {rate:.0f} built/min | '
            f'cpu={monitor.cpu:.0f}% heavy_cap={builder.heavy.cap}{temp}')

    def tick():
        state.commit()
        report()

    monitor = Monitor(builder.heavy, args.max_cpu_percent, args.max_temp, tick)
    monitor.start()
    threads = args.lip_workers + args.xwm_workers + args.io_workers
    queue = (item for item in round_robin(mods, jobs_by_mod) if (item[0]['name'], item[1]['id']) not in skip)
    submitted = 0
    try:
        with ThreadPoolExecutor(max_workers=threads) as executor:
            pending = set()
            for mod, job in queue:
                if args.limit and submitted >= args.limit:
                    break
                pending.add(executor.submit(builder.process, mod, job))
                submitted += 1
                if len(pending) >= threads * 4:
                    finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in finished:
                        outcomes.add(future.result())
            for future in pending:
                outcomes.add(future.result())
    finally:
        monitor.stop.set()
        monitor.join(timeout=10)
        report(force=True)
        wall = time.perf_counter() - started
        done = outcomes.snapshot()
        summary = {'jobs': submitted, **done, 'wall_s': round(wall, 1),
                   'built_per_min': round(done.get('built', 0) / wall * 60, 1) if wall else None,
                   'jobs_per_min': round(sum(done.values()) / wall * 60, 1) if wall else None,
                   'avg_system_cpu': round(monitor.average_cpu(), 1) if monitor.average_cpu() is not None else None,
                   'peak_rss_mb': round(peak_rss() / 2**20, 1), **builder.counters.snapshot()}
        state.execute('INSERT INTO runs VALUES (?, ?, ?, ?)',
                      (started_wall, time.time(), json.dumps(sys.argv), json.dumps(summary)))
        state.close()
        builder.close()
        if args.summary:
            with open(args.summary, 'w', encoding='utf-8') as stream:
                json.dump(summary, stream, indent=1)
        print(json.dumps(summary, indent=1))
    return 1 if done.get('failed') else 0


def command_verify(args, config):
    """Structural + manifest check: missing / unexpected / invalid / zero-byte / hardlinked / leftover .part."""
    failed = False
    result = {}
    for mod in config['mods']:
        if args.mod and mod['name'] not in args.mod:
            continue
        root = os.path.join(os.path.abspath(args.out_root), mod['name']) if args.out_root else mod['destination']
        expected = {target.replace('/', os.sep).lower() for job in load_jobs(mod) for target in job['targets']}
        actual, invalid, linked, parts = set(), [], [], []
        for directory, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(directory, name)
                relative = os.path.relpath(path, root).lower()
                if name.lower().endswith('.part'):
                    parts.append(relative)
                    continue
                if not name.lower().endswith('.fuz'):
                    continue
                actual.add(relative)
                info = os.stat(path)
                if info.st_nlink > 1:
                    linked.append(relative)
                with open(path, 'rb') as stream:
                    if not fuz_header_ok(stream.read(12), info.st_size):
                        invalid.append(relative)
        report = {'expected': len(expected), 'actual': len(actual), 'missing': sorted(expected - actual),
                  'unexpected': sorted(actual - expected), 'invalid': invalid, 'hardlinked': linked, 'part': parts}
        failed |= any(report[key] for key in ('missing', 'unexpected', 'invalid', 'hardlinked', 'part'))
        result[mod['name']] = report
        print(mod['name'], json.dumps({k: len(v) if isinstance(v, list) else v for k, v in report.items()}))
    if args.report:
        with open(args.report, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, indent=1)
    return 1 if failed else 0


def command_status(args, _config):
    state = sqlite3.connect(args.state or os.path.join(HERE, 'state.sqlite'))
    for row in state.execute('SELECT mod, status, outcome, COUNT(*) FROM jobs GROUP BY 1, 2, 3 ORDER BY 1, 2, 3'):
        print(*row)
    for row in state.execute("SELECT mod, id, stage, error FROM jobs WHERE status='failed' LIMIT 20"):
        print('FAILED', *row)
    row = state.execute('SELECT summary FROM runs ORDER BY started DESC LIMIT 1').fetchone()
    if row:
        print('last run:', row[0])
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('run', 'verify', 'status'))
    parser.add_argument('--config', default=os.path.join(HERE, 'mods.json'))
    parser.add_argument('--mod', action='append', help='only these mod names (repeatable)')
    parser.add_argument('--state', help='SQLite state file (default: state.sqlite next to this script)')
    parser.add_argument('--cache', help='work/cache dir for .wav/.lip/.xwm (default: ./cache)')
    parser.add_argument('--tools', help='LipGenerator folder (LipGenerator.exe, xWMAEncode.exe, ffmpeg.exe)')
    parser.add_argument('--out-root', help='write to <out-root>/<mod name> instead of the configured destination')
    parser.add_argument('--limit', type=int, help='process at most N jobs (after round-robin interleave)')
    parser.add_argument('--lip-workers', type=int, default=4)
    parser.add_argument('--xwm-workers', type=int, default=4)
    parser.add_argument('--io-workers', type=int, default=2, help='extra job threads for checks/restores/writes')
    parser.add_argument('--workers', type=int, help='max heavy processes at once (default lip+xwm)')
    parser.add_argument('--priority', choices=tuple(PRIORITY_CLASSES), default='below_normal')
    parser.add_argument('--affinity-mask', help='CPU mask, e.g. 0xFFF0 (inherited by child processes)')
    parser.add_argument('--max-cpu-percent', type=float, default=75, help='shrink heavy slots above this; 0 = off')
    parser.add_argument('--max-temp', type=float, help='shrink heavy slots at/above this CPU °C if a sensor exists')
    parser.add_argument('--lip-original-attempts', type=int, default=2)
    parser.add_argument('--lip-sanitized-attempts', type=int, default=3)
    parser.add_argument('--xwm-attempts', type=int, default=3)
    parser.add_argument('--lip-timeout', type=float, default=60,
                        help='kill a LipGenerator run after N s (normal ~2 s; hung ones spin a core forever)')
    parser.add_argument('--xwm-timeout', type=float, default=120)
    parser.add_argument('--pad-min-duration', type=float, default=0,
                        help='after LipGenerator really fails on a WAV shorter than this (s), pad with silence; 0 = off')
    parser.add_argument('--no-link', action='store_true', help='copy WAVs into the cache instead of hardlinking')
    parser.add_argument('--keep-cache', action='store_true')
    parser.add_argument('--skip-failed', action='store_true', help='do not retry jobs marked failed')
    parser.add_argument('--summary', help='write run summary JSON here')
    parser.add_argument('--report', help='verify: write full report JSON here')
    parser.add_argument('--report-every', type=float, default=30)
    args = parser.parse_args()
    config = load_config(args.config) if args.command != 'status' else None
    handler = {'run': command_run, 'verify': command_verify, 'status': command_status}[args.command]
    return handler(args, config)


if __name__ == '__main__':
    sys.exit(main())
