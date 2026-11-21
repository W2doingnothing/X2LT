from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from local_video.discovery import Video
from local_video.store import Store
from local_video.model import MediaModel


class ModelTests(unittest.TestCase):
    def test_same_quality_different_dimensions_keep_actual_variant_metadata(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            for i,(width,height) in enumerate(((1280,720),(1920,1080))):
                job=store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123',width,height),Path(tmp)/str(i),'best')
                store.set_state(job,'completed')
            page=store.resources.page([Path(tmp)/'0',Path(tmp)/'1'])
            self.assertEqual({(m['width'],m['height']) for m in page['resources'][0]['locations']},{(1280,720),(1920,1080)})

    def test_status_change_does_not_overwrite_new_content_text(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            one=store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123',tweet_text='older'),tmp,'best')
            store.enqueue(Video('123','2','alice','https://video.twimg.com/b.mp4','https://x.com/alice/status/123',tweet_text='newer'),tmp,'best')
            store.set_state(one,'completed')
            self.assertEqual(store.db.execute('SELECT text FROM model_contents').fetchone()[0],'newer')

    def test_generic_and_x_saved_order_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            content=store.model.content('local','library','uuid')
            media=store.model.media(content,'file','image')
            variant=store.model.variant(media,'original','jpg')
            store.model.location(variant,tmp,str(Path(tmp)/'local.jpg'))
            job=store.enqueue(Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123'),tmp,'best')
            store.set_state(job,'completed')
            self.assertEqual(store.resources.page([tmp])['total'],2)

    def test_namespaced_identity_and_authorless_media_without_download(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            model=store.model
            a=model.content('telegram','channel-a','42')
            b=model.content('telegram','channel-b','42')
            c=model.content('x','','42',author='alice')
            self.assertEqual(len({a,b,c}),3)
            self.assertEqual(model.content('telegram','channel-a','42'),a)
            local=model.content('local','library-a','local-uuid',text='本地文件')
            media=model.media(local,'original','video')
            variant=model.variant(media,'original','mp4',1080,1920)
            location=model.location(variant,tmp,str(Path(tmp)/'local.mp4'),state='completed',bytes=12)
            task=model.create_task(variant,kind='import',location_id=location,state='completed')
            self.assertEqual(store.db.execute('SELECT kind,job_id FROM model_tasks WHERE id=?',(task,)).fetchone()[0],'import')
            self.assertEqual(store.list(),[])
            self.assertEqual(store.resources.summary([tmp])['video_count'],1)

    def test_x_writes_keep_variants_locations_removal_and_rebind_in_sync(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'q.db')) as store:
            v=Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123')
            one=store.enqueue(v,Path(tmp)/'a','best')
            two=store.enqueue(v,Path(tmp)/'b','best')
            three=store.enqueue(v,Path(tmp)/'a','720p')
            for job in (one,two,three):store.set_state(job,'completed',bytes=100,sha256='a'*64)
            self.assertEqual(store.model.counts(),dict(contents=1,media=1,variants=2,locations=3,tasks=3))
            operation=store.removal.prepare(['123'],Path(tmp)/'a')
            self.assertEqual(store.resources.summary([Path(tmp)/'a'])['video_count'],0)
            self.assertEqual(store.resources.summary([Path(tmp)/'b'])['video_count'],1)
            self.assertEqual(store.model.task(one)['state'],'removing')
            store.relocate(two,Path(tmp)/'moved.mp4',Path(tmp)/'b/original.mp4')
            self.assertEqual(store.model.task(two)['local_path'],str((Path(tmp)/'moved.mp4').resolve()))

    def test_upgrade_backup_and_interrupted_backfill_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'q.db'
            with closing(Store(path)) as store:
                for i in range(4):store.enqueue(Video(str(100+i),'1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123'),tmp,'best')
                expected=store.list()
                with store.lock,store.db:
                    for table in ('model_tasks','model_locations','model_saved','model_variants','model_media','model_contents','model_sources','model_migrations'):
                        store.db.execute('DELETE FROM '+table)
                def interrupt(cursor):raise RuntimeError('synthetic interruption')
                with self.assertRaises(RuntimeError):store.model.migrate(batch=2,after_batch=interrupt)
                self.assertEqual(store.model.migration()['cursor'],2)
            with closing(Store(path)) as store:
                self.assertTrue(store.model.migration()['done'])
                self.assertEqual(store.model.counts()['tasks'],4)
                self.assertEqual(store.list(),expected)
                backup=path.with_name(path.name+'.pre-model-v1.bak')
                self.assertTrue(backup.is_file())
                with closing(sqlite3.connect(backup)) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],4)


if __name__=='__main__':unittest.main()
