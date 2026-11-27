from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from local_video.local_collection import LocalCollection
from local_video.local_groups import LocalGroups
from local_video.model import MediaModel
from local_video.services.telegram_archive import TelegramArchive
from local_video.store import Store


class TelegramArchiveTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = Store(self.root/'queue.sqlite')
        self.addCleanup(self.store.close)
        self.collection = LocalCollection(self.store)
        self.groups = LocalGroups(self.collection)
        self.libraries = Mock()
        self.libraries.get.return_value = {'id': 'library', 'path': str(self.root)}
        self.native = Mock()

    def archive(self):
        return TelegramArchive(self.store, self.libraries, self.collection, self.groups, self.native)

    def register(self, archive, message, media, data, **extra):
        path = self.root/f'{message}-{media}.png'
        path.write_bytes(data)
        job = dict(library_id='library', extension='.png', peer_id='-100001',
                   message_id=str(message), peer_title='目标频道', group_id='',
                   title='媒体', body=f'消息 {message}', media_id=str(media),
                   kind='image', width=0, height=0, size=len(data),
                   url=f'https://t.me/channel/{message}')
        job.update(extra)
        return archive.register(job, path, sha256(data).hexdigest())

    def page(self, query=''):
        return self.groups.page('library', query, [], False, 0, 50)['items']

    def test_model_v1_database_adds_telegram_tables_after_upgrade(self):
        self.store.db.executescript('DROP TABLE telegram_saved; DROP TABLE telegram_messages;')
        self.assertTrue(self.store.model.migration()['done'])
        self.store.model = MediaModel(self.store.db, self.store.lock)
        self.register(self.archive(), 2, 3, b'telegram')
        self.assertEqual(len(self.page()), 1)

    def test_message_identity_includes_shared_entry_and_groups_media_per_message(self):
        archive = self.archive()
        shared = self.register(archive, 4, 11, b'shared')
        other = self.register(archive, 4, 12, b'second')
        self.assertEqual(self.register(archive, 5, 13, b'shared', body='单独正文'), shared)
        items = {row['body']: row for row in self.page()}
        self.assertEqual(set(items), {'消息 4', '单独正文'})
        self.assertEqual({item['id'] for item in items['消息 4']['telegram_items']}, {shared, other})
        self.assertEqual([item['id'] for item in items['单独正文']['telegram_items']], [shared])
        self.assertEqual(len(self.page('单独正文')), 1)
        self.assertEqual(self.collection.stats()[0]['message_count'], 2)
        self.assertEqual(self.collection.stats()[0]['media_count'], 2)
        archive.open_source('library', shared, items['单独正文']['telegram']['content_id'])
        self.native.open_url.assert_called_once_with('https://t.me/channel/5')

    def test_legacy_album_migration_preserves_manual_changes_and_receipt_ids(self):
        archive = self.archive()
        first = self.register(archive, 4, 11, b'first')
        second = self.register(archive, 5, 12, b'second', body='')
        third = self.register(archive, 6, 13, b'third')
        fourth = self.register(archive, 7, 14, b'fourth', body='')
        db, model = self.store.db, self.store.model
        saved = [tuple(row) for row in db.execute('SELECT entry,variant,model_location FROM telegram_saved')]
        for album, members, body, edited in [('42', [first,second], '消息 4', False),
                                               ('43', [third,fourth], '消息 6', True)]:
            parent = model.content('telegram', '-100001', f'album:{album}', '目标频道', body)
            with db:
                db.execute('''UPDATE model_media SET content_id=? WHERE id IN (
                    SELECT v.media_id FROM model_variants v JOIN telegram_saved t ON t.variant=v.id
                    WHERE t.entry IN (?,?))''', (parent, *members))
                db.execute('UPDATE local_entries SET model_content=? WHERE id IN (?,?)', (parent, *members))
                group = db.execute('INSERT INTO local_groups(library,title,cover,body) VALUES(?,?,?,?)',
                                   ('library', '手动标题' if edited else body, members[0], body)).lastrowid
                db.execute('INSERT INTO telegram_albums VALUES(?,?,?)', ('library', parent, group))
                self.groups.save_members('library', group, members)
        self.collection.set_tags(first, ['留存'])

        self.archive()

        backup = self.root/'queue.sqlite.pre-telegram-message-archive-v1.bak'
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(backup)) as copy:
            self.assertEqual(copy.execute('SELECT COUNT(*) FROM telegram_albums').fetchone()[0], 2)
        self.assertEqual([tuple(row) for row in db.execute('SELECT entry,variant,model_location FROM telegram_saved')], saved)
        self.assertEqual({row['title'] for row in self.page() if 'group' in row}, {'手动标题'})
        self.assertEqual(next(row for row in self.page() if row['id'] == first)['tags'], ['留存'])
        self.assertEqual({row[0] for row in db.execute('''SELECT c.native_id FROM model_media m
            JOIN model_contents c ON c.id=m.content_id''')}, {'4','5','6','7'})

    def test_replayed_legacy_job_keeps_sent_date_and_forward_metadata(self):
        archive = self.archive()
        self.register(archive, 5, 12, b'media', sent_at=1234, forward_id='-100002',
                      forward_title='来源', forward_url='https://t.me/example/2', avatar_key='internal-key')
        self.register(archive, 5, 12, b'media')
        message = self.page()[0]['telegram']
        self.assertEqual(message['sent_at'], 1234)
        self.assertEqual(message['forward'], dict(id='-100002', title='来源',
                                                 url='https://t.me/example/2', avatar_key='internal-key'))
        self.register(archive, 5, 12, b'media', forward_id='-100003', forward_title='另一来源')
        self.assertEqual(self.page()[0]['telegram']['forward'],
                         dict(id='-100003', title='另一来源', url='', avatar_key=''))
