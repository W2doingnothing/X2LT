from hashlib import sha256
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from local_video.archive import manifest, original_path, valid_receipt
from local_video.discovery import Video
from local_video.flatten import flatten_completed
from local_video.libraries import LibraryManager
from local_video.local_collection import LocalCollection
from local_video.local_groups import LocalGroups
from local_video.runner import Runner
from local_video.services.telegram_archive import TelegramArchive
from local_video.services.telegram_queue import TelegramQueue
from local_video.store import Store


class AutoFlattenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.media = self.root / 'media'
        self.media.mkdir()
        self.libraries = LibraryManager(self.root / 'libraries.json')
        self.library = self.libraries.add('媒体库', self.media)
        self.store = Store(self.root / 'jobs.db')
        self.addCleanup(self.store.close)

    def test_library_default_off_and_verified_move_preserves_original_receipt(self):
        from local_video.auto_flatten import AutoFlatten

        self.assertFalse(self.library['auto_flatten'])
        video = Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4',
                      'https://x.com/alice/status/123')
        identifier = self.store.enqueue(video, self.media, 'best')
        path = original_path(self.store.get_many([identifier])[0])
        path.parent.mkdir(parents=True)
        path.write_bytes(b'complete')
        digest = sha256(b'complete').hexdigest()
        self.store.set_state(identifier, 'completed', bytes=8, sha256=digest, verification='length+sha256')
        layout = AutoFlatten(self.store)
        cancel = threading.Event()
        self.assertEqual(layout.apply(self.library, True, cancel, lambda event: None)['moved'], 1)
        row = self.store.get_many([identifier])[0]
        concentrated = Path(row['local_path'])
        self.assertEqual(concentrated.parent, self.media / '集中媒体')
        self.assertEqual(concentrated.read_bytes(), b'complete')
        self.assertFalse(path.exists())
        self.assertTrue(manifest(path).is_file())
        self.assertEqual(valid_receipt(row)['local_path'], str(concentrated))
        self.assertEqual(layout.apply(self.library, False, cancel, lambda event: None)['moved'], 1)
        self.assertEqual(path.read_bytes(), b'complete')
        self.assertFalse(concentrated.exists())
        self.assertEqual(valid_receipt(self.store.get_many([identifier])[0])['local_path'], str(path))

    def test_restart_recovers_publication_before_catalog_update(self):
        from local_video.auto_flatten import AutoFlatten

        identifier = self.store.enqueue(
            Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123'),
            self.media, 'best',
        )
        path = original_path(self.store.get_many([identifier])[0])
        path.parent.mkdir(parents=True)
        path.write_bytes(b'complete')
        self.store.set_state(identifier, 'completed', bytes=8, sha256=sha256(b'complete').hexdigest(),
                             verification='length+sha256')
        cancel = threading.Event()
        with patch.object(AutoFlatten, '_update_location', side_effect=OSError('database busy')):
            result = AutoFlatten(self.store).apply(self.library, True, cancel, lambda event: None)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(len(list((self.media / '集中媒体').iterdir())), 1)
        self.assertFalse(path.exists())
        result = AutoFlatten(self.store).apply(self.library, True, cancel, lambda event: None)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(Path(self.store.get_many([identifier])[0]['local_path']).read_bytes(), b'complete')

    def test_legacy_x_locations_inside_library_join_auto_layout(self):
        from local_video.auto_flatten import AutoFlatten

        identifier = self.store.enqueue(
            Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123'),
            self.media, 'best',
        )
        row = self.store.get_many([identifier])[0]
        original = original_path(row)
        original.parent.mkdir(parents=True)
        original.write_bytes(b'complete')
        self.store.set_state(identifier, 'completed', bytes=8, sha256=sha256(b'complete').hexdigest(),
                             verification='length+sha256')
        legacy = self.media / '集中视频'
        cancel = threading.Event()
        emit = lambda event: None
        self.assertEqual(flatten_completed(self.store, self.media, legacy, cancel, emit)['moved'], 1)
        old_location = Path(self.store.get_many([identifier])[0]['local_path'])
        old_sidecar = old_location.with_suffix('.json')
        old_sidecar.write_text('{}')
        layout = AutoFlatten(self.store)
        self.assertEqual(layout.apply(self.library, True, cancel, emit)['moved'], 1)
        self.assertFalse(old_location.exists())
        self.assertTrue(old_sidecar.is_file())
        self.assertEqual(layout.apply(self.library, False, cancel, emit)['moved'], 1)
        self.assertEqual(original.read_bytes(), b'complete')

    def test_new_x_download_stages_at_original_and_publishes_to_concentration(self):
        self.libraries.save_layout(self.library['id'], True, False)
        identifier = self.store.enqueue(
            Video('123', '1', 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/123'),
            self.media, 'best',
        )
        path = original_path(self.store.get_many([identifier])[0])

        def transfer(url, destination, cancel, progress):
            self.assertEqual(destination, path)
            path.parent.mkdir(parents=True)
            path.write_bytes(b'complete')
            return dict(bytes=8, sha256=sha256(b'complete').hexdigest(), verification='length+sha256')

        runner = Runner(self.store, {'output': str(self.media), '_storage_guard': self.libraries}, {}, lambda event: None)
        with patch('local_video.runner.Downloader.download', side_effect=transfer), patch('local_video.runner.probe_video'):
            runner.run()
        row = self.store.get_many([identifier])[0]
        self.assertEqual(row['state'], 'completed')
        self.assertFalse(path.exists())
        target = Path(row['local_path'])
        self.assertEqual(target.parent, self.media / '集中媒体')
        self.assertEqual(valid_receipt(row)['local_path'], str(target))
        self.assertEqual(target.read_bytes(), b'complete')

    def test_telegram_download_and_rollback_preserve_location_identity_and_metadata(self):
        from local_video.auto_flatten import AutoFlatten

        self.library = self.libraries.save_layout(self.library['id'], True, False)
        collection = LocalCollection(self.store)
        archive = TelegramArchive(self.store, self.libraries, collection, LocalGroups(collection), Mock())
        transport = Mock()
        transport.chunks.return_value = iter([b'telegram'])
        queue = TelegramQueue(self.store, self.libraries, transport, archive.register, lambda: None)
        self.addCleanup(queue.close)
        item = dict(peer_id='-10001', peer_title='频道', message_id='7', media_id='video-1',
                    group_id='', title='示例', body='原来正文', url='https://t.me/channel/7',
                    kind='video', extension='.mp4', size=8, width=0, height=0, name='example.mp4')
        self.assertEqual(queue.add([item], self.library['id']), 1)
        deadline = time.monotonic() + 4
        while queue.busy() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(queue.list()[0]['state'], 'completed')
        saved = collection.one('SELECT * FROM telegram_saved')
        location = collection.one('SELECT * FROM local_locations WHERE id=?', (saved['local_location'],))
        model = collection.one('SELECT * FROM model_locations WHERE id=?', (saved['model_location'],))
        concentrated = Path(location['path'])
        original = self.media / 'Telegram' / collection.one('SELECT filename FROM telegram_downloads')['filename']
        self.assertEqual(concentrated.parent, self.media / '集中媒体')
        self.assertEqual(model['local_path'], location['path'])
        self.assertEqual(concentrated.read_bytes(), b'telegram')
        self.assertFalse(original.exists())
        sidecar = original.with_suffix('.json')
        sidecar.write_text('{}')
        with self.store.db:
            self.store.db.execute('DELETE FROM telegram_downloads')
        layout = AutoFlatten(self.store)
        cancel = threading.Event()
        emit = lambda event: None
        result = layout.apply(self.library, False, cancel, emit)
        self.assertEqual(result['moved'], 1)
        self.assertEqual(collection.one('SELECT * FROM telegram_saved'), saved)
        self.assertEqual(collection.one('SELECT path FROM local_locations WHERE id=?', (location['id'],))['path'],
                         str(original))
        self.assertEqual(collection.one('SELECT local_path FROM model_locations WHERE id=?', (model['id'],))['local_path'],
                         str(original))
        self.assertEqual(collection.one('SELECT body FROM local_entries WHERE id=?', (saved['entry'],))['body'], '原来正文')
        self.assertEqual(original.read_bytes(), b'telegram')
        self.assertFalse(concentrated.exists())
        self.assertTrue(sidecar.is_file())
        with self.store.db:
            self.store.db.execute('DELETE FROM auto_flatten_telegram_origins')
        self.assertEqual(layout.apply(self.library, True, cancel, emit)['moved'], 1)
        self.assertEqual(layout.apply(self.library, False, cancel, emit)['moved'], 1)
        self.assertEqual(original.read_bytes(), b'telegram')
