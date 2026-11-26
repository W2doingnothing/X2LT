import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import Mock, patch

from PIL import Image

from local_video.desktop_service import DesktopService


class TelegramTaskCardTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = DesktopService(self.root / 'data', self.root / 'home')
        self.addCleanup(self.app._close)
        self.library = self.app._libraries.get()['id']
        self.transport = self.app._telegram._transport
        self.transport.status = Mock(return_value={'authorized': True})
        with BytesIO() as image:
            Image.new('RGB', (32, 18), 'red').save(image, format='JPEG')
            self.image = image.getvalue()
        self.transport.chunks = Mock(side_effect=lambda item, offset: iter([self.image[offset:]]))
        self.item = dict(peer_id='-10042', peer_title='测试频道', message_id='8', media_id='21',
                         title='任务标题', body='消息正文', kind='image', size=len(self.image),
                         name='fixture.jpg', extension='.jpg', width=32, height=18,
                         url='https://t.me/c/42/8', sent_at=1234, forward_id='-10060',
                         forward_title='转发来源')
        self.transport.scan = Mock(return_value=[self.item])
        self.transport.preview = Mock(return_value=None)

    def call(self, action, payload=None):
        result = self.app.call(action, payload)
        self.assertTrue(result['ok'], result)
        return result['data']

    def until(self, condition):
        deadline = monotonic() + 4
        while monotonic() < deadline:
            if result := condition():
                return result
            sleep(.01)
        self.fail('Telegram background operation timed out')

    def scan(self):
        scan_id = self.call('tg_scan', dict(mode='channel', source='-10042', limit=50, media_mode='all'))['id']
        items = self.until(lambda: self.call('tg_scan_status', {'id': scan_id})['items'])
        return scan_id, items[0]

    def complete(self, scan_id, item, library):
        self.assertEqual(self.call('tg_enqueue', dict(scan_id=scan_id, ids=[item['id']], library_id=library)), 1)
        return self.until(lambda: next((job for job in self.call('tg_jobs')
                                       if job['library_id'] == library and job['state'] == 'completed'), None))

    def test_scan_and_queue_share_media_keys_across_scans_and_libraries(self):
        scan_id, item = self.scan()
        job = self.complete(scan_id, item, self.library)
        self.assertEqual(item['message_key'], '-10042:8')
        self.assertEqual(item['media_key'], '-10042:8:21')
        self.assertEqual(job['kind'], 'image')
        self.assertTrue({'peer_id', 'url', 'path', 'filename'}.isdisjoint(job))
        for field in ('message_key', 'media_key', 'body', 'sent_at', 'forward_title', 'forward_id',
                      'avatar_key', 'extension', 'width', 'height'):
            self.assertEqual(job[field], item[field], field)
        next_id, next_item = self.scan()
        self.assertNotEqual(item['id'], next_item['id'])
        self.assertEqual(item['media_key'], next_item['media_key'])
        self.assertEqual(self.call('tg_scan_status', dict(id=next_id, library_id=self.library))['items'][0]
                         ['download_state'], 'completed')
        second = self.root / 'second'
        second.mkdir()
        library = self.app._libraries.add('第二媒体库', second)['id']
        self.assertEqual(self.call('tg_scan_status', dict(id=next_id, library_id=library))['items'][0]
                         ['download_state'], 'new')
        other_job = self.complete(next_id, next_item, library)
        self.assertEqual(other_job['media_key'], job['media_key'])
        self.assertNotEqual(other_job['id'], job['id'])

    def test_old_queue_rows_keep_their_identity_without_optional_metadata(self):
        identifier = 'a' * 32
        queue = self.app._telegram._queue
        queue._write('''INSERT INTO telegram_downloads
            (id, library_id, peer_id, message_id, media_id, payload, filename, total, state)
            VALUES (?,?,?,?,?,?,?,?,'paused')''',
                     (identifier, self.library, '-10042', '8', '99',
                      json.dumps({'title': '', 'name': 'old.jpg', 'kind': 'image'}), identifier + '-old.jpg', 16))
        job, = self.call('tg_jobs')
        self.assertEqual((job['message_key'], job['media_key']), ('-10042:8', '-10042:8:99'))
        self.assertEqual((job['id'], job['title'], job['state']), (identifier, 'old.jpg', 'paused'))

    def test_job_preview_reuses_scan_cache_and_rejects_unknown_queue_ids(self):
        self.transport.preview.return_value = self.image
        scan_id, item = self.scan()
        preview = self.call('tg_scan_preview', dict(id=item['id'], scan_id=scan_id))
        job = self.complete(scan_id, item, self.library)
        self.assertEqual(self.call('tg_job_preview', {'id': job['id']}), preview)
        self.assertIsNone(self.call('tg_job_preview', {'id': item['id']}))
        self.assertIsNone(self.call('tg_job_preview', {'id': "' OR 1=1 --"}))
        self.assertFalse(self.app.call('tg_job_preview', {'path': str(self.root)})['ok'])
        self.transport.preview.assert_called_once()

    def test_completed_job_falls_back_to_the_checked_local_thumbnail(self):
        scan_id, item = self.scan()
        job = self.complete(scan_id, item, self.library)
        self.assertTrue(self.call('tg_job_preview', {'id': job['id']}).startswith('data:image/jpeg;base64,'))
        self.transport.preview.assert_not_called()
        path, = Path(self.app._libraries.get()['path']).glob('Telegram/*.jpg')
        path.write_bytes(self.image + b'replaced')
        self.assertIsNone(self.call('tg_job_preview', {'id': job['id']}))

    def test_completed_video_preview_uses_local_thumbnail_provider(self):
        video = b'\x00\x00\x00\x18ftypmp42' + b'\0' * 128
        self.item.update(kind='video', extension='.mp4', width=320, height=180, size=len(video),
                         name='fixture.mp4')
        self.transport.chunks.side_effect = lambda item, offset: iter([video[offset:]])
        scan_id, item = self.scan()
        job = self.complete(scan_id, item, self.library)
        path, = Path(self.app._libraries.get()['path']).glob('Telegram/*.mp4')
        with patch('local_video.services.local_library.windows_thumbnail',
                   return_value=Image.new('RGB', (32, 18), 'red')) as thumbnail:
            result = self.call('tg_job_preview', {'id': job['id']})
            self.assertTrue(result.startswith('data:image/jpeg;base64,'))
            thumbnail.assert_called_once_with(path, (960, 960))
        self.transport.preview.assert_not_called()
