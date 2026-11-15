"""Project from an xEdit export: voice planning, creature substitution, child voices."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from voiceforge import xedit_import as builder

LINES = ['id', 'info_formid', 'response_number', 'needs_voice',
         'voice_text (stage directions removed)', 'subtitle_text (as in game unchanged)', 'export_notes']
PATHS = ['game_path (relative to Data)', 'info_formid', 'response_number', 'voice_type',
         'resolution', 'context_info']


def write_csv(path, header, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


def wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'RIFF')


class Fixture:
    """Export: guard line (male human + dog by sex projection), talking dog, barking dog, empty."""

    def __init__(self, root):
        self.root = root
        self.export = root / 'VoiceForge-test'
        self.export.mkdir()
        lines = [
            ('l1', '000001:Mod.esp', 1, 'yes', 'Halt, criminal!', 'Halt, criminal!', ''),
            ('l2', '000002:Mod.esp', 1, 'yes', 'The curse is broken.', 'The curse is broken.', ''),
            ('l3', '000003:Mod.esp', 1, 'yes', '(bark)', '(bark)', ''),
            ('l4', '000004:Mod.esp', 1, 'no', '', '*growls*', ''),
        ]
        paths = []

        def target(vt, n):
            return f'Sound/Voice/Mod.esp/{vt}/q_t_0000000{n}_1.fuz'

        for vt in ('maleguard', 'crdoghusky', 'crtrollvoice'):
            paths.append((target(vt, 1), '000001:Mod.esp', 1, vt, 'condition_voice_candidates', ''))
        paths.append((target('crdoghusky', 2), '000002:Mod.esp', 1, 'crdoghusky', 'speaker_and_conditions', ''))
        paths.append((target('crdoghusky', 3), '000003:Mod.esp', 1, 'crdoghusky', 'speaker_and_conditions', ''))
        paths.append((target('crdoghusky', 4), '000004:Mod.esp', 1, 'crdoghusky', 'speaker_and_conditions', ''))
        write_csv(self.export / 'lines.csv', LINES, lines)
        write_csv(self.export / 'paths.csv', PATHS, paths)
        self.library = root / 'library'
        refs = {
            'maleguard': [dict(file='g.wav', text='Guard reference.')],
            'malecommander': [dict(file='c.wav', text='Commander reference.')],
        }
        (self.library).mkdir()
        (self.library / 'ref_candidates.json').write_text(json.dumps(refs), encoding='utf-8')
        wav(self.library / 'refs' / 'maleguard' / 'g.wav')
        wav(self.library / 'refs' / 'malecommander' / 'c.wav')

    def build(self, substitutions=None, **kwargs):
        project = self.root / 'project'
        return builder.prepare(self.export, project, self.library / 'ref_candidates.json', 'test',
                               substitutions=substitutions or {}, **kwargs), project


class VoicePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_only_sole_speakers_with_speech_need_a_voice(self):
        plan = builder.plan_voices(self.fx.export / 'lines.csv', self.fx.export / 'paths.csv',
                                   {'maleguard', 'malecommander'})
        self.assertEqual(sorted(plan.speaking), ['crdoghusky'])
        self.assertEqual([line.text for line in plan.speaking['crdoghusky']], ['The curse is broken.'])
        self.assertEqual(plan.noise, {'crdoghusky': 1, 'crtrollvoice': 1})
        self.assertEqual(plan.silent, {'crdoghusky': 2})

    def test_project_builds_without_references_for_noise_voices(self):
        result, project = self.fx.build()
        jobs = json.loads((project / 'jobs.json').read_text(encoding='utf-8'))
        self.assertEqual([j['vt'] for j in jobs], ['maleguard'])
        self.assertEqual(result['unvoiced_speaking_voice_types'], {'crdoghusky': 1})

    def test_substituted_voice_keeps_its_own_folder_and_borrows_references(self):
        result, project = self.fx.build({'crdoghusky': 'malecommander'})
        jobs = json.loads((project / 'jobs.json').read_text(encoding='utf-8'))
        dog = [j for j in jobs if j['vt'] == 'crdoghusky']
        self.assertEqual(len(dog), 1)
        self.assertEqual(dog[0]['text'], 'The curse is broken.')
        self.assertEqual(dog[0]['targets'], ['Sound/Voice/Mod.esp/crdoghusky/q_t_00000002_1.fuz'])
        config = json.loads((project / 'project.json').read_text(encoding='utf-8'))
        refs = json.loads(Path(config['references']).read_text(encoding='utf-8'))
        self.assertEqual(refs['crdoghusky'][0]['source_voice_type'], 'malecommander')
        audio = Path(config['references']).parent / 'refs' / 'malecommander' / 'c.wav'
        self.assertTrue(audio.is_file())
        self.assertEqual(result['substitutions'], {'crdoghusky': 'malecommander'})

    def test_substitution_must_name_a_library_voice(self):
        with self.assertRaises(ValueError):
            self.fx.build({'crdoghusky': 'notavoice'})

    def test_rebuild_is_repeatable(self):
        first, _ = self.fx.build({'crdoghusky': 'malecommander'})
        second = builder.prepare(self.fx.export, Path(self.temp.name) / 'project2',
                                 self.fx.library / 'ref_candidates.json', 'test',
                                 substitutions={'crdoghusky': 'malecommander'})
        self.assertEqual(first['jobs'], second['jobs'])
        self.assertEqual(first['targets'], second['targets'])

    def test_child_block_still_applies(self):
        with (self.fx.export / 'paths.csv').open(encoding='utf-8') as stream:
            rows = list(csv.reader(stream))
        rows.append(['Sound/Voice/Mod.esp/femalechild/q_t_00000001_1.fuz', '000001:Mod.esp', '1',
                     'femalechild', 'condition_voice_candidates', ''])
        write_csv(self.fx.export / 'paths.csv', rows[0], rows[1:])
        refs = json.loads((self.fx.library / 'ref_candidates.json').read_text(encoding='utf-8'))
        refs['femalechild'] = [dict(file='f.wav', text='Child reference.')]
        (self.fx.library / 'ref_candidates.json').write_text(json.dumps(refs), encoding='utf-8')
        wav(self.fx.library / 'refs' / 'femalechild' / 'f.wav')
        with self.assertRaises(ValueError):
            self.fx.build(child_voices='block')

    def test_child_voices_can_be_excluded(self):
        with (self.fx.export / 'paths.csv').open(encoding='utf-8') as stream:
            rows = list(csv.reader(stream))
        rows.append(['Sound/Voice/Mod.esp/femalechild/q_t_00000001_1.fuz', '000001:Mod.esp', '1',
                     'femalechild', 'condition_voice_candidates', ''])
        write_csv(self.fx.export / 'paths.csv', rows[0], rows[1:])
        refs = json.loads((self.fx.library / 'ref_candidates.json').read_text(encoding='utf-8'))
        refs['femalechild'] = [dict(file='f.wav', text='Child reference.')]
        (self.fx.library / 'ref_candidates.json').write_text(json.dumps(refs), encoding='utf-8')
        wav(self.fx.library / 'refs' / 'femalechild' / 'f.wav')
        result, project = self.fx.build(child_voices='exclude')
        jobs = json.loads((project / 'jobs.json').read_text(encoding='utf-8'))
        self.assertEqual(sorted({j['vt'] for j in jobs}), ['maleguard'])
        self.assertEqual(result['child_voice_types'], [])

    def test_export_folder_detection(self):
        self.assertTrue(builder.is_export_folder(self.fx.export))
        self.assertFalse(builder.is_export_folder(self.fx.library))


if __name__ == '__main__':
    unittest.main()
