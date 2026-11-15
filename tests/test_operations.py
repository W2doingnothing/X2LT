import unittest
from contextlib import closing
from pathlib import Path
import tempfile
from unittest.mock import patch
from local_video.operations import OperationState
from local_video.runner import Runner
from local_video.store import Store
from local_video.discovery import Video


class OperationTest(unittest.TestCase):
    def test_unknown_reading_total_and_recoverable_snapshot(self):
        state=OperationState();run=state.begin(reading=True)
        state.update({'type':'run_stats','run_id':run,'discovered_tweets':3,'discovered_media':5,'queued':4,'processed':1,'successful':1,'failed':0,'skipped':1})
        snap=state.snapshot()
        self.assertTrue(snap['reading']);self.assertIsNone(snap['total'])
        self.assertEqual(snap['discovered_media'],5)
        snap['queued']=999
        self.assertEqual(state.snapshot()['queued'],4)

    def test_stale_events_cannot_replace_new_run(self):
        state=OperationState();old=state.begin();new=state.begin(reading=True)
        state.update({'type':'finished','run_id':old})
        self.assertEqual(state.snapshot()['run_id'],new)
        self.assertEqual(state.snapshot()['phase'],'reading')

    def test_end_with_failures_is_not_success_and_pause_retains_reason(self):
        state=OperationState();run=state.begin()
        state.update({'type':'run_stats','run_id':run,'reading':False,'total':4,'processed':4,'successful':3,'failed':1})
        state.update({'type':'finished','run_id':run})
        self.assertEqual(state.snapshot()['phase'],'failed')
        run=state.begin();state.update({'type':'run_paused','run_id':run,'text':'存储空间不足'})
        state.update({'type':'finished','run_id':run})
        self.assertEqual(state.snapshot()['phase'],'paused')
        self.assertIn('空间不足',state.snapshot()['message'])

    def test_runner_discovery_counts_deduplicate_and_finalize_total(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            videos=[Video(str(100+i),'1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/100') for i in range(4)]
            store.enqueue(videos[0],tmp,'best')
            completed=store.enqueue(videos[2],tmp,'best');store.set_state(completed,'completed')
            store.ignore_tweets([videos[3].tweet_id],True)
            events=[];runner=Runner(store,{'output':tmp},{},events.append)
            with patch('local_video.runner.discover',return_value=iter([*videos,videos[0]])),patch.object(runner,'_job',return_value='successful'):
                runner.run(source='https://x.com/fixture/likes')
            stats=[e for e in events if e['type']=='run_stats']
            self.assertTrue(any(e['reading'] and e['total'] is None for e in stats))
            last=stats[-1]
            self.assertEqual((last['discovered_tweets'],last['discovered_media']),(4,4))
            self.assertEqual((last['total'],last['queued'],last['processed'],last['successful'],last['skipped']),(4,2,4,2,2))

    def test_runner_pre_cancel_emits_paused(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            events=[];runner=Runner(store,{'output':tmp},{},events.append);runner.cancel.set();runner.run()
            self.assertTrue(any(e['type']=='run_paused' for e in events))

    def test_worker_unexpected_failure_still_finishes_with_failed_count(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            store.enqueue(Video('123','1','fixture','https://video.twimg.com/a.mp4','https://x.com/fixture/status/123'),tmp,'best')
            events=[];runner=Runner(store,{'output':tmp},{},events.append)
            with patch.object(runner,'_job',side_effect=RuntimeError('fixture failure')):
                runner.run()
            self.assertEqual(events[-1]['type'],'finished')
            self.assertEqual([e for e in events if e['type']=='run_stats'][-1]['failed'],1)
