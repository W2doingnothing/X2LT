from contextlib import closing, contextmanager
from dataclasses import replace
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_video.discovery import Video, media_from_message
from local_video.store import Store
from local_video.archive import original_path, valid_receipt
from local_video.images import download_image, check_image
from local_video.flatten import flatten_completed, rollback_completed
from local_video.runner import Runner, destination_for


def image_bytes(fmt='PNG'):
    from PIL import Image
    buf=BytesIO()
    Image.new('RGB',(32,24),'purple').save(buf,format=fmt)
    return buf.getvalue()


@contextmanager
def fixture_server(body):
    from http.server import ThreadingHTTPServer
    import tests.test_transfer as fixture
    original=fixture.DATA
    fixture.DATA=body
    server=ThreadingHTTPServer(('127.0.0.1',0),fixture.Handler)
    server.etag='"fixture-image-v1"';server.ranges=[];server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def policy(url):
        if not url.startswith(base+'/'):raise ValueError('outside fixture')
        return url
    try:yield server,base,policy
    finally:
        server.shutdown();server.server_close();thread.join();fixture.DATA=original


class ImageTests(unittest.TestCase):
    def test_staging_alias_is_rejected_before_downloader_resolves_it(self):
        class Fake:
            def download(self,*args,**kwargs):raise AssertionError('must not follow staging alias')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.png'
            original=Path.is_symlink
            def staging_alias(p):return str(p).endswith('.incoming') or original(p)
            with patch.object(Path,'is_symlink',staging_alias):
                with self.assertRaises(ValueError):download_image(Fake(),'fixture',path,threading.Event(),lambda *a:None)

    def test_retry_invalid_image_fetches_corrected_server_response(self):
        from local_video.transfer import Downloader
        import tests.test_transfer as fixture
        with tempfile.TemporaryDirectory() as tmp, fixture_server(b'<html>temporary error</html>') as (server,base,policy):
            dest=Path(tmp)/'fixture.png'
            downloader=Downloader(url_policy=policy,connections=1)
            with self.assertRaises(ValueError):download_image(downloader,base+'/file',dest,threading.Event(),lambda *a:None)
            fixture.DATA=image_bytes();server.etag='"corrected"'
            download_image(downloader,base+'/file',dest,threading.Event(),lambda *a:None)
            self.assertEqual(dest.read_bytes(),fixture.DATA)

    def test_real_image_pause_resume_and_wrong_payload(self):
        from PIL import Image
        from local_video.transfer import Downloader, Paused
        buf=BytesIO();Image.new('RGB',(800,800),'purple').save(buf,format='PNG',compress_level=0)
        body=buf.getvalue()
        with tempfile.TemporaryDirectory() as tmp, fixture_server(body) as (server,base,policy):
            dest=Path(tmp)/'fixture.png';cancel=threading.Event()
            downloader=Downloader(connections=1,chunk_size=16384,url_policy=policy,retries=1,request_bytes=4*1024**2)
            def progress(done,total):
                if done>=1048576:cancel.set()
            with self.assertRaises(Paused):download_image(downloader,base+'/slow',dest,cancel,progress)
            self.assertFalse(dest.exists());server.ranges=[]
            result=download_image(downloader,base+'/slow',dest,threading.Event(),lambda *a:None)
            self.assertEqual(result['sha256'],hashlib.sha256(body).hexdigest())
            self.assertEqual(dest.read_bytes(),body)
            self.assertTrue(any(r.startswith('bytes=1048576-') for r in server.ranges))
        with tempfile.TemporaryDirectory() as tmp, fixture_server(b'<html>not an image</html>') as (_,base,policy):
            dest=Path(tmp)/'invalid.png'
            with self.assertRaises(ValueError):download_image(Downloader(url_policy=policy),base+'/file',dest,threading.Event(),lambda *a:None)
            self.assertFalse(dest.exists())

    def test_discovery_modes_preserve_mixed_media_and_text(self):
        from local_video.discovery import discover
        class Fake:
            session=None
            def __iter__(self):
                meta={'tweet_id':'123','user':{'name':'alice'},'content':'mixed text'}
                yield (3,'https://pbs.twimg.com/media/a?format=png&name=orig',{**meta,'num':1,'type':'photo','extension':'png'})
                yield (3,'https://video.twimg.com/v.mp4',{**meta,'num':2,'type':'video','preview_url':'https://pbs.twimg.com/ext_tw_video_thumb/123/a.jpg'})
        with patch('gallery_dl.extractor.find',side_effect=lambda url:Fake()):
            for mode,expected in [('all',['image','video']),('image',['image']),('video',['video'])]:
                found=list(discover('https://x.com/alice/status/123',{},'',threading.Event(),media_mode=mode))
                self.assertEqual([m.media_type for m in found],expected)
                self.assertTrue(all(m.tweet_text=='mixed text' for m in found))

    def test_enrichment_updates_only_existing_metadata_without_download(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            old=Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123')
            job=store.enqueue(old,tmp,'best');store.set_state(job,'completed',bytes=10,sha256='a'*64)
            fresh=replace(old,tweet_text='new text',preview_url='https://pbs.twimg.com/media/a.jpg')
            runner=Runner(store,{}, {},lambda e:None)
            with patch('local_video.runner.discover',return_value=iter([fresh,replace(fresh,media_id='2')])), patch('local_video.runner.Downloader.download',side_effect=AssertionError('download')):
                runner.enrich(store.get_many([job])[0])
            self.assertEqual(len(store.list()),1)
            self.assertEqual(store.get_many([job])[0]['tweet_text'],'new text')
            self.assertEqual(store.get_many([job])[0]['state'],'completed')

    def test_empty_enrichment_retains_existing_text_and_cover(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            media=Video('123','1','alice','https://video.twimg.com/a.mp4','https://x.com/alice/status/123',tweet_text='saved text',preview_url='https://pbs.twimg.com/media/a.jpg')
            job=store.enqueue(media,tmp,'best')
            store.update_metadata(job,replace(media,tweet_text='',preview_url=''))
            row=store.get_many([job])[0]
            self.assertEqual(row['tweet_text'],'saved text')
            self.assertEqual(row['preview_url'],media.preview_url)

    def test_photo_metadata_and_poster_are_distinct(self):
        meta={'tweet_id':'123','num':2,'type':'photo','extension':'jpg','content':'hello 推文','user':{'name':'alice'}}
        row=media_from_message((3,'https://pbs.twimg.com/media/fixture?format=jpg&name=orig',meta))
        self.assertEqual((row.media_type,row.extension,row.tweet_text),('image','jpg','hello 推文'))
        self.assertIsNone(media_from_message((3,row.url,{**meta,'type':'preview'})))
        with self.assertRaises(ValueError):media_from_message((3,'https://evil.test/a.jpg',meta))

    def test_image_dedup_does_not_depend_on_video_quality(self):
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            v=Video('123','1','alice','https://pbs.twimg.com/media/a?format=png','https://x.com/alice/status/123',media_type='image',extension='png',tweet_text='first')
            job=store.enqueue(v,tmp,'best')
            store.set_state(job,'completed',bytes=10,sha256='a'*64,verification='length+sha256')
            self.assertEqual(job,store.enqueue(replace(v,tweet_text='updated'),tmp,'480p'))
            row=store.get_many([job])[0]
            self.assertEqual(row['state'],'completed')
            self.assertEqual(row['tweet_text'],'updated')
            self.assertEqual(original_path(row),Path(tmp).resolve()/'images/alice/123/123_1.png')

    def test_bad_forced_image_never_replaces_good_file(self):
        class Fake:
            def download(self,url,path,*args,**kwargs):
                Path(path).write_bytes(b'<html>not image</html>')
                return {'bytes':22,'sha256':'a'*64,'verification':'length+sha256'}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.png';path.write_bytes(image_bytes())
            original=path.read_bytes()
            with self.assertRaises(ValueError):download_image(Fake(),'fixture',path,threading.Event(),lambda *a:None,force=True)
            self.assertEqual(path.read_bytes(),original)

    def test_actual_image_format_must_match_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.jpg';path.write_bytes(image_bytes())
            with self.assertRaises(ValueError):check_image(path,'jpg')

    def test_image_download_flatten_rollback_and_receipt_dedup(self):
        from local_video.transfer import Downloader
        body=image_bytes()
        def fake_download(self,url,path,*args,**kwargs):
            Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_bytes(body)
            return {'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'verification':'length+sha256'}
        with tempfile.TemporaryDirectory() as tmp, closing(Store(Path(tmp)/'queue.db')) as store:
            v=Video('123','1','alice','https://pbs.twimg.com/media/a?format=png','https://x.com/alice/status/123',media_type='image',extension='png')
            job=store.enqueue(v,tmp,'best')
            with patch.object(Downloader,'download',fake_download):Runner(store,{'output':tmp},{},lambda e:None).run()
            row=store.get_many([job])[0]
            self.assertEqual(row['state'],'completed')
            old=original_path(row)
            self.assertEqual(flatten_completed(store,tmp,Path(tmp)/'集中视频',threading.Event(),lambda e:None)['moved'],1)
            self.assertFalse(old.exists())
            self.assertTrue(valid_receipt(row))
            self.assertEqual(rollback_completed(store,tmp,threading.Event(),lambda e:None)['moved'],1)
            self.assertEqual(old.read_bytes(),body)
            old.unlink()
            with patch.object(Downloader,'download',side_effect=AssertionError('duplicate')):Runner(store,{'output':tmp},{},lambda e:None).run()
            self.assertEqual(store.get_many([job])[0]['state'],'completed')
