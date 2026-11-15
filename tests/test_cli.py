import gc
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_video.__main__ import main


class CLITests(unittest.TestCase):
    def test_gui_smoke_exercises_pillow_tk_image_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('sys.argv',['local_video','--smoke-gui',tmp]):
                self.assertEqual(main(),0)
            report=json.loads((Path(tmp)/'smoke-result.json').read_text())
            self.assertTrue(report['pillow_tk'])
        gc.collect()
