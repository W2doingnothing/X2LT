import unittest
import tempfile
import json
from pathlib import Path
from local_video.emoji import validate_emoji
from local_video.libraries import LibraryManager

class EmojiTests(unittest.TestCase):
    def test_single_emoji_combinations(self):
        for s in ['📁','🖼️','👩🏽‍💻','🇨🇳','1️⃣','👨‍👩‍👧‍👦']:
            self.assertEqual(validate_emoji(' '+s+' '),s)
        for s in ['', 'abc','📁📚','a📁','\n','🇨']:
            with self.assertRaises(ValueError):validate_emoji(s)

    def test_legacy_icon_and_image_cover_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'media';folder.mkdir();config=root/'libraries.json'
            config.write_text(json.dumps({'libraries':[{'id':'one','name':'库','path':str(folder),'quota_bytes':0,'cover':{'kind':'image','value':'managed-id'}}],'default_id':'one'}))
            libs=LibraryManager(config)
            self.assertEqual(libs.get()['emoji'],'📁')
            libs.appearance('one',emoji='🎬')
            self.assertEqual(libs.get()['cover']['value'],'managed-id')
            self.assertEqual(LibraryManager(config).get()['emoji'],'🎬')
