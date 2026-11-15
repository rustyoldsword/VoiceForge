import csv
import io
import json
import math
import os
import struct
import tempfile
import threading
import unittest
import urllib.error
import wave
from pathlib import Path
from unittest.mock import patch

import msgpack
from voiceforge.project import (Project, create_project, jobs_from_csv, relative, contained,
                                validate_jobs, read_json, read_refs, write_json, MODEL)
from voiceforge.engine import FishClient, FatalAPI, ProjectLock, regenerate, valid_wav, verify, legacy_snapshot
from voiceforge import fuz_backend as fuz


def audio():
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(i / 20))) for i in range(4410)))
    return out.getvalue()


def job():
    return dict(id='voice_123', vt='maleguard', text='Welcome, traveler.',
                targets=['Sound/Voice/Test.esp/maleguard/test_00000800_1.fuz'])


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def project(self):
        return Project(create_project(self.root / 'project', 'Test', [job()]))

    def test_path_escape_and_duplicate_targets(self):
        for value in ('../bad.fuz', 'C:\\bad.fuz', '/bad.fuz', 'a/../x', 'a//x', 'a:b/x'):
            with self.assertRaises(ValueError):
                relative(value)
        inside = contained(self.root, 'Sound/Voice/x.fuz')
        self.assertTrue(inside.is_relative_to(self.root.resolve()))
        for value in ('../bad.fuz', 'a/../../x', 'C:/bad.fuz'):
            with self.assertRaises(ValueError):
                contained(self.root, value)
        duplicate = job()
        duplicate['id'] = 'another'
        with self.assertRaises(ValueError):
            validate_jobs([job(), duplicate])

    def test_csv_exact_mapping_deduplication_and_skip(self):
        lines, paths = self.root / 'lines.csv', self.root / 'paths.csv'
        with lines.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=['info_formid', 'response_number', 'needs_voice', 'voice_text (stage directions removed)'])
            writer.writeheader()
            for ident, needs, text in [('800:Test.esp', 'yes', 'Welcome, traveler.'), ('801:Test.esp', 'yes', 'Welcome, traveler.'), ('802:Test.esp', 'no', '[silence]')]:
                writer.writerow(dict(info_formid=ident, response_number=1, needs_voice=needs, **{'voice_text (stage directions removed)': text}))
        with paths.open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=['info_formid', 'response_number', 'voice_type', 'game_path (relative to Data)'])
            writer.writeheader()
            for ident in ('800', '801', '802'):
                writer.writerow(dict(info_formid=ident + ':Test.esp', response_number='01', voice_type='MaleGuard',
                    **{'game_path (relative to Data)': f'Sound\\Voice\\Test.esp\\MaleGuard\\q_00000{ident}_1.fuz'}))
        jobs, skipped = jobs_from_csv(lines, paths)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len(jobs[0]['targets']), 2)
        self.assertEqual(len(skipped), 1)

    def test_manifest_changes_are_not_silently_cached(self):
        p = self.project()
        data = read_json(p.root / 'jobs.json')
        data[0]['text'] = 'Changed text'
        write_json(p.root / 'jobs.json', data)
        with self.assertRaises(ValueError):
            Project(p.root)

    def test_streaming_wav_header_repair(self):
        data = bytearray(audio())
        struct.pack_into('<I', data, 4, 0xFFFFFF00)
        struct.pack_into('<I', data, 40, 0xFFFFFF00)
        path, fixed = self.root / 'stream.wav', self.root / 'fixed.wav'
        path.write_bytes(data)
        info = fuz.parse_wav(str(path))
        self.assertFalse(info['exact'])
        self.assertTrue(valid_wav(path))
        fuz.write_canonical_wav(str(path), info, str(fixed))
        self.assertTrue(fuz.parse_wav(str(fixed))['exact'])
        self.assertEqual(fixed.read_bytes()[44:], data[44:])

    def test_fuz_structural_check_and_orphan_reporting(self):
        p = self.project()
        target = contained(p.root / 'output', job()['targets'][0])
        target.parent.mkdir(parents=True)
        target.write_bytes(b'FUZE' + struct.pack('<II', 1, 3) + b'lip' + b'RIFFaudio')
        self.assertTrue(verify(p)['clean'])
        (target.parent / 'extra.fuz').write_bytes(b'broken')
        report = verify(p)
        self.assertEqual(report['counts']['unexpected'], 1)
        self.assertEqual(report['counts']['invalid'], 1)

    def test_regeneration_preserves_original_files_and_ignores_imported_wav(self):
        p = self.project()
        wav = p.root / 'wav_raw' / 'voice_123.wav'
        wav.write_bytes(audio())
        source = self.root / 'old'
        source.mkdir()
        (source / wav.name).write_bytes(audio())
        p.config['raw_source'] = str(source)
        p.save()
        regenerate(p, ['voice_123'])
        self.assertFalse(p.wav(job()).exists())
        self.assertEqual(len(list((p.root / 'backups').rglob('*.wav'))), 1)
        self.assertTrue((source / wav.name).exists())

    def test_project_lock_excludes_second_runner(self):
        p = self.project()
        with ProjectLock(p.root):
            with self.assertRaises(RuntimeError):
                with ProjectLock(p.root):
                    pass

    def client(self, emit=lambda e: None):
        stop = threading.Event()
        client = FishClient(dict(fish_workers=3, references='', secret=''), stop, emit)
        client.key = 'TEST-ONLY-NONSECRET'
        client.references = lambda vt: [dict(audio=b'ref', text='Reference text')]
        return client

    def test_fish_401_and_402_halt_without_paid_fallback(self):
        for code in (401, 402):
            client = self.client()
            with patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('url', code, '', {}, None)) as request:
                with self.assertRaises(FatalAPI):
                    client.generate(job(), self.root / 'out.wav')
                self.assertTrue(client.stop.is_set())
                self.assertEqual(request.call_count, 1)
                self.assertEqual(request.call_args.args[0].get_header('Model'), MODEL)

    def test_fish_payload_inline_refs_and_atomic_wav(self):
        client = self.client()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return audio()
        with patch('urllib.request.urlopen', return_value=Response()) as request:
            out = self.root / 'out.wav'
            client.generate(job(), out)
            body = msgpack.unpackb(request.call_args.args[0].data)
            self.assertEqual(body['text'], job()['text'])
            self.assertEqual(body['references'][0]['audio'], b'ref')
            self.assertNotIn('reference_id', body)
            self.assertTrue(valid_wav(out))
            self.assertFalse(Path(str(out) + '.part').exists())

    def test_bad_audio_response_is_not_accepted(self):
        client = self.client()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'RIFFbroken'
        with patch('urllib.request.urlopen', return_value=Response()):
            with self.assertRaises(RuntimeError):
                client.generate(job(), self.root / 'out.wav')
        self.assertFalse((self.root / 'out.wav').exists())

    def test_library_reference_id_does_not_send_inline_references(self):
        client = self.client()
        client.config['voice_ids'] = {'maleguard': 'a' * 32}
        client.references = lambda vt: self.fail('Inline references should not be loaded')
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return audio()
        with patch('urllib.request.urlopen', return_value=Response()) as request:
            client.generate(job(), self.root / 'out.wav')
            body = msgpack.unpackb(request.call_args.args[0].data)
            self.assertEqual(body['reference_id'], 'a' * 32)
            self.assertNotIn('references', body)

    def test_rate_limit_is_retried_without_changing_model(self):
        client = self.client()
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return audio()
        with patch('urllib.request.urlopen', side_effect=[urllib.error.HTTPError('url',429,'',{},None),Response()]) as request:
            with patch.object(client.stop, 'wait', return_value=False):
                client.generate(job(), self.root / 'out.wav')
            self.assertEqual(request.call_count, 2)
            self.assertEqual({c.args[0].get_header('Model') for c in request.call_args_list}, {MODEL})

    def test_lip_unclosed_direction_cleanup(self):
        self.assertEqual(fuz.sanitized('[Whisper something'), 'Whisper something')
        self.assertEqual(fuz.sanitized('Hello [smiles] traveler'), 'Hello traveler')

    def test_late_start_lip_detection(self):
        # Header bytes as LipGenerator writes them: normal lips carry a negative start offset
        # before the 10 00 marker, the late variant has flag 6/7 and no offset.
        normal = bytes.fromhex('0000000000000000000000000000') + b'\xff\xff\x10\x00' + b'\0' * 16
        late = bytes.fromhex('0000000000000000000000000000') + b'\x07\x00\x10\x00' + b'\0' * 16
        self.assertFalse(fuz.late_start(normal))
        self.assertTrue(fuz.late_start(late))
        self.assertTrue(fuz.late_start(b'\0' * 30))  # no marker: treated as unusable

    def test_lead_silence_and_minimum_length(self):
        source, padded = self.root / 'a.wav', self.root / 'b.wav'
        source.write_bytes(audio())  # 0.1 s of tone
        seconds = fuz.lead_wav(str(source), str(padded), 150, 0.4)
        self.assertAlmostEqual(seconds, 0.55, places=3)
        with wave.open(str(padded)) as stream:
            frames = stream.readframes(stream.getnframes())
        lead = int(0.15 * 44100) * 2
        self.assertEqual(frames[:lead], b'\0' * lead)
        self.assertNotEqual(frames[lead:lead + 200], b'\0' * 200)
        long_source = self.root / 'long.wav'
        with wave.open(str(long_source), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(44100)
            stream.writeframes(b'\1\0' * 44100)
        self.assertAlmostEqual(fuz.lead_wav(str(long_source), str(padded), 100, 0.4), 1.1, places=3)

    def test_lipgen_pool_gives_each_worker_a_private_copy(self):
        tools = self.root / 'tools'
        tools.mkdir()
        (tools / 'LipGenerator.exe').write_bytes(b'MZ..FonixMemoryMutex..end')
        (tools / 'FonixData.cdf').write_bytes(b'cdf')
        executables, pool = fuz.build_lipgen_pool(str(tools), str(self.root / 'cache'), 3)
        images = [Path(e).read_bytes() for e in executables]
        self.assertEqual(len({Path(e).parent for e in executables}), 3)
        self.assertEqual(len(set(images)), 3)
        self.assertTrue(all(b'FonixMemoryMutex' not in i and len(i) == 25 for i in images))
        self.assertEqual((tools / 'LipGenerator.exe').read_bytes(), b'MZ..FonixMemoryMutex..end')
        (tools / 'LipGenerator.exe').write_bytes(b'MZ..unknown build')
        with self.assertRaises(RuntimeError):
            fuz.build_lipgen_pool(str(tools), str(self.root / 'cache'), 1)
        self.assertTrue(Path(pool).is_dir())

    def test_reference_voice_types_ignore_case(self):
        path = self.root / 'ref_candidates.json'
        write_json(path, {'MaleNord': [{'file': 'a.wav', 'text': 'x'}]})
        self.assertIn('malenord', read_refs(path))
        write_json(path, {'MaleNord': [], 'malenord': []})
        with self.assertRaises(ValueError):
            read_refs(path)


if __name__ == '__main__':
    unittest.main()
