import json
import tempfile
import threading
import unittest
from pathlib import Path

from voiceforge.engine import FishClient
from voiceforge.project import MAX_FISH_WORKERS, Project, create_project


class FishWorkerTests(unittest.TestCase):
    def test_a_project_can_run_twelve_fish_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = [dict(id='a', vt='maleguard', text='Hello.', targets=['Sound/Voice/X.esp/maleguard/a_00000800_1.fuz'])]
            root = create_project(Path(temp) / 'p', 'p', jobs)
            config = json.loads((root / 'project.json').read_text(encoding='utf-8'))
            config['fish_workers'] = 12
            (root / 'project.json').write_text(json.dumps(config), encoding='utf-8')
            project = Project(root)  # 0.4.1 refused anything above 3
            client = FishClient(project.config, threading.Event(), lambda value: None)
            acquired = [client.gate.acquire(blocking=False) for _ in range(12)]
            self.assertEqual(acquired.count(True), 12)
            self.assertFalse(client.gate.acquire(blocking=False))

    def test_more_than_the_maximum_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            jobs = [dict(id='a', vt='maleguard', text='Hello.', targets=['Sound/Voice/X.esp/maleguard/a_00000800_1.fuz'])]
            root = create_project(Path(temp) / 'p', 'p', jobs)
            config = json.loads((root / 'project.json').read_text(encoding='utf-8'))
            config['fish_workers'] = MAX_FISH_WORKERS + 1
            (root / 'project.json').write_text(json.dumps(config), encoding='utf-8')
            with self.assertRaises(ValueError):
                Project(root)


if __name__ == '__main__':
    unittest.main()
