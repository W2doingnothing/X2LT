from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_video.discovery import Video
from local_video.runner import Runner
from local_video.store import Store


def video(tweet='123', media='1'):
    return Video(tweet,media,'alice','https://video.twimg.com/fixture.mp4',f'https://x.com/alice/status/{tweet}',tweet_text='推文标题\n正文',preview_url='https://pbs.twimg.com/media/fixture.jpg')


class TweetQueueTests(unittest.TestCase):
    def test_discovery_after_unignore_does_not_start_in_same_run(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            started=threading.Event();release=threading.Event();seen=[]
            def download(downloader,url,path,*args,**kwargs):
                seen.append(str(path));started.set()
                self.assertTrue(release.wait(5))
                return {'bytes':4,'sha256':'a'*64,'verification':'length+sha256'}
            def discovery(*args,**kwargs):
                yield video('123','1')
                self.assertTrue(started.wait(5))
                store.ignore_tweets(['123']);store.ignore_tweets(['123'],False)
                try:yield video('123','2')
                finally:release.set()
            runner=Runner(store,{'output':tmp},{},lambda e:None)
            with patch('local_video.runner.discover',discovery),patch('local_video.runner.Downloader.download',download):
                runner.run(source='fixture')
                self.assertEqual(len(seen),1)
                runner.run();self.assertEqual(len(seen),2)

    def test_profile_url_rejects_non_handle_input(self):
        from local_video.policy import profile_url
        for value in ('alice/../settings','https://evil.test','a b','a'*31):
            with self.assertRaises(ValueError):profile_url(value)

    def test_unignore_during_run_waits_for_next_explicit_run(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            for i in range(1,5):store.enqueue(video('123',str(i)),tmp,'best')
            release=threading.Event();started=threading.Event();seen=[];lock=threading.Lock()
            def download(downloader,url,path,*args,**kwargs):
                with lock:
                    seen.append(str(path))
                    if len(seen)==2:started.set()
                self.assertTrue(release.wait(5))
                return {'bytes':4,'sha256':'a'*64,'verification':'length+sha256'}
            runner=Runner(store,{'output':tmp},{},lambda e:None)
            with patch('local_video.runner.Downloader.download',download):
                thread=threading.Thread(target=runner.run);thread.start()
                try:
                    self.assertTrue(started.wait(5))
                    store.ignore_tweets(['123']);store.ignore_tweets(['123'],False)
                finally:release.set();thread.join(10)
                self.assertEqual(len(seen),2)
                runner.run()
                self.assertEqual(len(seen),4)

    def test_ignore_survives_restart_output_quality_and_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            database=Path(tmp)/'queue.db'
            with closing(Store(database)) as store:
                job=store.enqueue(video(),tmp,'best')
                store.ignore_tweets(['123'])
                self.assertEqual(store.runnable_ids(tmp),[])
                self.assertEqual(store.runnable_ids(force_id=job),[])
                self.assertFalse(store.claim(job,force=True))
                self.assertIsNone(store.enqueue(video('123','2'),Path(tmp)/'other','480p'))
                self.assertEqual(len(store.list()),1)
            with closing(Store(database)) as store:
                self.assertTrue(store.is_ignored('123'))
                store.ignore_tweets(['123'],False)
                self.assertEqual(store.get_many([job])[0]['state'],'pending')
                self.assertEqual(store.runnable_ids(tmp),[job])

    def test_group_page_includes_completed_siblings_and_variants(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            a=store.enqueue(video('123','1'),tmp,'best')
            store.set_state(a,'completed',bytes=4,sha256='a'*64)
            store.enqueue(video('123','2'),tmp,'best')
            store.enqueue(video('123','2'),tmp,'480p')
            store.enqueue(video('456','1'),tmp,'best')
            self.assertEqual(store.tweet_count(),2)
            page=store.tweet_page(limit=1,offset=1)
            self.assertEqual({r['tweet_id'] for r in page[0]},{'123'})
            self.assertEqual(len(page[0]),3)
            store.ignore_tweets(['123'])
            self.assertEqual(store.tweet_count(),1)
            self.assertEqual(store.tweet_count(ignored=True),1)
            self.assertEqual(len(store.tweet_page(ignored=True)[0]),3)

    def test_started_media_finish_but_unstarted_tweet_media_skip(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            jobs=[store.enqueue(video('123',str(i)),tmp,'best') for i in range(1,5)]
            other=store.enqueue(video('456'),tmp,'best')
            release=threading.Event();started=threading.Event();seen=[];lock=threading.Lock()
            def download(downloader,url,path,*args,**kwargs):
                with lock:
                    seen.append(str(path))
                    if len(seen)==2:started.set()
                self.assertTrue(release.wait(5))
                return {'bytes':4,'sha256':'a'*64,'verification':'length+sha256'}
            runner=Runner(store,{'output':tmp},{},lambda e:None)
            with patch('local_video.runner.Downloader.download',download):
                thread=threading.Thread(target=runner.run);thread.start()
                try:
                    self.assertTrue(started.wait(5))
                    store.ignore_tweets(['123'])
                finally:release.set();thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(seen),3)
            rows={r['id']:r for r in store.list()}
            self.assertEqual(sum(rows[j]['state']=='completed' for j in jobs),2)
            self.assertEqual(rows[other]['state'],'completed')
