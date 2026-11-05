import sys
import os
from pathlib import Path
if '--smoke-test' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from voiceforge.ui import run

if __name__ == '__main__':
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    raise SystemExit(run(home))
