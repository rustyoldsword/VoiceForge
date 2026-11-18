import io
import math
import os
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voiceforge import engine
from voiceforge.engine import FatalAPI, FishClient, valid_wav


def audio():
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(i / 20))) for i in range(4410)))
    return out.getvalue()


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return audio()


class KeyFileTests(unittest.TestCase):
    """0.4.3 started twenty requests at the same moment; all but the first saw an empty key while the first was
    still reading the key file, and the run stopped with "FISH_API_KEY is missing" although the file was fine."""

    def test_every_parallel_request_gets_the_key_from_the_file(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = Path(temp) / '.env.local'
            secret.write_text('FISH_API_KEY = TEST-ONLY-NONSECRET\n', encoding='utf-8')
            client = FishClient(dict(fish_workers=12, references='', secret=str(secret)), threading.Event(), lambda e: None)
            client.references = lambda vt: [dict(audio=b'ref', text='Reference text')]
            real = engine.dotenv_values

            def slow_file(path):  # a cold disk: reading the file takes a moment
                time.sleep(0.2)
                return real(path)

            start, errors = threading.Barrier(12), []

            def request(n):
                start.wait()
                try:
                    client.generate(dict(id=f'j{n}', vt='maleguard', text='Hello.'), Path(temp) / f'{n}.wav')
                except FatalAPI as error:
                    errors.append(str(error))

            env = {k: v for k, v in os.environ.items() if k != 'FISH_API_KEY'}
            with patch.dict(os.environ, env, clear=True), patch.object(engine, 'dotenv_values', slow_file), \
                    patch('urllib.request.urlopen', return_value=Response()):
                threads = [threading.Thread(target=request, args=(n,)) for n in range(12)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
            self.assertEqual(errors, [])
            self.assertTrue(all(valid_wav(Path(temp) / f'{n}.wav') for n in range(12)))

    def test_a_file_without_the_key_still_stops_the_run(self):
        with tempfile.TemporaryDirectory() as temp:
            secret = Path(temp) / '.env.local'
            secret.write_text('OTHER_KEY=x\n', encoding='utf-8')
            client = FishClient(dict(fish_workers=1, references='', secret=str(secret)), threading.Event(), lambda e: None)
            env = {k: v for k, v in os.environ.items() if k != 'FISH_API_KEY'}
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(FatalAPI):
                    client.generate(dict(id='j', vt='maleguard', text='Hello.'), Path(temp) / 'j.wav')


if __name__ == '__main__':
    unittest.main()
