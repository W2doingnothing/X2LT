from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from local_video.desktop_service import DesktopService
from local_video.libraries import LibraryManager
from local_video.transfer import Paused


class StartupCapacityTests(unittest.TestCase):
    def test_bootstrap_returns_while_capacity_walk_is_blocked(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service = DesktopService(root/'data', root/'home')
            entered = threading.Event()
            resume = threading.Event()
            try:
                walk = service._libraries._measure

                def slow_measure(*args):
                    entered.set()
                    if not resume.wait(5):
                        self.fail('test did not release the synthetic capacity scan')
                    return walk(*args)

                with patch.object(service._libraries,'_measure',side_effect=slow_measure), patch.object(
                        service._store.catalog,'ensure_ready',side_effect=AssertionError('bootstrap queried catalog')):
                    response = service.call('bootstrap')
                    self.assertTrue(response['ok'],response)
                    self.assertTrue(entered.wait(2))
                    data = response['data']
                    self.assertEqual(data['tasks'],[])
                    self.assertEqual(data['total'],0)
                    self.assertIs(data['settings']['infinite_scroll'],False)
                    first = data['libraries'][0]
                    self.assertTrue(first['capacity_pending'])
                    self.assertFalse(first['online'])
                    self.assertNotIn('used_bytes',first)
                    self.assertTrue(service.call('poll')['ok'])
                    self.assertTrue(service._libraries.snapshot(cached_only=True)[0]['capacity_pending'])
                    resume.set()
                    deadline = time.monotonic()+3
                    while time.monotonic()<deadline:
                        row = service._libraries.snapshot(cached_only=True)[0]
                        if not row['capacity_pending']:
                            break
                        time.sleep(.01)
                    self.assertTrue(row['online'])
                    self.assertEqual(row['used_bytes'],0)
            finally:
                resume.set()
                service._close()

    def test_scan_from_old_path_cannot_publish_after_library_path_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original, moved = root/'original', root/'moved'
            original.mkdir()
            moved.mkdir()
            (original/'fixture.bin').write_bytes(b'a'*30)
            (moved/'fixture.bin').write_bytes(b'b'*63)
            manager = LibraryManager(root/'libraries.json')
            library = manager.add('测试库',original)
            measured, release = threading.Event(), threading.Event()
            measure = manager._measure

            def measure_old(item):
                result = measure(item)
                measured.set()
                release.wait(5)
                return result

            try:
                with patch.object(manager,'_measure',side_effect=measure_old):
                    manager.refresh_background(lambda: None)
                    worker = manager.capacity_worker
                    self.assertTrue(measured.wait(2))
                    manager.update(library['id'],path=moved)
                    release.set()
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())
                row = manager.snapshot(cached_only=True)[0]
                self.assertTrue(row['capacity_pending'])
                self.assertNotIn('used_bytes',row)
                self.assertEqual(manager.snapshot(refresh=True)[0]['used_bytes'],63)
            finally:
                release.set()
                manager.close()

    def test_cached_estimate_never_overrides_real_write_quota(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            library = root/'library'
            library.mkdir()
            config = root/'libraries.json'
            manager = LibraryManager(config)
            created = manager.add('测试库',library,100)
            (library/'fixture.txt').write_bytes(b'x'*12)
            self.assertEqual(manager.snapshot(refresh=True)[0]['used_bytes'],12)

            (library/'fixture.txt').write_bytes(b'x'*80)
            reopened = LibraryManager(config)
            row = reopened.snapshot(cached_only=True)[0]
            self.assertEqual(row['used_bytes'],12)
            self.assertTrue(row['capacity_stale'])
            self.assertTrue(row['capacity_pending'])
            self.assertFalse(row['online'])
            with reopened.lease(library/'write.bin') as lease:
                with self.assertRaises(Paused):
                    lease.reserve(40)

            other = root/'other'
            other.mkdir()
            reopened.update(created['id'],path=other)
            row = reopened.snapshot(cached_only=True)[0]
            self.assertTrue(row['capacity_pending'])
            self.assertNotIn('used_bytes',row)

    def test_summary_snapshot_does_not_query_task_cards(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service = DesktopService(root/'data', root/'home')
            try:
                with patch.object(service._store.catalog,'page',side_effect=AssertionError('summary queried cards')):
                    result = service.call('snapshot',{'summary_only':True})
                self.assertTrue(result['ok'],result)
                self.assertEqual(result['data']['tasks'],[])
                self.assertEqual(len(result['data']['libraries']),1)
            finally:
                service._close()
