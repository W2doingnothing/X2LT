from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local_video.media import probe_video


class MediaTests(unittest.TestCase):
    def test_optional_probe_rejects_file_without_video_stream(self):
        with patch('local_video.media.shutil.which', return_value='ffprobe.exe'), patch('local_video.media.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{"streams":[{"codec_type":"audio"}]}')):
            with self.assertRaisesRegex(RuntimeError, '视频流'):
                probe_video(Path('fixture.mp4'))

    def test_optional_probe_checks_stream_and_missing_tool(self):
        with patch('local_video.media.shutil.which', return_value=None):
            self.assertEqual(probe_video(Path('fixture.mp4')), 'not-installed')
        with patch('local_video.media.shutil.which', return_value='ffprobe.exe'), patch('local_video.media.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='{"streams":[{"codec_type":"video"}]}')):
            self.assertEqual(probe_video(Path('fixture.mp4')), 'ffprobe')
