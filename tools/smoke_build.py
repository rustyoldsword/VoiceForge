# Real-tool smoke test of the FUZ builder: builds given WAVs with the real LipGenerator/xWMAEncode
# into a temporary folder and reports lip state (late or not) and the lead silence used.
# Run: .venv\Scripts\python.exe tools\smoke_build.py <tools dir> <wav> "<text>" [<wav> "<text>" ...]
import argparse
import os
import struct
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voiceforge import fuz_backend as fuz  # noqa: E402


def main():
    tools, pairs = sys.argv[1], sys.argv[2:]
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, 'raw')
        os.makedirs(raw)
        jobs = []
        for index in range(0, len(pairs), 2):
            ident = f'job{index // 2}'
            with open(pairs[index], 'rb') as src, open(os.path.join(raw, ident + '.wav'), 'wb') as dst:
                dst.write(src.read())
            jobs.append(dict(id=ident, text=pairs[index + 1], targets=[f'Sound/Voice/T.esp/V/{ident}.fuz']))
        args = argparse.Namespace(tools=tools, lip_workers=4, xwm_workers=4, workers=8, priority='below_normal',
                                  cache=os.path.join(tmp, 'cache'), no_link=True, keep_cache=False, lip_timeout=60,
                                  xwm_timeout=120, lip_original_attempts=2, lip_sanitized_attempts=3, xwm_attempts=3,
                                  pad_min_duration=0.5)
        state = fuz.State(os.path.join(tmp, 's.sqlite'))
        builder = fuz.Builder(args, state)
        mod = dict(name='project', raw=raw, destination=os.path.join(tmp, 'out'))
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda j: builder.process(mod, j), jobs))
        for job, result in zip(jobs, results):
            path = os.path.join(mod['destination'], *job['targets'][0].split('/'))
            row = state.query('SELECT padded_ms, lip_attempts, flags FROM qa WHERE id=?', (job['id'],))
            err = state.query('SELECT error FROM jobs WHERE id=?', (job['id'],))
            late = None
            if os.path.exists(path):
                blob = open(path, 'rb').read()
                lip_size = struct.unpack('<I', blob[8:12])[0]
                late = fuz.late_start(blob[12:12 + lip_size])
            print(job['id'], result, 'late' if late else 'ok' if late is False else '-', row, err[0][0] if err else '')
        state.close()
        builder.close()
        print('pool removed:', not os.path.exists(builder.lip_pool_dir or ''))


if __name__ == '__main__':
    main()
