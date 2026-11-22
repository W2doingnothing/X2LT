from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image

from local_video.local_import_files import comic_pages, copy_into, fingerprint, read_page, scan
from local_video.transfer import Paused


class LocalImportFileTests(unittest.TestCase):
    def setUp(self):
        self.storage = TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name).resolve()

    def image(self, path, size=(12, 8)):
        path.parent.mkdir(exist_ok=True, parents=True)
        with Image.new('RGB', size, 'red') as picture:
            picture.save(path)

    def test_folder_comic_pages_natural_sort_read_and_verified_copy(self):
        comic = self.root / '绘本'
        for name in ('10.png', '2.png', '1.png'):
            self.image(comic / name)
        self.image(comic / 'nested' / '3.png')
        (comic / 'readme.txt').write_text('unrelated', encoding='utf-8')
        row, = scan([comic], 'comic')
        self.assertEqual(row['title'], '绘本')
        self.assertEqual([page['id'] for page in row['pages']], ['1.png', '2.png', '10.png'])
        with read_page(comic, '2.png') as image:
            self.assertEqual(image.size, (12, 8))
        target = self.root / '复制'
        digest = copy_into(comic, 'comic', row['pages'], target)
        self.assertEqual(digest, fingerprint(comic, 'comic', row['pages']))
        self.assertEqual(digest, fingerprint(target, 'comic', row['pages']))
        self.assertFalse((target / 'nested').exists())
        self.assertFalse((target / 'readme.txt').exists())
        with self.assertRaises(FileExistsError):
            copy_into(comic, 'comic', row['pages'], target)
        with self.assertRaises(ValueError):
            read_page(comic, '../other.png')

    def test_batch_scan_keeps_books_separate_and_requires_explicit_recursion(self):
        self.image(self.root / 'book-a' / '01.png')
        self.image(self.root / 'book-b' / 'chapter' / '01.png')
        (self.root / 'loose.mp4').write_bytes(b'regular-video-fixture')
        (self.root / 'other.txt').write_text('skip')
        self.assertEqual([row['title'] for row in scan([self.root], 'comic', batch=True)], ['book-a'])
        self.assertEqual([row['title'] for row in scan([self.root], 'comic', batch=True, recursive=True)],
                         ['book-a', 'book-b'])
        row, = scan([self.root], 'media')
        self.assertEqual((row['kind'], row['extension'], row['title']), ('video', 'mp4', 'loose'))
        self.assertEqual(len(list(scan([self.root], 'media', recursive=True))), 3)

    def test_archive_is_read_without_extraction_and_cannot_be_traversed(self):
        picture = self.root / 'picture.png'
        self.image(picture)
        comic = self.root / 'graphic.cbz'
        with ZipFile(comic, 'w', ZIP_DEFLATED) as archive:
            archive.write(picture, 'pages/10.png')
            archive.write(picture, 'pages/2.png')
        row, = scan([comic], 'comic')
        self.assertEqual([p['id'] for p in row['pages']], ['pages/2.png', 'pages/10.png'])
        with read_page(comic, 'pages/2.png') as image:
            self.assertEqual(image.size, (12, 8))
        target = self.root / 'copy.cbz'
        self.assertEqual(copy_into(comic, 'comic', row['pages'], target), fingerprint(target, 'comic', row['pages']))
        self.assertFalse((self.root / 'pages').exists())
        invalid = self.root / 'invalid.cbz'
        for unsafe in ('../escaped.png', '/absolute.png', 'D:/escape.png', 'dir\\file.png', 'dir/CON.png'):
            with self.subTest(unsafe=unsafe):
                with ZipFile(invalid, 'w') as archive:
                    archive.write(picture, 'valid.png')
                    archive.writestr(unsafe.replace('\\', '/'), picture.read_bytes())
                if '\\' in unsafe:
                    invalid.write_bytes(invalid.read_bytes().replace(b'dir/file.png', b'dir\\file.png'))
                with self.assertRaises(ValueError):
                    list(scan([invalid], 'comic'))
                with self.assertRaises(ValueError):
                    read_page(invalid, 'valid.png')
        with ZipFile(invalid, 'w') as archive:
            link = ZipInfo('link.png')
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            archive.writestr(link, 'picture.png')
        with self.assertRaises(ValueError):
            list(scan([invalid], 'comic'))

    def test_archive_does_not_read_a_forged_footer_in_the_comment(self):
        target = self.root / 'forged-footer.cbz'
        with ZipFile(target, 'w') as archive:
            archive.writestr('page.png', b'image fixture')
            archive.comment = struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 1, 1, 1 << 30, 0, 0) + b'x'
        with patch('local_video.local_import_files.ZipFile') as reader:
            with self.assertRaises(ValueError):
                list(scan([target], 'comic'))
            reader.assert_not_called()

    def test_interrupted_scan_and_hash_do_not_proceed(self):
        cancelled = Event()
        cancelled.set()
        target = self.root / 'video.mp4'
        target.write_bytes(b'video')
        with self.assertRaises(Paused):
            list(scan([target], 'media', cancel=cancelled))
        with self.assertRaises(Paused):
            fingerprint(target, 'video', [], cancelled)

        comic = self.root / 'cancelled.cbz'
        with ZipFile(comic, 'w') as archive:
            archive.writestr('page.png', b'image fixture')
        states = iter((False, True))
        with self.assertRaises(Paused):
            comic_pages(comic, cancel=lambda: next(states))
