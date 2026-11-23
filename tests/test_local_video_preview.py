import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from local_video.desktop_service import DesktopService
from local_video.transfer import file_hash


class LocalVideoPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.service = DesktopService(self.root / 'data', self.root / 'home')
        self.addCleanup(self.service._close)
        self.library = self.service._libraries.get()['id']
        self.source = self.root / 'fixture.mp4'
        self.source.write_bytes(b'synthetic local video')
        local = self.service._local
        self.identifier = local.collection.add(
            {'kind':'video', 'title':'fixture', 'extension':'mp4', 'bytes':self.source.stat().st_size, 'pages':[]},
            file_hash(self.source, self.service._runtime.cancel),
            self.source,self.root,self.library,False,
        )

    def preview(self):
        response = self.service.call('local_media_preview',{'id':self.identifier})
        self.assertTrue(response['ok'],response)
        return response['data']

    def test_portrait_is_cached_without_changing_video(self):
        with patch('local_video.services.local_library.windows_thumbnail',
                   side_effect=lambda *_: Image.new('RGB',(30,72),'blue')) as extract:
            preview = self.preview()
            self.assertTrue(preview.startswith('data:image/jpeg;base64,'))
            with Image.open(BytesIO(base64.b64decode(preview.split(',')[1]))) as image:
                self.assertEqual(image.size,(30,72))
            self.assertIsNotNone(self.preview())
            self.assertEqual(extract.call_count,1)
            self.service._local.clear_previews()
            self.assertIsNotNone(self.preview())
            self.assertEqual(extract.call_count,2)
        self.assertEqual(self.source.read_bytes(),b'synthetic local video')

    def test_unavailable_video_keeps_placeholder(self):
        with patch('local_video.services.local_library.windows_thumbnail',return_value=None):
            self.assertIsNone(self.preview())
        with patch('local_video.services.local_library.windows_thumbnail') as extract:
            self.source.write_bytes(b'changed and no longer the verified original')
            self.assertIsNone(self.preview())
            extract.assert_not_called()

    def test_moved_video_during_decode_is_not_cached(self):
        def decode(*_):
            self.source.rename(self.source.with_suffix('.moved'))
            return Image.new('RGB',(16,9),'red')

        with patch('local_video.services.local_library.windows_thumbnail',side_effect=decode):
            self.assertIsNone(self.preview())
        self.assertEqual(list((self.root/'data'/'local-thumbnails').glob('*.jpg')),[])
