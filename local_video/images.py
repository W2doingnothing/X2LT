# SPDX-License-Identifier: GPL-2.0-only
"""Restricted raster decoding and verified image promotion."""
import os
import stat
from pathlib import Path
import warnings

from PIL import Image, ImageOps

from .security import atomic_json
from .transfer import file_hash, Paused

FORMATS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}
MAX_PIXELS = 40_000_000


def is_alias(path):
    path = Path(path)
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        return False
    return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def extension_for(row):
    kind = row.get('media_type', 'video')
    ext = row.get('extension', 'mp4')
    if kind == 'video' and ext == 'mp4' or kind == 'image' and ext in FORMATS.values():
        return ext
    raise ValueError('不支持的媒体类型或格式')


def read_image(path, expected=None, size=None):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path, formats=list(FORMATS)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('图片像素超出安全上限')
                actual = FORMATS.get(image.format)
                if not actual or expected and actual != expected:
                    raise ValueError('图片真实格式与文件类型不符')
                image.verify()
            with Image.open(path, formats=list(FORMATS)) as image:
                if size:
                    image.draft('RGB', size)
                image.load()
                result = ImageOps.exif_transpose(image).convert('RGB')
                if size:
                    result.thumbnail(size, Image.Resampling.LANCZOS)
                return result
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('图片无法安全解码或内容无效') from exc


def check_image(path, expected):
    image = read_image(path, expected, (1, 1))
    image.close()


def download_image(downloader, url, destination, cancel, progress, force=False):
    destination = Path(destination)
    if is_alias(destination):
        raise ValueError('目标不能为符号链接')
    # Separate staging keeps a valid existing image intact on forced failure.
    staging = destination.with_suffix(destination.suffix + '.incoming')
    if is_alias(staging):
        raise ValueError('图片暂存不能为符号链接或目录联接')
    result = downloader.download(url, staging, cancel, progress, force=force)
    try:
        check_image(staging, destination.suffix.lower()[1:])
    except ValueError:
        # An invalid completed staging response must be fetched again on retry.
        staging.with_suffix(staging.suffix + '.verified.json').unlink(missing_ok=True)
        raise
    if cancel.is_set():
        raise Paused('已暂停，图片暂存保留')
    if staging.stat().st_size != result['bytes'] or file_hash(staging, cancel) != result['sha256']:
        raise ValueError('图片暂存校验失败')
    os.replace(staging, destination)
    atomic_json(destination.with_suffix(destination.suffix + '.verified.json'), result)
    staging.with_suffix(staging.suffix + '.verified.json').unlink(missing_ok=True)
    return result
