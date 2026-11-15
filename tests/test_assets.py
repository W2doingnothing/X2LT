from io import BytesIO
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from local_video.assets import LibraryAssets,validate_avatar_url
from local_video.previews import PreviewCache


class AssetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=PreviewCache(self.root/'previews')
        self.assets=LibraryAssets(self.root/'covers',self.cache)

    def test_cover_copied_raster_and_only_managed_id_readable(self):
        original=self.root/'source.png';Image.new('RGB',(1800,1000),'red').save(original)
        cover=self.assets.import_cover(original);original.unlink()
        self.assertEqual(cover['kind'],'image')
        self.assertTrue(self.assets.cover(cover).startswith('data:image/jpeg;base64,'))
        self.assertRaises(ValueError,self.assets.cover,{'kind':'image','value':'../../secret'})
        self.assertRaises(ValueError,self.assets.import_cover,self.root/'bad.svg')

    def test_avatar_official_profile_only_and_no_raw_url_returned(self):
        url='https://pbs.twimg.com/profile_images/123/name_normal.jpg'
        validate_avatar_url(url)
        for bad in ('https://evil.test/a.jpg','http://pbs.twimg.com/profile_images/a.jpg','https://pbs.twimg.com/media/a.jpg','https://user:password@pbs.twimg.com/profile_images/a.jpg'):
            self.assertRaises(ValueError,validate_avatar_url,bad)
        buf=BytesIO();Image.new('RGB',(40,40),'green').save(buf,format='PNG')
        with patch.object(self.assets.fetcher,'_fetch',return_value=buf.getvalue()) as fetch:
            image=self.assets.avatar(url,'',threading.Event())
            self.assertTrue(image.startswith('data:image/jpeg;base64,'))
            self.assets.avatar(url,'',threading.Event())
            self.assertEqual(fetch.call_count,1)
        self.cache.trim(clear=True)
        self.assertFalse(list(self.cache.root.glob('*.jpg')))
