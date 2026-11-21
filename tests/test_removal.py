from contextlib import closing
from pathlib import Path
import hashlib
import tempfile
import unittest
import threading
from unittest.mock import patch

from local_video.archive import original_path, save_receipt, valid_receipt
from local_video.discovery import Video
from local_video.store import Store
from local_video.runner import Runner
from local_video.transfer import Paused


class RemovalTests(unittest.TestCase):
    def test_replacement_during_hash_is_not_recycled(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            job,path=self.make_job(store,tmp)
            operation=store.removal.prepare(['123'],tmp)
            real_open=Path.open
            class Reader:
                def __init__(self,handle):self.handle=handle
                def __enter__(self):return self
                def __exit__(self,*args):return self.handle.__exit__(*args)
                def __getattr__(self,name):return getattr(self.handle,name)
                def read(self,size):
                    data=self.handle.read(size)
                    if not data:
                        with real_open(path,'wb') as changed:changed.write(b'unknown-media')
                    return data
            def opened(p,*args,**kw):
                handle=real_open(p,*args,**kw)
                return Reader(handle) if p==path and args==('rb',) else handle
            recycled=[]
            with patch.object(Path,'open',opened):
                result=store.removal.process(operation['id'],recycle=lambda p:recycled.append(p))
            self.assertEqual(recycled,[])
            self.assertEqual(result['failed'],1)

    def test_promoted_force_download_and_relocated_original_are_both_removed(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            job,original=self.make_job(store,tmp)
            moved=Path(tmp)/'concentrated.mp4';original.rename(moved)
            store.relocate(job,moved,original);store.finish_relocation(job)
            save_receipt(store.get_many([job])[0],moved)
            runner=Runner(store,{'output':tmp},{},lambda e:None)
            operations=[]
            def downloading(_,url,path,cancel,progress,**kw):
                path.write_bytes(b'new-fixture-media')
                operations.append(store.removal.prepare(['123'],tmp))
                return {'bytes':17,'sha256':hashlib.sha256(b'new-fixture-media').hexdigest(),'verification':'length+sha256'}
            with patch('local_video.runner.Downloader.download',downloading), patch('local_video.runner.probe_video'):
                runner._job(store.get_many([job])[0],force=True)
            result=store.removal.process(operations[0]['id'],recycle=lambda p:p.unlink(),runner=runner)
            self.assertEqual(result['failed'],0,result)
            self.assertFalse(original.exists())
            self.assertFalse(moved.exists())

    def test_image_incoming_staging_and_its_parts_are_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            video=Video('123','1','alice','https://pbs.twimg.com/media/a.jpg','https://x.com/alice/status/123',media_type='image',extension='jpg')
            job=store.enqueue(video,tmp,'best')
            original=original_path(store.get_many([job])[0])
            original.parent.mkdir(parents=True)
            incoming=original.with_suffix('.jpg.incoming');incoming.write_bytes(b'partial staging')
            incoming.with_suffix('.incoming.verified.json').write_text('{}')
            parts=incoming.with_suffix('.incoming.parts');parts.mkdir();(parts/'0.part').write_bytes(b'partial')
            operation=store.removal.prepare(['123'],tmp)
            result=store.removal.process(operation['id'],recycle=lambda p:p.unlink())
            self.assertEqual(result['failed'],0)
            self.assertFalse(incoming.exists())
            self.assertFalse(list(Path(tmp).rglob('*.parts')))

    def test_removal_cancels_only_target_worker_and_cleans_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            video=Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123')
            job=store.enqueue(video,tmp,'best')
            runner=Runner(store,{'output':tmp},{},lambda e:None)
            started=threading.Event()
            def downloading(_,url,path,cancel,progress,**kw):
                path.parent.mkdir(parents=True,exist_ok=True)
                parts=path.with_suffix('.mp4.parts');parts.mkdir()
                (parts/'0.part').write_bytes(b'partial')
                started.set()
                self.assertTrue(cancel.wait(3))
                raise Paused('target cancelled')
            with patch('local_video.runner.Downloader.download',downloading):
                thread=threading.Thread(target=runner.run);thread.start()
                try:
                    self.assertTrue(started.wait(3))
                    operation=store.removal.prepare(['123'],tmp)
                    result=store.removal.process(operation['id'],recycle=lambda p:p.unlink(),runner=runner)
                    self.assertFalse(runner.cancel.is_set())
                    self.assertEqual(result['done'],1)
                finally:runner.cancel.set();thread.join(5)
            self.assertEqual(store.get_many([job])[0]['state'],'removed')
            self.assertFalse(list(Path(tmp).rglob('*.parts')))

    def test_recycle_noop_is_failure_not_success(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            job,path=self.make_job(store,tmp)
            operation=store.removal.prepare(['123'],tmp)
            result=store.removal.process(operation['id'],recycle=lambda p:None)
            self.assertEqual(result['failed'],1)
            self.assertTrue(path.exists())

    def test_windows_recycle_uses_synthetic_file_only(self):
        import os
        if os.name!='nt':self.skipTest('Windows recycle API')
        from local_video.removal import recycle_file
        with tempfile.TemporaryDirectory() as tmp:
            fixture=Path(tmp)/'x2lt-synthetic-recycle-test.txt'
            fixture.write_text('Synthetic recycle API smoke test')
            recycle_file(fixture)
            self.assertFalse(fixture.exists())

    def make_job(self, store, output, message='123', media='1'):
        job = store.enqueue(Video(message, media, 'alice', 'https://video.twimg.com/a.mp4',
                                  f'https://x.com/alice/status/{message}', tweet_text='同一段内容'), output, 'best')
        row = store.get_many([job])[0]
        path = original_path(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture-media')
        store.set_state(job, 'completed', bytes=13, sha256=hashlib.sha256(b'fixture-media').hexdigest(), verification='length+sha256')
        save_receipt(store.get_many([job])[0], path)
        return job, path

    def test_removed_receipt_cannot_restore_completion_and_only_new_scan_rearms(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            job, path = self.make_job(store, tmp)
            baseline = store.ignore_snapshot()
            operation = store.removal.prepare(['123'], tmp)
            store.removal.process(operation['id'], recycle=lambda p: p.unlink())
            self.assertFalse(path.exists())
            self.assertIsNone(valid_receipt(store.get_many([job])[0]))
            self.assertEqual(store.catalog.page()['total'], 0)
            self.assertTrue(store.is_ignored('123'))
            key = store.removal.page()['items'][0]['key']
            store.removal.clear([key])
            self.assertEqual(store.get_many([job])[0]['state'], 'removed')
            self.assertEqual(store.runnable_ids(), [])
            video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123')
            self.assertIsNone(store.enqueue(video, tmp, 'best', baseline=baseline))
            store.enqueue(video, tmp, 'best', baseline=store.ignore_snapshot())
            self.assertEqual(store.get_many([job])[0]['state'], 'pending')

    def test_scoped_delete_preserves_other_library_and_rejects_stale_completion(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            a, path = self.make_job(store, Path(tmp)/'a')
            b, other = self.make_job(store, Path(tmp)/'b')
            operation = store.removal.prepare(['123'], Path(tmp)/'a')
            store.set_state(a, 'completed', bytes=99)
            self.assertEqual(store.get_many([a])[0]['state'], 'removing')
            store.removal.process(operation['id'], recycle=lambda p: p.unlink())
            self.assertFalse(path.exists())
            self.assertTrue(other.exists())
            self.assertEqual(store.get_many([b])[0]['state'], 'completed')

    def test_failure_blocks_clear_and_retry_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp)/'q.db'
            with closing(Store(database)) as store:
                job, path = self.make_job(store, tmp)
                operation = store.removal.prepare(['123'], tmp)
                def denied(p):
                    raise OSError('fixture locked')
                store.removal.process(operation['id'], recycle=denied)
                self.assertTrue(path.exists())
                self.assertEqual(store.removal.status(operation['id'])['failed'], 1)
                with self.assertRaises(ValueError):
                    store.removal.clear([store.removal.page()['items'][0]['key']])
            with closing(Store(database)) as store:
                store.removal.process(operation['id'], recycle=lambda p: p.unlink())
                self.assertFalse(path.exists())
                self.assertEqual(store.removal.status(operation['id'])['failed'], 0)

    def test_changed_file_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            job, path = self.make_job(store, tmp)
            path.write_bytes(b'not-the-original')
            operation = store.removal.prepare(['123'], tmp)
            store.removal.process(operation['id'], recycle=lambda p: p.unlink())
            self.assertTrue(path.exists())
            self.assertEqual(store.removal.status(operation['id'])['failed'], 1)

    def test_old_ignore_is_listed_and_clear_does_not_enqueue(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            job, path = self.make_job(store, tmp)
            store.ignore_tweets(['123'])
            page = store.removal.page(query='alice')
            self.assertEqual(page['total'], 1)
            self.assertEqual(page['items'][0]['reason'], 'ignored')
            store.removal.clear([page['items'][0]['key']])
            self.assertFalse(store.is_ignored('123'))
            self.assertTrue(path.exists())
            self.assertEqual(store.get_many([job])[0]['state'], 'completed')


if __name__ == '__main__':
    unittest.main()
