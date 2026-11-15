from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_video.discovery import Video, discover, video_from_message, select_variant
from local_video.store import Store


class DiscoveryTests(unittest.TestCase):
    def test_extractor_redirect_rejected_before_second_request(self):
        import requests
        from local_video.discovery import guard_session, DiscoveryError
        class Adapter(requests.adapters.BaseAdapter):
            def __init__(self):
                self.urls = []
            def send(self, request, **kw):
                self.urls.append(request.url)
                response = requests.Response()
                response.status_code = 302
                response.url = request.url
                response.request = request
                response.headers['Location'] = 'https://127.0.0.1/private'
                response._content = b''
                response._content_consumed = True
                return response
            def close(self):
                pass
        session = requests.Session()
        adapter = Adapter()
        session.mount('https://', adapter)
        guard_session(session, threading.Event())
        with self.assertRaises(DiscoveryError):
            session.get('https://x.com/test', headers={'x-csrf-token': 'fixture-csrf'})
        self.assertEqual(adapter.urls, ['https://x.com/test'])
        session.close()

    def test_native_video_only_and_stable_filename(self):
        meta = {'tweet_id': '123', 'num': 2, 'type': 'video', 'user': {'name': 'alice'}, 'width': 1280, 'height': 720}
        video = video_from_message((3, 'https://video.twimg.com/ext_tw_video/456/pu/vid/avc1/1280x720/a.mp4?tag=1', meta))
        self.assertEqual(video.tweet_id, '123')
        self.assertEqual(video.relative_path, 'alice/123/123_2.mp4')
        for kind in ['photo', 'animated_gif']:
            self.assertIsNone(video_from_message((3, 'https://video.twimg.com/a.mp4', {**meta, 'type': kind})))
        self.assertIsNone(video_from_message((6, 'https://evil.test', meta)))

    def test_path_traversal_author_is_sanitized(self):
        video = video_from_message((3, 'https://video.twimg.com/a.mp4', {'tweet_id': '123', 'num': 1, 'type': 'video', 'user': {'name': '../../evil'}}))
        self.assertNotIn('..', video.relative_path)

    def test_quality_selects_real_variant_not_rewritten_url(self):
        variants = [
            {'url': 'https://video.twimg.com/vid/1920x1080/a.mp4', 'bitrate': 4000, 'content_type': 'video/mp4'},
            {'url': 'https://video.twimg.com/vid/1280x720/b.mp4', 'bitrate': 2000, 'content_type': 'video/mp4'},
            {'url': 'https://video.twimg.com/vid/640x360/c.mp4', 'bitrate': 500, 'content_type': 'video/mp4'},
        ]
        self.assertEqual(select_variant(variants, '720p')['bitrate'], 2000)
        self.assertEqual(select_variant(variants, 'best')['bitrate'], 4000)

    def test_discover_propagates_proxy_and_uses_memory_cookies(self):
        from gallery_dl import config
        class Fake:
            session = None
            def __iter__(self):
                self.test_values = config.get(('extractor', 'twitter'), 'cookies')
                self.proxy = config.get(('extractor',), 'proxy')
                yield (3, 'https://video.twimg.com/a.mp4', {'tweet_id': '123', 'num': 1, 'type': 'video', 'user': {'name': 'alice'}})
                yield (3, 'https://video.twimg.com/a.mp4', {'tweet_id': '123', 'num': 1, 'type': 'video', 'user': {'name': 'alice'}})
        fake = Fake()
        with patch('gallery_dl.extractor.find', return_value=fake):
            videos = list(discover('https://x.com/alice/likes', {'auth_token': 'fixture-token', 'ct0': 'fixture-csrf'}, 'socks5://127.0.0.1:7897', threading.Event(), limit=10))
        self.assertEqual(len(videos), 1)
        self.assertEqual(fake.test_values, {'auth_token': 'fixture-token', 'ct0': 'fixture-csrf'})
        self.assertEqual(fake.proxy, 'socks5h://127.0.0.1:7897')
        self.assertIsNone(config.get(('extractor', 'twitter'), 'cookies'))


class StoreTests(unittest.TestCase):
    def test_rediscovered_completed_job_retains_completion_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'jobs.sqlite3')
            video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123')
            job = store.enqueue(video, tmp, 'best')
            store.set_state(job, 'completed', bytes=4, sha256='test', verification='sha256-only')
            self.assertEqual(store.list()[0]['verification'], 'sha256-only')
            store.enqueue(video, tmp, 'best')
            self.assertEqual(store.list()[0]['state'], 'completed')
            store.close()

    def test_queue_dedup_recovery_and_output_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'queue.sqlite3')
            video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123', 1280, 720)
            first = store.enqueue(video, str(Path(tmp) / 'out'), 'best')
            self.assertEqual(store.enqueue(video, str(Path(tmp) / 'out'), 'best'), first)
            second = store.enqueue(video, str(Path(tmp) / 'other'), 'best')
            self.assertNotEqual(second, first)
            store.set_state(first, 'downloading')
            store.recover()
            rows = {r['id']: r for r in store.list()}
            self.assertEqual(rows[first]['state'], 'paused')
            self.assertEqual(len(rows), 2)
            store.close()


if __name__ == '__main__':
    unittest.main()
