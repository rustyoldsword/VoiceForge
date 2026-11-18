import struct
import tempfile
import unittest
from pathlib import Path

from voiceforge.engine import finished_on_disk
from voiceforge.project import Project, contained, create_project


def job(ident, frame):
    return dict(id=ident, vt='maleguard', text=f'Line {ident}.',
                targets=[f'Sound/Voice/Test.esp/maleguard/t_{frame:08X}_1.fuz'])


class RestartTests(unittest.TestCase):
    """A restart after a long night must not reopen every finished WAV and FUZ: 63,000 finished lines kept a
    restarted 0.4.2 silent for about ten minutes before the first new Fish request."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        jobs = [job('done', 0x800), job('lost', 0x801), job('pending', 0x802)]
        self.p = Project(create_project(Path(self.temp.name) / 'p', 'p', jobs))
        for ident in ('done', 'lost'):
            target = contained(self.p.root / 'output', self.p.by_id[ident]['targets'][0])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'FUZE' + struct.pack('<II', 1, 3) + b'lip' + b'RIFFaudio')
            self.p.set(ident, status='done', stage='complete')
        # A finished line is judged by its FUZ, not by its WAV: this WAV is not even a WAV.
        (self.p.root / 'wav_raw' / 'done.wav').write_bytes(b'not a wav')
        contained(self.p.root / 'output', self.p.by_id['lost']['targets'][0]).unlink()

    def tearDown(self):
        self.temp.cleanup()

    def test_finished_lines_with_their_files_are_skipped(self):
        self.assertEqual(finished_on_disk(self.p, self.p.jobs), {'done'})

    def test_a_finished_line_whose_file_was_deleted_is_done_again(self):
        self.assertNotIn('lost', finished_on_disk(self.p, self.p.jobs))

    def test_a_pending_line_is_never_skipped(self):
        self.assertNotIn('pending', finished_on_disk(self.p, self.p.jobs))


if __name__ == '__main__':
    unittest.main()
