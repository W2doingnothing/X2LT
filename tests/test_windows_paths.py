import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_video.archive import original_path


@unittest.skipUnless(os.name=='nt','Windows extended path form')
class WindowsPathTests(unittest.TestCase):
    def test_mixed_extended_resolve_during_concurrent_parent_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            row={'output':tmp,'author':'alice','tweet_id':'123','media_id':'1','quality':'best'}
            resolve=Path.resolve
            def mixed(path,*args,**kwargs):
                result=resolve(path,*args,**kwargs)
                return Path('\\\\?\\'+str(result)) if path.suffix=='.mp4' else result
            with patch.object(Path,'resolve',mixed):
                self.assertEqual(original_path(row).name,'123_1.mp4')
