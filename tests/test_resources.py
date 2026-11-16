import tempfile
import unittest
from pathlib import Path
from local_video.store import Store
from local_video.discovery import Video


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'queue.db');self.addCleanup(self.store.close)

    def add(self,tid='1',mid='1',lib='a',quality='best',state='completed'):
        job=self.store.enqueue(Video(tid,mid,'alice','https://video.twimg.com/a.mp4?secret=hidden','https://x.com/alice/status/'+tid,tweet_text='标题 '+tid),self.root/lib,quality)
        self.store.set_state(job,state)
        return job

    def test_unique_resources_locations_scope_and_stable_recent(self):
        first=self.add();self.add(quality='720p');self.add(lib='b')
        second=self.add(tid='2');self.add(tid='3',state='pending');self.add(tid='4',lib='unconfigured')
        self.store.ignore_tweets(['1'])
        resources=self.store.resources
        page=resources.page([self.root/'a',self.root/'b'])
        self.assertEqual(page['total'],2)
        self.assertEqual([r['message_id'] for r in page['resources']],['2','1'])
        self.assertEqual(len(page['resources'][1]['locations']),3)
        self.assertNotIn('secret',repr(page));self.assertNotIn('video.twimg',repr(page))
        self.store.set_state(first,'completed')
        self.assertEqual(resources.page([self.root/'a'])['resources'][0]['message_id'],'2')
        self.store.catalog.set_tags('1',['旅行'])
        self.assertEqual(resources.page([self.root/'a'],tags=['旅行'])['total'],1)
        self.assertEqual(resources.page([self.root/'a'],query='标题 2')['total'],1)
        self.assertEqual(resources.page([self.root/'a'],offset=1,limit=1)['resources'][0]['message_id'],'1')
        self.assertEqual(resources.summary([self.root/'a'])['video_count'],2)
        self.assertEqual(resources.page([])['total'],0)

    def test_filename_search_including_unmoved_completed_files(self):
        self.add(tid='123',mid='456')
        page=self.store.resources.page([self.root/'a'],query='123_456.mp4')
        self.assertEqual(page['total'],1)
        self.assertEqual(page['resources'][0]['locations'][0]['filename'],'123_456.mp4')

    def test_legacy_backfill_is_undated_and_new_additions_sort_first(self):
        self.add();self.add(tid='2')
        with self.store.db:
            self.store.db.execute('DELETE FROM resource_entries')
            self.store.db.execute("DELETE FROM catalog_migrations WHERE name='resources-v1'")
        from local_video.resources import Resources
        projection=Resources(self.store.db,self.store.lock)
        page=projection.page([self.root/'a'])
        self.assertTrue(all(r['added_at'] is None for r in page['resources']))
        self.add(tid='3')
        self.assertEqual(projection.page([self.root/'a'])['resources'][0]['message_id'],'3')
        other=Store(self.root/'queue.db');self.addCleanup(other.close)
        self.assertEqual([r['message_id'] for r in other.resources.page([self.root/'a'])['resources']],['3','2','1'])
