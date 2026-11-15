import hashlib
from contextlib import closing
import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_video.discovery import Video
from local_video.flatten import flatten_completed
from local_video.runner import Runner, destination_for
from local_video.security import atomic_json
from local_video.store import Store


class FlattenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'downloads'
        self.target = self.output / '集中视频'
        self.store = Store(self.root / 'jobs.sqlite3')
        self.addCleanup(self.store.close)

    def video(self, quality='best', body=b'video-fixture'):
        video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123')
        job = self.store.enqueue(video, str(self.output), quality)
        row = next(r for r in self.store.list() if r['id'] == job)
        path = destination_for(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        atomic_json(path.with_suffix('.mp4.verified.json'), {'source': video.url, 'bytes': len(body), 'sha256': digest, 'verification': 'length+sha256'})
        self.store.set_state(job, 'completed', bytes=len(body), sha256=digest, verification='length+sha256')
        return video, job, path

    def run_flatten(self):
        return flatten_completed(self.store, self.output, self.target, threading.Event(), lambda e: None)

    def test_move_preserves_record_and_subsequent_rediscovery_needs_no_network(self):
        video, job, old = self.video()
        result = self.run_flatten()
        self.assertEqual(result['moved'], 1)
        self.assertFalse(old.exists())
        row = self.store.list()[0]
        new = destination_for(row)
        self.assertEqual(new.parent, self.target)
        self.assertEqual(new.read_bytes(), b'video-fixture')
        self.assertEqual(row['state'], 'completed')
        self.assertEqual(row['sha256'], hashlib.sha256(b'video-fixture').hexdigest())
        self.assertFalse(new.with_suffix('.mp4.verified.json').exists())
        self.assertTrue(old.with_suffix('.mp4.verified.json').is_file())
        self.store.enqueue(video, str(self.output), 'best')
        runner = Runner(self.store, {'output': str(self.output)}, {}, lambda e: None)
        with patch('local_video.transfer.Downloader._probe', side_effect=AssertionError('must not access network')), patch('local_video.runner.probe_video', return_value='not-installed'):
            runner.run()
        self.assertEqual(self.store.list()[0]['state'], 'completed')
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.run_flatten()['moved'], 0)

    def test_conflict_preserves_existing_file_and_quality_variants(self):
        self.video()
        self.video('720p', b'smaller-fixture')
        self.target.mkdir(parents=True)
        foreign = self.target / 'alice_123_1_best.mp4'
        foreign.write_bytes(b'user-existing-file')
        self.run_flatten()
        self.assertEqual(foreign.read_bytes(), b'user-existing-file')
        paths = [destination_for(r) for r in self.store.list()]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(p.exists() and p != foreign for p in paths))

    def test_corrupt_source_is_not_moved(self):
        _, job, old = self.video()
        old.write_bytes(b'corrupted')
        result = self.run_flatten()
        self.assertEqual(result['failed'], 1)
        self.assertTrue(old.exists())
        self.assertFalse(self.store.list()[0].get('local_path'))

    def test_copy_failure_leaves_source_and_original_record(self):
        _, job, old = self.video()
        with patch('local_video.flatten.copy_verified', side_effect=OSError('disk full')):
            result = self.run_flatten()
        self.assertEqual(result['failed'], 1)
        self.assertTrue(old.exists())
        self.assertEqual(destination_for(self.store.list()[0]), old)

    def test_cleanup_failure_after_commit_recovers_without_data_loss(self):
        _, job, old = self.video()
        with patch('local_video.flatten.cleanup_source', side_effect=OSError('file busy')):
            self.run_flatten()
        row = self.store.list()[0]
        self.assertNotEqual(destination_for(row), old)
        self.assertTrue(old.exists())
        self.assertTrue(row['relocation_source'])
        self.run_flatten()
        self.assertFalse(old.exists())
        self.assertFalse(self.store.list()[0]['relocation_source'])

    def test_interruption_before_database_commit_reuses_verified_copy(self):
        _, job, old = self.video()
        with patch.object(self.store, 'relocate', side_effect=OSError('database busy')):
            self.assertEqual(self.run_flatten()['failed'], 1)
        self.assertTrue(old.exists())
        self.assertEqual(len(list(self.target.glob('*.mp4'))), 1)
        self.run_flatten()
        self.assertFalse(old.exists())
        self.assertEqual(len(list(self.target.glob('*.mp4'))), 1)

    def test_manifest_publish_failure_keeps_unique_original(self):
        _, job, old = self.video()
        with patch('local_video.flatten._write_manifest', side_effect=OSError('metadata write failed')):
            self.assertEqual(self.run_flatten()['failed'], 1)
        self.assertTrue(old.exists())
        self.assertEqual(destination_for(self.store.list()[0]), old)
        self.assertEqual(len(list(self.target.glob('*.mp4'))), 0)
        self.run_flatten()
        self.assertEqual(len(list(self.target.glob('*.mp4'))), 1)

    def test_unfinished_and_other_output_jobs_are_untouched(self):
        _, job, old = self.video()
        self.store.set_state(job, 'paused')
        self.assertEqual(self.run_flatten()['moved'], 0)
        self.assertTrue(old.exists())

    def test_upgrade_old_queue_preserves_completed_records(self):
        _, job, old = self.video()
        legacy_path = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy_path)) as legacy:
            with legacy:
                self.store.db.backup(legacy)
                legacy.execute('ALTER TABLE jobs DROP COLUMN local_path')
                legacy.execute('ALTER TABLE jobs DROP COLUMN relocation_source')
        upgraded = Store(legacy_path)
        try:
            row = upgraded.list()[0]
            self.assertEqual(row['id'], job)
            self.assertEqual(row['state'], 'completed')
            self.assertEqual(destination_for(row), old)
            self.assertEqual(row['local_path'], '')
            self.assertEqual(row['relocation_source'], '')
        finally:
            upgraded.close()

    def test_other_archive_root_keeps_its_own_video_when_one_is_moved_again(self):
        video, first, old = self.video()
        second_output = self.root / 'other-downloads'
        second = self.store.enqueue(video, str(second_output), 'best')
        second_row = next(r for r in self.store.list() if r['id'] == second)
        second_file = destination_for(second_row)
        second_file.parent.mkdir(parents=True)
        second_file.write_bytes(old.read_bytes())
        second_file.with_suffix('.mp4.verified.json').write_bytes(old.with_suffix('.mp4.verified.json').read_bytes())
        self.store.set_state(second, 'completed', bytes=13, sha256=hashlib.sha256(b'video-fixture').hexdigest(), verification='length+sha256')
        self.run_flatten()
        flatten_completed(self.store, second_output, self.target, threading.Event(), lambda e: None)
        records = {r['id']: r for r in self.store.list()}
        self.assertNotEqual(destination_for(records[first]), destination_for(records[second]))
        flatten_completed(self.store, self.output, self.root / 'new-target', threading.Event(), lambda e: None)
        self.assertTrue(all(destination_for(row).is_file() for row in self.store.list()))


if __name__ == '__main__':
    unittest.main()
