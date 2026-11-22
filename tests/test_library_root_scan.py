import io
from hashlib import sha256
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from PIL import Image

from local_video.desktop_service import DesktopService


class LibraryRootScanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.folder = self.root / 'library'
        self.folder.mkdir()
        self.service = DesktopService(self.root / 'data', self.root / 'home')
        self.addCleanup(self.service._close)
        self.local = self.service._local
        self.library = self.service._libraries.add('测试库', self.folder)['id']

    def wait(self, job, stage):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            result = self.local.status({'id': job})
            if result['stage'] not in ('scanning', 'importing'):
                self.assertEqual(result['stage'], stage, result)
                return result
            time.sleep(.02)
        self.fail(f'任务等待超时: {job}')

    def png(self, path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (16, 17), color).save(path)

    def scan(self, **kwargs):
        job = self.local.scan_library({'library_id': self.library, **kwargs})['id']
        return job, self.wait(job, 'preview')

    def entries(self):
        return self.local.page({'library_id': self.library})['items']

    def confirm(self, job):
        self.local.start({'id': job})
        return self.wait(job, 'completed')

    def test_recursive_originals_relocation_file_operations_and_scan_exclusions(self):
        source = self.folder / 'sub' / 'picture.png'
        self.png(source, 'red')
        self.png(self.folder / 'X2LT本地' / 'old-copy.png', 'black')
        job, status = self.scan()
        self.assertEqual(status['total'], 1)
        self.assertEqual(self.entries(), [])
        self.confirm(job)
        entry = self.entries()[0]
        self.assertTrue(entry['locations'][0]['manageable'])
        self.assertTrue(entry['locations'][0]['recyclable'])
        identifier = entry['id']
        location = entry['locations'][0]['id']
        self.local.tags({'id': identifier, 'tags': ['保留']})

        renamed = source.with_name('moved.png')
        source.rename(renamed)
        job, status = self.scan()
        self.assertEqual(status['already'], 1)
        self.assertEqual(status['missing'], 1)
        self.confirm(job)
        entry = self.entries()[0]
        self.assertEqual(entry['tags'], ['保留'])
        self.assertEqual(entry['locations'][0]['id'], location)
        self.assertEqual(entry['locations'][0]['label'], 'moved.png')

        request = dict(id=identifier, library_id=self.library, location_id=location)
        with self.assertRaises(ValueError):
            self.local.manage_file({**request, 'name': '..\\out.png'})
        with self.assertRaises(ValueError):
            self.local.manage_file({**request, 'name': 'moved.jpg'})
        self.local.manage_file({**request, 'name': 'renamed.png'})
        dest = self.folder / 'dest'
        dest.mkdir()
        self.service._native.choose_folder = Mock(return_value=str(dest))
        self.local.manage_file({**request, 'move': True})
        moved = dest / 'renamed.png'
        self.assertTrue(moved.is_file())
        self.local.remove(request)
        self.assertTrue(moved.is_file())
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.local.scan_exclusions({'library_id': self.library})[0]['id'], identifier)
        self.assertEqual(self.scan()[1]['excluded'], 1)
        self.local.scan_restore({'id': identifier, 'library_id': self.library})
        self.assertEqual(self.entries(), [])
        self.confirm(self.scan()[0])
        self.assertEqual(self.entries()[0]['id'], identifier)

        with patch('local_video.services.local_library.recycle_file', side_effect=lambda path: path.unlink()) as recycle:
            self.local.remove({**request, 'recycle': True})
            recycle.assert_called_once_with(moved)
        self.assertEqual(self.entries(), [])

    def test_comic_scan_groups_chapters_and_indexes_nested_archives_and_optional_loose(self):
        self.service._libraries.update(self.library, scan_mode='comic')
        book = self.folder / '分类' / '书'
        self.png(book / '章 10' / '1.png', 'red')
        self.png(book / '章 2' / '1.png', 'blue')
        self.png(self.folder / '分类' / '另一本' / '1.png', 'green')
        self.png(self.folder / 'loose.png', 'purple')
        image = io.BytesIO()
        Image.new('RGB', (2, 3), 'yellow').save(image, 'PNG')
        with ZipFile(book / 'extra.cbz', 'w') as archive:
            archive.writestr('1.png', image.getvalue())
        job, status = self.scan()
        self.assertEqual({i['title'] for i in status['items']}, {'书', '另一本', 'extra'})
        self.confirm(job)
        comic = next(item for item in self.entries() if item['title'] == '书')
        book_id, book_location = comic['id'], comic['locations'][0]['id']
        self.assertFalse(comic['locations'][0]['recyclable'])
        pages = self.local.comic_state(self.local.collection.entry(book_id))['pages']
        self.assertEqual([page['id'] for page in pages], ['章 2/1.png', '章 10/1.png'])
        self.local.manage_file(dict(id=book_id, library_id=self.library, location_id=book_location, name='新书'))
        self.assertTrue((self.folder / '分类' / '新书' / 'extra.cbz').is_file())
        self.assertTrue(next(item for item in self.entries() if item['title'] == 'extra')['locations'][0]['manageable'])
        loose_job, preview = self.scan(include_loose=True)
        self.assertEqual({i['title'] for i in preview['items']}, {'新书', '另一本', 'extra', 'library'})
        self.confirm(loose_job)
        loose = next(item for item in self.entries() if item['title'] == 'library')
        self.assertFalse(loose['locations'][0]['manageable'])

    def test_library_scan_skips_platform_location_from_another_output(self):
        protected = self.folder / 'transferred' / 'saved.png'
        self.png(protected, 'green')
        model = self.local.collection.store.model
        media = model.media(model.content('x', '', '123'), '456', 'image')
        variant = model.variant(media, 'original', 'png')
        data = protected.read_bytes()
        model.location(variant,self.root/'previous-library',protected,bytes=len(data),sha256=sha256(data).hexdigest())
        job, result = self.scan()
        self.assertEqual(result['excluded'], 1)
        self.assertEqual(result['items'], [])
        self.service._libraries.update(self.library,scan_mode='comic')
        with self.assertRaisesRegex(ValueError,'模式已更改'):
            self.local.start({'id':job})

    def test_media_mode_does_not_allow_recycling_or_moving_a_comic_page(self):
        self.service._libraries.update(self.library, scan_mode='comic')
        page = self.folder / 'book' / 'chapter 1' / '1.png'
        self.png(page, 'purple')
        self.confirm(self.scan()[0])
        comic = self.entries()[0]
        self.local.link({'library_id': self.service._libraries.default_id, 'id': comic['id'], 'linked': True})
        self.local.remove({'library_id': self.library, 'id': comic['id']})

        self.service._libraries.update(self.library, scan_mode='media')
        self.confirm(self.scan()[0])
        image = self.entries()[0]
        request = dict(id=image['id'],library_id=self.library,location_id=image['locations'][0]['id'])
        with self.assertRaisesRegex(ValueError,'漫画'):
            self.local.manage_file({**request, 'name': 'other.png'})
        with self.assertRaisesRegex(ValueError,'漫画'):
            self.local.remove({**request, 'recycle': True})
        self.assertTrue(page.is_file())
        self.assertTrue(self.local.comic_page({'id':comic['id'],'page_id':'chapter 1/1.png'}).startswith('data:'))
