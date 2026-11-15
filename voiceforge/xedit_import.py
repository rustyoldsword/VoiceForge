"""A VoiceForge project straight from an xEdit export folder (lines.csv + paths.csv).

VoiceTypes without a reference (creatures, daedric princes, moan voices...) are planned per line
before the project is built:

* the line also has a VoiceType with references -> the reference-less voice is only a candidate of a
  broad condition (e.g. "any male") and is dropped;
* the line has no speech once stage directions are removed -> dropped (barks, growls);
* otherwise the creature really speaks -> it is voiced only when it is mapped to a library voice.
  The FUZ stays in the creature's own folder; only the reference is borrowed.
"""
from __future__ import annotations

import csv
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .project import create_project, jobs_from_csv, read_refs, relative, write_json

SUBSTITUTIONS_FILE = 'voice-substitutions.json'
PLAN_REPORT = 'voice-plan.txt'
CHILD_VOICES = {'femalechild', 'malechild'}
STAGE_DIRECTIONS = re.compile(r'\([^)]*\)|\[[^\]]*\]|\*[^*]*\*|<[^>]*>')
LETTERS = re.compile(r'[^\W\d_]{2}')


def has_speech(text: str) -> bool:
    return bool(LETTERS.search(STAGE_DIRECTIONS.sub(' ', text)))


def is_export_folder(path) -> bool:
    path = Path(path)
    return path.is_dir() and (path / 'lines.csv').is_file() and (path / 'paths.csv').is_file()


@dataclass
class SpokenLine:
    info: str
    response: int
    text: str


@dataclass
class VoicePlan:
    speaking: dict[str, list[SpokenLine]] = field(default_factory=dict)
    speaking_targets: dict[str, set[str]] = field(default_factory=dict)
    noise: dict[str, int] = field(default_factory=dict)
    silent: dict[str, int] = field(default_factory=dict)


def _rows(path: Path) -> list[dict]:
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def plan_voices(lines_csv, paths_csv, available: set[str]) -> VoicePlan:
    """Classify every VoiceType without references, line by line."""
    lines = {(row['info_formid'].casefold(), int(row['response_number'])): row for row in _rows(lines_csv)}
    voices: dict[tuple, set[str]] = {}
    targets: dict[tuple, dict[str, str]] = {}
    for row in _rows(paths_csv):
        key = (row['info_formid'].casefold(), int(row['response_number']))
        vt = row['voice_type'].lower()
        voices.setdefault(key, set()).add(vt)
        targets.setdefault(key, {})[vt] = relative(row['game_path (relative to Data)'])
    plan = VoicePlan()
    noise, silent = Counter(), Counter()
    for key in sorted(voices):
        missing = voices[key] - available
        if not missing:
            continue
        line = lines.get(key, {})
        text = line.get('voice_text (stage directions removed)', '')
        if voices[key] & available:
            noise.update(missing)
        elif line.get('needs_voice', 'yes').lower() not in ('yes', 'true', '1') or not has_speech(text):
            silent.update(missing)
        else:
            for vt in sorted(missing):
                plan.speaking.setdefault(vt, []).append(SpokenLine(line['info_formid'], key[1], text))
                plan.speaking_targets.setdefault(vt, set()).add(targets[key][vt].casefold())
    plan.noise, plan.silent = dict(noise), dict(silent)
    return plan


def apply_plan(jobs: list[dict], plan: VoicePlan, available: set[str], substitutions: dict[str, str]) -> list[dict]:
    """Keep library voices; keep a reference-less voice only for its own spoken lines, if mapped."""
    kept = []
    for job in jobs:
        vt = job['vt']
        if vt in available:
            kept.append(job)
            continue
        if vt not in substitutions:
            continue
        own = plan.speaking_targets.get(vt, set())
        targets = [t for t in job['targets'] if t.casefold() in own]
        if targets:
            kept.append(dict(job, targets=targets))
    return kept


def validate_reference_library(path: Path, needed: set[str]) -> tuple[int, list[str]]:
    if not Path(path).is_file():
        raise FileNotFoundError(f'Reference manifest not found: {path}')
    refs = read_refs(path)
    missing = sorted(needed - set(refs))
    refs_root = Path(path).parent / 'refs'
    checked = 0
    for vt in sorted(needed & set(refs)):
        clips = refs[vt]
        if not clips:
            raise ValueError(f'{vt}: empty reference list')
        for clip in clips:
            if not isinstance(clip, dict) or not str(clip.get('text', '')).strip():
                raise ValueError(f'{vt}: reference transcript missing')
            audio = refs_root / relative(clip.get('source_voice_type', vt)) / relative(clip.get('file', ''))
            if not audio.is_file():
                raise FileNotFoundError(f'{vt}: reference audio missing: {audio}')
            checked += 1
    return checked, missing


def unused_project_path(parent: Path, stem: str) -> Path:
    base = Path(parent) / f'{stem}-Project'
    if not base.exists():
        return base
    return Path(parent) / f'{stem}-Project-{time.strftime("%Y%m%d-%H%M%S")}'


def write_plan_report(path: Path, plan: VoicePlan, substitutions: dict[str, str]) -> None:
    out = ['Voice plan for the xEdit export', '']
    out.append('Creatures and other voices WITHOUT references that really speak:')
    for vt, lines in sorted(plan.speaking.items()):
        chosen = substitutions.get(vt)
        state = f'voiced with {chosen}' if chosen else 'NOT VOICED (no substitute chosen)'
        out.append(f'  {vt}: {len(lines)} lines - {state}')
        for line in lines:
            out.append(f'      {line.info} #{line.response}: {line.text}')
    out += ['', 'Dropped: only a candidate next to voices with references (broad conditions):']
    out += [f'  {vt}: {n} lines' for vt, n in sorted(plan.noise.items())]
    out += ['', 'Dropped: no speech (barks, growls, empty):']
    out += [f'  {vt}: {n} lines' for vt, n in sorted(plan.silent.items())]
    Path(path).write_text('\n'.join(out) + '\n', encoding='utf-8')


def load_plan(export_dir, references) -> tuple[VoicePlan, set[str]]:
    """The voice plan of an export against a reference library: (plan, voices with references)."""
    export_dir = Path(export_dir)
    if not is_export_folder(export_dir):
        raise FileNotFoundError(f'Expected lines.csv and paths.csv in the xEdit export folder: {export_dir}')
    available = set(read_refs(references))
    return plan_voices(export_dir / 'lines.csv', export_dir / 'paths.csv', available), available


def saved_substitutions(export_dir) -> dict[str, str]:
    path = Path(export_dir) / SUBSTITUTIONS_FILE
    if not path.is_file():
        return {}
    import json
    value = json.loads(path.read_text(encoding='utf-8'))
    return {str(k).lower(): str(v).lower() for k, v in value.items()} if isinstance(value, dict) else {}


def prepare(export_dir, project_dir, references, name, *, substitutions=None, child_voices='keep',
            plan: VoicePlan | None = None) -> dict:
    """Build the project. child_voices: 'keep', 'exclude' (drop FemaleChild/MaleChild) or 'block' (refuse)."""
    export_dir, references = Path(export_dir).resolve(), Path(references).resolve()
    if plan is None:
        plan, available = load_plan(export_dir, references)
    else:
        available = set(read_refs(references))
    substitutions = {k.lower(): v.lower() for k, v in (substitutions or {}).items()}
    substitutions = {k: v for k, v in substitutions.items() if k in plan.speaking and v}
    for vt, donor in substitutions.items():
        if donor not in available:
            raise ValueError(f'{vt}: substitute voice {donor} is not in the reference library')
    write_json(export_dir / SUBSTITUTIONS_FILE, substitutions)
    write_plan_report(export_dir / PLAN_REPORT, plan, substitutions)

    all_jobs, skipped = jobs_from_csv(export_dir / 'lines.csv', export_dir / 'paths.csv')
    jobs = apply_plan(all_jobs, plan, available, substitutions)
    children = sorted({job['vt'] for job in jobs} & CHILD_VOICES)
    if children and child_voices == 'block':
        raise ValueError('Child VoiceTypes are blocked for this project: ' + ', '.join(children))
    if child_voices == 'exclude':
        jobs = [job for job in jobs if job['vt'] not in CHILD_VOICES]
    if not jobs:
        raise ValueError(f'No voiceable lines left after the voice plan; see {PLAN_REPORT}')
    # Keep the portable input manifest beside the original CSV export as well.
    write_json(export_dir / 'jobs.json', jobs)

    needed = {job['vt'] for job in jobs}
    donors = {substitutions.get(vt, vt) for vt in needed}
    checked_clips, missing_voices = validate_reference_library(references, donors)
    if missing_voices:
        raise ValueError('VoiceTypes without references: ' + ', '.join(missing_voices))
    # Talking creatures only name the library voice they borrow: no copies, no link back to the export folder.
    project = create_project(project_dir, name, jobs, references=str(references), skipped=skipped,
                             borrowed=substitutions)
    result = {
        'project': str(project),
        'jobs_json': str(export_dir / 'jobs.json'),
        'references': str(references),
        'jobs': len(jobs),
        'targets': sum(len(job['targets']) for job in jobs),
        'voice_types': len(needed),
        'reference_clips_checked': checked_clips,
        'child_voice_types': sorted(needed & CHILD_VOICES),
        'skipped_targets': len(skipped),
        'substitutions': substitutions,
        'unvoiced_speaking_voice_types': {vt: len(lines) for vt, lines in sorted(plan.speaking.items())
                                          if vt not in substitutions},
        'dropped_candidate_voice_types': len(set(plan.noise) - set(plan.speaking)),
        'voice_plan': str(export_dir / PLAN_REPORT),
    }
    write_json(export_dir / 'voiceforge-project-result.json', result)
    return result
