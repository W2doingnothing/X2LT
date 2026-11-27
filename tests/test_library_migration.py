import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_video.archive import comparable_path, original_path, save_receipt, valid_receipt
from local_video.libraries import LibraryManager
from local_video.local_collection import LocalCollection
from local_video.services.native import NativeHost
from local_video.services.runtime import WorkerRuntime
from local_video.services.telegram_queue import TelegramQueue
from local_video.store import Store


class LibraryMigrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = comparable_path(Path(temp.name).resolve(strict=True))
        self.source, self.target, self.data = (root / name for name in ('source', 'target', 'data'))
        for folder in (self.source, self.target, self.data):
            folder.mkdir()
        self.manager = LibraryManager(self.data / 'libraries.json')
        self.library = self.manager.add('主库', self.source)
        self.id = self.library['id']
        self.store = Store(self.data / 'queue.sqlite3')
        self.addCleanup(self.store.close)
        self.collection = LocalCollection(self.store)
        self.native = NativeHost()
        self.native.approved_paths.add(str(self.target))
        self.runtime = WorkerRuntime()
        self.external = root / 'outside.mp4'
        self.external.write_bytes(b'outside')

    def service(self):
        from local_video.services.library_migration import LibraryMigrationService
        return LibraryMigrationService(self.manager, self.store, self.native, self.runtime, self.data)

    def request(self):
        return {'id': self.id, 'path': str(self.target)}

    def test_migrates_download_receipt_partial_and_local_paths(self):
        with self.store.lock, self.store.db:
            self.store.db.execute("""INSERT INTO jobs(id,tweet_id,media_id,author,url,source,output,quality,
                bytes,sha256,state,verification,local_path) VALUES(1,'1','2','alice','https://example.com/x',
                '',?,'best',?,?, 'completed','sha256-only',?)""",
                                  (str(self.source), 7, hashlib.sha256(b'outside').hexdigest(), str(self.external)))
        row = self.store.get_many([1])[0]
        original = original_path(row)
        original.parent.mkdir(parents=True)
        save_receipt(row)
        part = original.with_suffix('.mp4.parts') / '0.part'
        part.parent.mkdir()
        part.write_bytes(b'partial')
        local = self.source / 'local.mp4'
        local.write_bytes(b'local')
        empty = self.source / 'empty'
        empty.mkdir()
        def add(path, root):
            return self.collection.add(dict(kind='video', title=path.name, extension='mp4', bytes=path.stat().st_size,
                                            pages=[]), hashlib.sha256(path.read_bytes()).hexdigest(), path, root,
                                       self.id, False)
        local_id, external_id = add(local, self.source), add(self.external, self.external.parent)
        service = self.service()
        self.assertEqual(service.plan(self.request())['file_count'], 3)
        service.start(self.request())
        self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'done')
        self.assertEqual(self.manager.get(self.id)['path'], str(self.target))
        self.assertEqual(self.manager.default_id, self.id)
        self.assertFalse(self.manager.get(self.id).get('previous_paths'))
        self.assertEqual(list(self.source.iterdir()), [])
        self.assertTrue((self.target / 'empty').is_dir())
        self.assertEqual((self.target / part.relative_to(self.source)).read_bytes(), b'partial')
        row = self.store.get_many([1])[0]
        self.assertEqual(row['output'], str(self.target))
        self.assertEqual(valid_receipt(row)['local_path'], str(self.external))
        self.assertEqual(self.collection.get_location(local_id)['path'], str(self.target / local.name))
        self.assertEqual(self.collection.get_location(external_id)['path'], str(self.external))

    def test_refuses_collision_without_overwriting(self):
        (self.source / 'media.mp4').write_bytes(b'source')
        (self.target / 'media.mp4').write_bytes(b'target')
        service = self.service()
        with self.assertRaises(ValueError):
            service.plan(self.request())
        self.assertEqual((self.target / 'media.mp4').read_bytes(), b'target')
        self.assertFalse(service.pending())

    def test_refuses_insufficient_space_before_creating_migration(self):
        (self.source / 'a').write_bytes(b'first')
        service = self.service()
        with patch('local_video.services.library_migration.shutil.disk_usage') as usage:
            usage.return_value.free = 4
            with self.assertRaisesRegex(ValueError, '磁盘空间不足'):
                service.start(self.request())
        self.assertFalse(service.pending())
        self.assertFalse(list(self.target.iterdir()))

    def test_restarts_after_database_commit_before_config_switch(self):
        (self.source / 'a').write_bytes(b'first')
        service = self.service()
        with patch.object(service, '_publish_root', side_effect=OSError('configuration file unavailable')):
            service.start(self.request())
            self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'failed')
        self.assertFalse(service.status({'id': self.id})['can_abandon'])
        self.assertEqual(self.manager.get(self.id)['path'], str(self.source))
        recovered = self.service()
        self.assertEqual(self.manager.get(self.id)['path'], str(self.target))
        self.assertTrue((self.source / 'a').is_file())
        recovered.retry({'id': self.id})
        self.runtime.worker.join(10)
        self.assertEqual(recovered.status({'id': self.id})['status'], 'done')
        self.assertEqual((self.target / 'a').read_bytes(), b'first')

    def test_resumes_after_copy_failure_without_cleaning_source_early(self):
        (self.source / 'a').write_bytes(b'first')
        (self.source / 'b').write_bytes(b'second')
        service = self.service()
        publish = service._publish
        def interrupt(temporary, target):
            if target.name == 'b':
                raise OSError('synthetic interruption')
            publish(temporary, target)
        with patch.object(service, '_publish', side_effect=interrupt):
            service.start(self.request())
            self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'failed')
        self.assertEqual((self.target / 'a').read_bytes(), b'first')
        self.assertEqual((self.source / 'a').read_bytes(), b'first')
        self.assertEqual(self.manager.get(self.id)['path'], str(self.source))
        resumed = self.service()
        resumed.retry({'id': self.id})
        self.runtime.worker.join(10)
        self.assertEqual(resumed.status({'id': self.id})['status'], 'done')
        self.assertEqual((self.target / 'b').read_bytes(), b'second')
        self.assertEqual(list(self.source.iterdir()), [])

    def test_rebuilds_missing_published_file_before_rebinding(self):
        (self.source / 'a').write_bytes(b'first')
        (self.source / 'b').write_bytes(b'second')
        service = self.service()
        publish = service._publish
        def interrupt(temporary, target):
            if target.name == 'b':
                raise OSError('synthetic interruption')
            publish(temporary, target)
        with patch.object(service, '_publish', side_effect=interrupt):
            service.start(self.request())
            self.runtime.worker.join(10)
        (self.target / 'a').unlink()
        service.retry({'id': self.id})
        self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'done')
        self.assertEqual((self.target / 'a').read_bytes(), b'first')

    def test_telegram_checkpoint_follows_new_library_root(self):
        queue = TelegramQueue(self.store, self.manager, None, None, None)
        self.addCleanup(queue.close)
        before = self.source / 'Telegram' / 'test-media.mp4.part'
        before.parent.mkdir()
        before.write_bytes(b'partial')
        checksum = hashlib.sha256(b'partial').hexdigest()
        with self.store.lock, self.store.db:
            self.store.db.execute('''INSERT INTO telegram_downloads(id,library_id,peer_id,message_id,media_id,
                payload,state,root,filename,total,received,partial_digest)
                VALUES('test',?,'1','2','3','{}','paused',?,'test-media.mp4',100,7,?)''',
                                  (self.id, str(self.source), checksum))
        service = self.service()
        service.start(self.request())
        self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'done')
        task = dict(self.store.db.execute("SELECT * FROM telegram_downloads WHERE id='test'").fetchone())
        self.assertEqual(task['state'], 'paused')
        self.assertEqual(task['received'], 7)
        self.assertEqual(task['partial_digest'], checksum)
        self.assertEqual(queue._paths(task)[2].read_bytes(), b'partial')

    def test_target_corruption_during_cleanup_keeps_source(self):
        (self.source / 'a').write_bytes(b'first')
        service = self.service()
        clean = service._clean
        def corrupt(move, source, target):
            (target / 'a').write_bytes(b'wrong')
            clean(move, source, target)
        with patch.object(service, '_clean', side_effect=corrupt):
            service.start(self.request())
            self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'failed')
        self.assertFalse(service.status({'id': self.id})['can_abandon'])
        self.assertEqual((self.source / 'a').read_bytes(), b'first')
        self.assertEqual(self.manager.get(self.id)['path'], str(self.target))
        (self.target / 'a').write_bytes(b'first')
        service.retry({'id': self.id})
        self.runtime.worker.join(10)
        self.assertEqual(service.status({'id': self.id})['status'], 'done')
        self.assertFalse((self.source / 'a').exists())

    def test_abandon_after_failed_copy_keeps_both_directories(self):
        (self.source / 'a').write_bytes(b'first')
        (self.source / 'b').write_bytes(b'second')
        service = self.service()
        publish = service._publish
        def interrupt(temporary, target):
            if target.name == 'b':
                raise OSError('synthetic interruption')
            publish(temporary, target)
        with patch.object(service, '_publish', side_effect=interrupt):
            service.start(self.request())
            self.runtime.worker.join(10)
        self.assertTrue(service.status({'id': self.id})['can_abandon'])
        self.assertEqual(service.abandon({'id': self.id})['status'], 'abandoned')
        self.assertFalse(service.pending())
        self.assertEqual(self.manager.get(self.id)['path'], str(self.source))
        self.assertEqual((self.source / 'a').read_bytes(), b'first')
        self.assertEqual((self.target / 'a').read_bytes(), b'first')
