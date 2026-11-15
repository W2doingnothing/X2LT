from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image

from local_video.previews import PreviewCache, PreviewService


class PreviewTests(unittest.TestCase):
    def test_windows_junction_cannot_become_cache_or_clear_target(self):
        import os
        if os.name!='nt':self.skipTest('Windows junction regression')
        import _winapi
        if not hasattr(_winapi,'CreateJunction'):self.skipTest('junction API unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'cache';external=Path(tmp)/'unrelated';external.mkdir()
            victim=external/('a'*64+'.jpg');victim.write_bytes(b'unrelated')
            _winapi.CreateJunction(str(external),str(root))
            try:
                with self.assertRaises(ValueError):PreviewCache(root)
                self.assertEqual(victim.read_bytes(),b'unrelated')
            finally:root.rmdir()
            cache=PreviewCache(root);root.rmdir()
            _winapi.CreateJunction(str(external),str(root))
            try:
                with self.assertRaises(ValueError):cache.trim(clear=True)
                self.assertEqual(victim.read_bytes(),b'unrelated')
            finally:root.rmdir()

    def test_http_limits_and_redirect_policy(self):
        from tests.test_images import fixture_server
        from local_video.previews import MAX_RESPONSE
        with tempfile.TemporaryDirectory() as tmp, fixture_server(b'x'*(MAX_RESPONSE+1)) as (_,base,policy):
            cache=PreviewCache(Path(tmp)/'cache',url_policy=policy)
            with self.assertRaises(ValueError):cache._fetch(base+'/file','',threading.Event())
            with self.assertRaises(ValueError):cache._fetch(base+'/redirect','',threading.Event())

    def test_cached_thumbnail_needs_no_second_network_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=PreviewCache(Path(tmp)/'cache')
            row={'tweet_id':'123','media_id':'1','preview_url':'https://pbs.twimg.com/media/fixture?format=png&name=orig','media_type':'video'}
            body=BytesIO();Image.new('RGB',(64,48),'purple').save(body,format='PNG')
            with patch.object(cache,'_fetch',return_value=body.getvalue()) as fetch:
                self.assertEqual(cache.load(row,'',threading.Event(),(32,32)).size,(32,24))
                self.assertEqual(cache.load(row,'',threading.Event(),(16,16)).size,(16,12))
            self.assertEqual(fetch.call_count,1)

    def test_corrupt_cache_is_replaced_and_quota_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=PreviewCache(Path(tmp)/'cache',max_bytes=2000)
            row={'tweet_id':'123','media_id':'1','preview_url':'https://pbs.twimg.com/media/fixture.png','media_type':'video'}
            body=BytesIO();Image.new('RGB',(32,24),'red').save(body,format='PNG')
            with patch.object(cache,'_fetch',return_value=body.getvalue()):cache.load(row,'',threading.Event(),(16,16))
            files=list(cache.root.glob('*.jpg'));self.assertEqual(len(files),1)
            files[0].write_bytes(b'corrupt')
            with patch.object(cache,'_fetch',return_value=body.getvalue()) as fetch:
                self.assertEqual(cache.load(row,'',threading.Event(),(16,16)).size,(16,12))
                self.assertEqual(fetch.call_count,1)
            (cache.root/("a"*64+".jpg")).write_bytes(b"x"*2000)
            cache.trim()
            self.assertLessEqual(sum(p.stat().st_size for p in cache.root.glob('*.jpg')),2000)

    def test_invalid_url_and_cancel_never_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=PreviewCache(Path(tmp)/'cache')
            with patch.object(cache,'_fetch',side_effect=AssertionError('network')):
                with self.assertRaises(ValueError):cache.load({'preview_url':'https://evil.test/a'},'',threading.Event(),(32,32))
                cancel=threading.Event();cancel.set()
                with self.assertRaises(Exception):cache.load({'preview_url':'https://pbs.twimg.com/media/a'},'',cancel,(32,32))

    def test_service_emits_pil_result_and_close_prevents_new_work(self):
        events=[]
        with tempfile.TemporaryDirectory() as tmp:
            service=PreviewService(Path(tmp)/'cache',events.append)
            with patch.object(service.cache,'load',return_value=Image.new('RGB',(2,2))):
                future=service.request({'preview_url':'https://pbs.twimg.com/media/a'},'','fixture',(32,32))
                future.result(timeout=5)
            self.assertEqual(events[0]['token'],'fixture')
            self.assertIsInstance(events[0]['image'],Image.Image)
            service.close()
            self.assertIsNone(service.request({},'','late',(32,32)))
