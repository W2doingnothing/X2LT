from io import BytesIO
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock

from PIL import Image

from local_video.guangyapan_previews import GuangyapanPreviews, thumbnail_url


class GuangyapanPreviewTests(unittest.TestCase):
    def test_cache_is_reused_across_restart_and_cannot_cross_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = {'client_id': 'dvlp_fixture', 'client_secret': 'f' * 64}
            provider = Mock()
            provider.list.return_value = {'items': [{'id': 'root'}]}
            previews = GuangyapanPreviews(Path(temp), Event())
            previews.credentials = vault
            previews.root = Mock(return_value={'root'})
            previews.post = Mock(return_value={'fileInfo': {'fileId': '42', 'thumbnail': 'https://api.guangyapan.com/sample'}})
            jpeg = BytesIO()
            with Image.new('RGB', (128, 72)) as image:
                image.save(jpeg, format='JPEG')
            previews.cache._fetch = Mock(return_value=jpeg.getvalue())
            key = previews.register('account-one', '42')
            self.assertIsNone(previews.load(key, 'account-two', provider))
            with previews.load(key, 'account-one', provider) as image:
                self.assertEqual(image.size, (128, 72))
            previews.root.assert_called_once()
            previews.cache._fetch.assert_called_once()

            restarted = GuangyapanPreviews(Path(temp), Event())
            restarted.credentials = vault
            restarted.post = Mock(side_effect=AssertionError('cache hit must not use the API'))
            self.assertEqual(restarted.register('account-one', '42'), key)
            with restarted.load(key, None, provider) as image:
                self.assertEqual(image.size, (128, 72))
            self.assertNotEqual(restarted.register('account-two', '42'), key)

    def test_unrelated_root_and_url_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            previews = GuangyapanPreviews(Path(temp), Event())
            previews.root = Mock(return_value={'other-root'})
            provider = Mock()
            provider.list.return_value = {'items': [{'id': 'my-root'}]}
            with self.assertRaises(ValueError):
                previews.match_account({}, provider)
        for url in ('http://api.guangyapan.com/a', 'https://api.guangyapan.com.example/a',
                    'https://127.0.0.1/a', 'https://a.guangyacdn.com:8080/a'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                thumbnail_url(url)
