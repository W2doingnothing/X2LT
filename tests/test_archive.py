import hashlib
import json
import threading
import unittest
from unittest.mock import patch
import test_flatten
from local_video.runner import Runner, destination_for
from local_video.flatten import flatten_completed


class ArchiveTests(test_flatten.FlattenTests):
    # Existing safety tests also run against the new receipt-based organizer.
    def test_receipt_stays_at_original_path_and_target_has_only_video(self):
        _, _, old = self.video()
        self.run_flatten()
        self.assertTrue(old.with_suffix('.mp4.verified.json').exists())
        self.assertEqual(list(self.target.glob('*.json')), [])
        receipt = json.loads(old.with_suffix('.mp4.verified.json').read_text(encoding='utf8'))
        self.assertEqual(receipt['tweet_id'], '123')
        self.assertTrue(receipt['downloaded'])

    def test_external_move_does_not_redownload_and_recheck_retains_history(self):
        video, _, old = self.video()
        self.run_flatten()
        destination_for(self.store.list()[0]).rename(self.root / 'manually-moved.mp4')
        self.store.enqueue(video, self.output, 'best')
        with patch('local_video.runner.Downloader.download', side_effect=AssertionError('no network')) as download:
            Runner(self.store, {'output': str(self.output)}, {}, lambda e: None).run()
            Runner(self.store, {'output': str(self.output)}, {}, lambda e: None).run(recheck=True)
        download.assert_not_called()
        self.assertEqual(self.store.list()[0]['state'], 'completed')
        self.assertIn('找不到', self.store.list()[0]['error'])

    def test_receipt_recovers_dedup_in_a_new_queue_without_video(self):
        from local_video.store import Store
        video, _, old = self.video()
        self.run_flatten()
        destination_for(self.store.list()[0]).unlink()
        fresh = Store(self.root / 'fresh.sqlite3')
        try:
            fresh.enqueue(video, self.output, 'best')
            with patch('local_video.runner.Downloader.download', side_effect=AssertionError('no network')) as download:
                Runner(fresh, {'output': str(self.output)}, {}, lambda e: None).run()
            download.assert_not_called()
            self.assertEqual(fresh.list()[0]['state'], 'completed')
        finally:
            fresh.close()

    def test_rollback_finds_external_file_and_does_not_overwrite_conflict(self):
        from local_video.flatten import rollback_completed
        _, _, old = self.video()
        self.run_flatten()
        moved = destination_for(self.store.list()[0])
        search = self.root / 'external'
        search.mkdir()
        moved.rename(search / 'renamed.mp4')
        (search / 'wrong.mp4').write_bytes(b'x' * 13)
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b'unrelated')
        first = rollback_completed(self.store, self.output, threading.Event(), lambda e: None, search=search)
        self.assertEqual(first['failed'], 1)
        self.assertEqual(old.read_bytes(), b'unrelated')
        old.unlink()
        result = rollback_completed(self.store, self.output, threading.Event(), lambda e: None, search=search)
        self.assertEqual(result['moved'], 1)
        self.assertEqual(old.read_bytes(), b'video-fixture')
        self.assertFalse((search / 'renamed.mp4').exists())
        self.assertTrue((search / 'wrong.mp4').exists())
        self.assertEqual(destination_for(self.store.list()[0]), old)
        self.assertTrue(old.with_suffix('.mp4.verified.json').exists())

    def test_legacy_target_json_returns_to_original_directory(self):
        _, job, old = self.video()
        legacy = self.target / 'legacy.mp4'
        self.target.mkdir(parents=True)
        old.rename(legacy)
        old.with_suffix('.mp4.verified.json').rename(legacy.with_suffix('.mp4.verified.json'))
        self.store.relocate(job, legacy, old)
        self.store.finish_relocation(job)
        self.run_flatten()
        self.assertTrue(old.with_suffix('.mp4.verified.json').exists())
        self.assertFalse(legacy.with_suffix('.mp4.verified.json').exists())

    def test_force_download_selected_job_bypasses_history(self):
        _, job, old = self.video()
        result = {'bytes': 13, 'sha256': hashlib.sha256(b'video-fixture').hexdigest(), 'verification': 'length+sha256'}
        with patch('local_video.runner.Downloader.download', return_value=result) as download, patch('local_video.runner.probe_video'):
            Runner(self.store, {'output': str(self.output)}, {}, lambda e: None).run(force_id=job)
        download.assert_called_once()
        self.assertTrue(download.call_args.kwargs['force'])

    def test_progress_contains_counts_and_file_bytes(self):
        self.video()
        events = []
        flatten_completed(self.store, self.output, self.target, threading.Event(), events.append)
        self.assertTrue(any(e['type'] == 'operation_progress' and e['done'] == e['total'] == 1 for e in events))
        self.assertTrue(any(e['type'] == 'file_progress' and e['done'] == e['total'] == 13 for e in events))

    def test_invalid_json_does_not_suppress_a_real_download(self):
        from local_video.archive import original_path
        from local_video.store import Store
        video, _, old = self.video()
        old.with_suffix('.mp4.verified.json').write_text('{broken', encoding='utf8')
        fresh = Store(self.root / 'fresh.sqlite3')
        try:
            fresh.enqueue(video, self.output, 'best')
            with patch('local_video.runner.Downloader.download', side_effect=OSError('test network')) as download:
                Runner(fresh, {'output': str(self.output)}, {}, lambda e: None).run()
            download.assert_called_once()
            self.assertEqual(fresh.list()[0]['state'], 'failed')
        finally:
            fresh.close()

    def test_verify_never_downloads_pending_or_corrupt_completed_files(self):
        from local_video.discovery import Video
        _, _, old = self.video()
        self.store.enqueue(Video('456', '1', 'bob', 'https://video.twimg.com/b.mp4', 'https://x.com/bob/status/456'), self.output, 'best')
        old.write_bytes(b'corrupt')
        with patch('local_video.runner.Downloader.download') as download:
            Runner(self.store, {'output': str(self.output)}, {}, lambda e: None).run(recheck=True)
        download.assert_not_called()
        rows = self.store.list()
        self.assertEqual(rows[0]['state'], 'pending')
        self.assertEqual(rows[1]['state'], 'completed')
        self.assertIn('校验不符', rows[1]['error'])

    def test_unknown_legacy_json_is_not_deleted(self):
        _, _, old = self.video()
        self.run_flatten()
        new = destination_for(self.store.list()[0])
        foreign = new.with_suffix('.mp4.verified.json')
        foreign.write_text('{"belongs":"someone else"}', encoding='utf8')
        self.run_flatten()
        self.assertTrue(foreign.exists())
        self.assertTrue(old.with_suffix('.mp4.verified.json').exists())

    def test_rollback_recovers_copy_after_database_failure(self):
        from local_video.flatten import rollback_completed
        _, _, old = self.video()
        self.run_flatten()
        moved = destination_for(self.store.list()[0])
        with patch.object(self.store, 'relocate', side_effect=OSError('database busy')):
            result = rollback_completed(self.store, self.output, threading.Event(), lambda e: None)
        self.assertEqual(result['failed'], 1)
        self.assertTrue(old.exists())
        self.assertTrue(moved.exists())
        result = rollback_completed(self.store, self.output, threading.Event(), lambda e: None)
        self.assertEqual(result['moved'], 1)
        self.assertTrue(old.exists())
        self.assertFalse(moved.exists())

    def test_precancel_does_not_move_video_or_delete_json(self):
        from local_video.flatten import rollback_completed
        _, _, old = self.video()
        self.run_flatten()
        moved = destination_for(self.store.list()[0])
        cancel = threading.Event()
        cancel.set()
        rollback_completed(self.store, self.output, cancel, lambda e: None)
        self.assertTrue(moved.exists())
        self.assertFalse(old.exists())
        self.assertTrue(old.with_suffix('.mp4.verified.json').exists())

    def test_force_intent_survives_pause_restart_and_normal_resume(self):
        from local_video.store import Store
        from local_video.transfer import Paused
        _, job, old = self.video()
        self.run_flatten()
        with patch('local_video.runner.Downloader.download', side_effect=Paused('paused')):
            Runner(self.store, {'output': str(self.output)}, {}, lambda e: None).run(force_id=job)
        self.assertEqual(self.store.list()[0]['state'], 'paused')
        reopened = Store(self.root / 'jobs.sqlite3')
        try:
            reopened.recover()
            result = {'bytes': 13, 'sha256': hashlib.sha256(b'video-fixture').hexdigest(), 'verification': 'length+sha256'}
            with patch('local_video.runner.Downloader.download', return_value=result) as download, patch('local_video.runner.probe_video'):
                Runner(reopened, {'output': str(self.output)}, {}, lambda e: None).run()
            download.assert_called_once()
            self.assertTrue(download.call_args.kwargs['force'])
            self.assertFalse(reopened.list()[0]['force_download'])
        finally:
            reopened.close()

    def test_search_reconciles_video_already_manually_restored_to_original(self):
        from local_video.flatten import rollback_completed
        _, _, old = self.video()
        self.run_flatten()
        destination_for(self.store.list()[0]).rename(old)
        result = rollback_completed(self.store, self.output, threading.Event(), lambda e: None, search=self.output)
        self.assertEqual(result['moved'], 1)
        self.assertEqual(destination_for(self.store.list()[0]), old)
        self.assertEqual(self.run_flatten()['moved'], 1)

    def test_search_recovers_when_pending_cleanup_destination_was_moved(self):
        from local_video.flatten import rollback_completed
        _, _, old = self.video()
        with patch('local_video.flatten.cleanup_source', side_effect=OSError('busy')):
            self.run_flatten()
        search = self.root / 'external'
        search.mkdir()
        destination_for(self.store.list()[0]).rename(search / 'moved.mp4')
        result = rollback_completed(self.store, self.output, threading.Event(), lambda e: None, search=search)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(destination_for(self.store.list()[0]), old)
        self.assertTrue(old.exists())
        self.assertFalse(self.store.list()[0]['relocation_source'])

    def test_verify_preserves_pending_reservation_for_rollback_retry(self):
        from local_video.flatten import rollback_completed
        self.video()
        self.run_flatten()
        with patch.object(self.store, 'relocate', side_effect=OSError('database busy')):
            rollback_completed(self.store, self.output, threading.Event(), lambda e: None)
        with patch('local_video.runner.probe_video'):
            Runner(self.store, {'output': str(self.output)}, {}, lambda e: None).run(recheck=True)
        result = rollback_completed(self.store, self.output, threading.Event(), lambda e: None)
        self.assertEqual(result['moved'], 1)
        self.assertEqual(result['failed'], 0)

    def test_malformed_receipt_is_invalid_instead_of_crashing_the_batch(self):
        from local_video.archive import valid_receipt
        _, _, old = self.video()
        self.run_flatten()
        receipt = old.with_suffix('.mp4.verified.json')
        data = json.loads(receipt.read_text(encoding='utf8'))
        data['verification'] = []
        receipt.write_text(json.dumps(data), encoding='utf8')
        self.assertIsNone(valid_receipt(self.store.list()[0]))

    def test_malformed_receipt_location_is_invalid(self):
        from local_video.archive import valid_receipt
        _, _, old = self.video()
        self.run_flatten()
        receipt = old.with_suffix('.mp4.verified.json')
        data = json.loads(receipt.read_text(encoding='utf8'))
        for location in (['bad-location'], 'relative.mp4', 'C:/wrong.txt'):
            data['local_path'] = location
            receipt.write_text(json.dumps(data), encoding='utf8')
            self.assertIsNone(valid_receipt(self.store.list()[0]))

    def test_verify_progress_waits_for_hash_work_instead_of_completed_state(self):
        _, _, old = self.video()
        self.run_flatten()
        entered, release = threading.Event(), threading.Event()
        events = []
        def slow_hash(*args):
            entered.set()
            release.wait(3)
            return hashlib.sha256(b'video-fixture').hexdigest()
        with patch('local_video.runner.file_hash', side_effect=slow_hash), patch('local_video.runner.probe_video'):
            runner = Runner(self.store, {'output': str(self.output)}, {}, events.append)
            worker = threading.Thread(target=runner.run, kwargs={'recheck': True})
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                import time
                time.sleep(.1)
                progress = [e for e in events if e['type'] == 'operation_progress']
                self.assertTrue(progress)
                self.assertEqual(progress[-1]['done'], 0)
            finally:
                release.set()
                worker.join(3)
        progress = [e for e in events if e['type'] == 'operation_progress']
        self.assertEqual(progress[-1]['done'], 1)
