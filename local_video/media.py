# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Optional container probe; never claims full video decode verification."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def probe_video(path):
    executable = shutil.which('ffprobe')
    if not executable:
        return 'not-installed'
    try:
        result = subprocess.run([executable, '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(Path(path))], capture_output=True, text=True, encoding='utf8', errors='replace', timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode or not any(stream.get('codec_type') == 'video' for stream in json.loads(result.stdout).get('streams', [])):
            raise RuntimeError('媒体结构检查失败：未找到可识别的视频流；文件保留供检查')
    except (ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError('媒体结构检查失败或超时；文件保留供检查') from exc
    return 'ffprobe'
