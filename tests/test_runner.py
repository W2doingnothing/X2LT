from pathlib import Path
from contextlib import closing
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_video.discovery import Video
from local_video.runner import Runner, InstanceLock, destination_for
from local_video.store import Store
from local_video.transfer import Paused


class RunnerTests(unittest.TestCase):
    def test_precancel_is_respected_before_job_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'jobs.sqlite3')
            video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123')
            store.enqueue(video, tmp, 'best')
            runner = Runner(store, {}, {}, lambda e: None)
            runner.cancel.set()
            with patch('local_video.runner.Downloader.download', return_value={'bytes': 4, 'sha256': 'x', 'verification': 'sha256-only'}):
                runner.run()
            self.assertEqual(store.list()[0]['state'], 'pending')
            store.close()

    def test_output_quality_scoped_and_sandboxed(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = {'output': tmp, 'quality': '720p', 'author': 'alice', 'tweet_id': '123', 'media_id': '1'}
            self.assertEqual(destination_for(row), Path(tmp).resolve() / '720p/alice/123/123_1.mp4')
            with self.assertRaises(ValueError):
                destination_for({**row, 'author': '../../escape'})

    def test_duplicate_instance_is_blocked_then_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = InstanceLock(Path(tmp) / 'run.lock')
            with self.assertRaises(RuntimeError):
                InstanceLock(Path(tmp) / 'run.lock')
            first.close()
            second = InstanceLock(Path(tmp) / 'run.lock')
            second.close()

    def test_paused_work_becomes_completed_and_error_is_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'jobs.sqlite3')
            video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123')
            job_id = store.enqueue(video, tmp, 'best')
            events = []
            runner = Runner(store, {'proxy': '', 'connections': 1}, {'auth_token': 'fixture-secret'}, events.append)
            with patch('local_video.runner.Downloader.download', side_effect=Paused('paused')):
                runner.run()
            self.assertEqual(store.list()[0]['state'], 'paused')
            with patch('local_video.runner.Downloader.download', return_value={'bytes': 4, 'sha256': 'hash', 'verification': 'length+sha256'}):
                runner.run()
            self.assertEqual(store.list()[0]['state'], 'completed')
            store.set_state(job_id, 'pending')
            with patch('local_video.runner.Downloader.download', side_effect=OSError('auth_token=fixture-secret')):
                runner.run()
            self.assertNotIn('fixture-secret', str(events))
            self.assertNotIn('fixture-secret', store.list()[0]['error'])
            store.close()


class IncrementalQueueTests(unittest.TestCase):
    def test_initial_query_failure_reports_error_and_finishes(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            events=[]
            runner=Runner(store,{}, {},events.append)
            with patch.object(store,'runnable_ids',side_effect=OSError('fixture query failure')):
                runner.run()
            self.assertEqual(events[-1]['type'],'finished')
            self.assertTrue(any(e['type']=='message' and 'fixture query failure' in e['text'] for e in events))

    def test_scheduling_and_discovery_do_not_reload_history(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            videos=[Video(str(100+i),'1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123') for i in range(5)]
            for video in videos[:3]: store.enqueue(video,tmp,'best')
            seen=[]; events=[]
            runner=Runner(store,{'output':tmp}, {},events.append)
            with patch.object(store,'list',side_effect=AssertionError('full history scan')), patch.object(runner,'_job',side_effect=lambda row,**kw:seen.append(row['id'])), patch('local_video.runner.discover',return_value=iter(videos)):
                runner.run(source='fixture')
            self.assertEqual(sorted(seen),[1,2,3,4,5])
            self.assertTrue(all('id' in e for e in events if e['type']=='changed'))


if __name__ == '__main__':
    unittest.main()
