# SPDX-License-Identifier: GPL-2.0-only
"""Bounded local media inspection and comic reads without archive extraction."""
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import unicodedata
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from .images import is_alias, read_image
from .transfer import Paused


IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}
VIDEO_EXTENSIONS = {'mp4', 'mov', 'm4v', 'mkv', 'webm', 'avi', 'wmv', 'ts', 'mpeg', 'mpg'}
COMIC_EXTENSIONS = {'zip', 'cbz'}
MAX_PAGE_BYTES = 64 * 1024**2
MAX_COMIC_BYTES = 16 * 1024**3
MAX_CENTRAL_BYTES = 16 * 1024**2
MAX_COMIC_ENTRIES = 20_000
MAX_SCAN_ENTRIES = 100_000
MAX_RATIO = 1_000
CHUNK_BYTES = 1024**2

_WINDOWS_RESERVED = re.compile(r'(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)$')
_UNSAFE_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f\x7f]')
_CHAPTER_FOLDER = re.compile(
    r'(?i)(?:\d+(?:\.\d+)?|(?:ch(?:apter)?|episode|ep|章|话|話)[ ._-]*\d+.*|'
    r'第?\s*[0-9一二三四五六七八九十百零〇]+\s*[章话話回].*)$')


def _cancel(cancel):
    if cancel and (cancel() if callable(cancel) else cancel.is_set()):
        raise Paused('本地入库已暂停')


def _extension(path):
    return path.suffix.lstrip('.').lower()


def _natural_name(name):
    pieces = re.split(r'([0-9]+)', name.casefold())
    return tuple((int(part), '') if part.isascii() and part.isdigit() else (-1, part)
                 for part in pieces), name.casefold(), name


def _member_parts(name):
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise ValueError('漫画页的路径无效')
    parts = name.split('/')
    for part in parts:
        if (not part or len(part) > 240 or part in ('.', '..') or part.endswith((' ', '.'))
                or _UNSAFE_CHARS.search(part) or _WINDOWS_RESERVED.fullmatch(part.split('.')[0].rstrip(' '))):
            raise ValueError('漫画含有不安全的路径')
    return parts


def _safe_path(path):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('路径含有上级目录引用')
    for item in reversed((path, *path.parents)):
        if is_alias(item):
            raise ValueError('本地入库不读取符号链接或目录联接')
    return path


def selected_path(path):
    return _safe_path(path).resolve(strict=True)


def validate_file_name(name, path, directory):
    if not isinstance(name, str) or name != name.strip() or len(name) > 240 or '/' in name:
        raise ValueError('请输入有效的文件名称')
    _member_parts(name)
    if not directory and Path(name).suffix.lower() != path.suffix.lower():
        raise ValueError('请保留原文件扩展名')
    return name


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def _open_file(path):
    path = _safe_path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('请选择普通文件')
    with path.open('rb') as handle:
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            raise ValueError('读取期间源文件发生变化')
        yield handle
        if (_signature(os.fstat(handle.fileno())) != _signature(before)
                or _signature(_safe_path(path).stat()) != _signature(before)):
            raise ValueError('读取期间源文件发生变化')


def _directory_files(root, recursive, cancel, limit=MAX_SCAN_ENTRIES, skip=None, on_error=None):
    pending = [root]
    seen = 0
    while pending:
        _cancel(cancel)
        current = pending.pop()
        selected = []
        try:
            with os.scandir(_safe_path(current)) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit:
                        raise ValueError('目录内容过多，请分批导入')
                    _cancel(cancel)
                    path = Path(entry.path)
                    if is_alias(path) or (skip and skip(path)):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            selected.append((path, True))
                    elif entry.is_file(follow_symlinks=False):
                        selected.append((path, False))
        except OSError as exc:
            if not on_error:
                raise
            on_error(current, exc)
        for path, folder in reversed(sorted(selected, key=lambda row: _natural_name(row[0].name))):
            if folder:
                pending.append(path)
        for path, folder in sorted(selected, key=lambda row: _natural_name(row[0].name)):
            if not folder:
                yield path


def _page(name, size):
    if not 0 < size <= MAX_PAGE_BYTES:
        raise ValueError('漫画单页大小超出限制')
    return dict(id=name, name=name, size=size)


def _validate_pages(pages):
    used = set()
    total = 0
    for page in pages:
        normalized = unicodedata.normalize('NFC', page['id']).casefold()
        if normalized in used:
            raise ValueError('漫画含有重复的页面名称')
        used.add(normalized)
        total += page['size']
    if total > MAX_COMIC_BYTES:
        raise ValueError('漫画页总大小超出限制')
    return sorted(pages, key=lambda page: _natural_name(page['id']))


def _check_central_directory(handle):
    # Bound ZipFile's eager central-directory read before handing it the file.
    end = handle.seek(0, os.SEEK_END)
    tail_size = min(end, 65535 + 22)
    handle.seek(end - tail_size)
    tail = handle.read(tail_size)
    index = tail.rfind(b'PK\x05\x06')
    if (index < 0 or len(tail) - index < 22 or
            index + 22 + struct.unpack_from('<H', tail, index + 20)[0] != len(tail)):
        raise ValueError('无效的 ZIP 或 CBZ 漫画')
    _, disk, central_disk, disk_entries, entries, size, offset, _ = struct.unpack_from('<4s4H2LH', tail, index)
    end_record = end - tail_size + index
    central_end = end_record
    if end_record >= 20:
        handle.seek(end_record - 20)
        locator = handle.read(20)
        if locator.startswith(b'PK\x06\x07'):
            _, zip64_disk, start, disks = struct.unpack('<4sLQL', locator)
            if zip64_disk or disks != 1 or start != end_record - 76 or start < 0:
                raise ValueError('不支持分卷或非标准 ZIP64 漫画')
            central_end = start
            handle.seek(start)
            magic, record_size, _, _, disk, central_disk, disk_entries, entries, size, offset = struct.unpack(
                '<4sQ2H2L4Q', handle.read(56))
            if magic != b'PK\x06\x06' or record_size != 44:
                raise ValueError('无效的 ZIP64 目录记录')
    if disk or central_disk or disk_entries != entries:
        raise ValueError('不支持分卷漫画压缩包')
    if entries > MAX_COMIC_ENTRIES or size > MAX_CENTRAL_BYTES or offset + size > central_end:
        raise ValueError('漫画压缩包的目录超出限制或已经损坏')


@contextmanager
def _open_zip(path):
    try:
        with _open_file(path) as handle:
            _check_central_directory(handle)
            with ZipFile(handle) as archive:
                yield archive
    except Paused:
        raise
    except (BadZipFile, struct.error, UnicodeError, NotImplementedError, RuntimeError) as exc:
        raise ValueError('ZIP 或 CBZ 漫画无法读取') from exc


def _zip_pages(archive, cancel=None):
    pages = []
    entries = archive.infolist()
    if len(entries) > MAX_COMIC_ENTRIES:
        raise ValueError('漫画压缩包内的项目过多')
    for entry in entries:
        _cancel(cancel)
        original = entry.orig_filename
        parts = _member_parts(original[:-1] if entry.is_dir() else original)
        mode = entry.external_attr >> 16
        if (entry.flag_bits & 0x1 or stat.S_ISLNK(mode) or
                (entry.create_system == 0 and entry.external_attr & 0x400)
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise ValueError('漫画压缩包含有加密文件、链接或特殊文件')
        if (entry.is_dir() or parts[0] == '__MACOSX' or parts[-1].startswith('.')
                or _extension(PurePosixPath(original)) not in IMAGE_EXTENSIONS):
            continue
        if entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            raise ValueError('漫画使用了不支持的压缩方式')
        if entry.file_size > 1024**2 and entry.file_size > max(1, entry.compress_size) * MAX_RATIO:
            raise ValueError('漫画页压缩比异常')
        pages.append(_page(original, entry.file_size))
    return _validate_pages(pages)


def comic_pages(path, recursive=True, cancel=None):
    """Return validated natural-order descriptors for a folder, ZIP, or CBZ."""
    _cancel(cancel)
    path = _safe_path(path)
    if path.is_dir():
        pages = []
        for child in _directory_files(path, recursive, cancel, limit=MAX_COMIC_ENTRIES):
            if _extension(child) not in IMAGE_EXTENSIONS or child.name.startswith('.'):
                continue
            relative = child.relative_to(path).as_posix()
            parts = _member_parts(relative)
            if parts[0] == '__MACOSX':
                continue
            pages.append(_page(relative, _safe_path(child).stat().st_size))
        return _validate_pages(pages)
    if _extension(path) not in COMIC_EXTENSIONS:
        raise ValueError('请选择图片文件夹、ZIP 或 CBZ 漫画')
    with _open_zip(path) as archive:
        return _zip_pages(archive, cancel)


def _comic_record(path, recursive, cancel):
    pages = comic_pages(path, recursive, cancel)
    if not pages:
        return None
    is_folder = path.is_dir()
    return dict(path=str(path), kind='comic', title=path.name if is_folder else path.stem,
                bytes=sum(page['size'] for page in pages) if is_folder else path.stat().st_size,
                extension='' if is_folder else _extension(path), pages=pages)


def _comic_children(path, cancel):
    with os.scandir(path) as entries:
        candidates = []
        for index, entry in enumerate(entries):
            _cancel(cancel)
            if index >= MAX_SCAN_ENTRIES:
                raise ValueError('目录内容过多，请分批导入')
            child = Path(entry.path)
            if not is_alias(child) and (entry.is_dir(follow_symlinks=False) or
                    (entry.is_file(follow_symlinks=False) and _extension(child) in COMIC_EXTENSIONS)):
                candidates.append(child)
    return sorted(candidates, key=lambda child: _natural_name(child.name))


def scan(paths, kind, recursive=False, batch=False, cancel=None, on_error=None):
    """Scan selected files/folders; malformed discovered books do not hide siblings."""
    if kind not in ('media', 'image', 'video', 'comic'):
        raise ValueError('不支持的入库类型')
    seen = set()
    for original in paths:
        _cancel(cancel)
        root = _safe_path(original)
        if kind == 'comic':
            children = batch and root.is_dir()
            candidates = _comic_children(root, cancel) if children else (root,)
            for path in candidates:
                _cancel(cancel)
                if path in seen:
                    continue
                seen.add(path)
                try:
                    row = _comic_record(path, recursive, cancel)
                except Paused:
                    raise
                except (OSError, ValueError) as exc:
                    if on_error:
                        on_error(path, exc)
                    elif not children:
                        raise
                    continue
                if row:
                    yield row
                elif on_error and not children:
                    on_error(path, ValueError('所选漫画没有可读的图片页面'))
        else:
            inputs = _directory_files(root, recursive, cancel) if root.is_dir() else (root,)
            for path in inputs:
                _cancel(cancel)
                ext = _extension(path)
                media_kind = 'image' if ext in IMAGE_EXTENSIONS else 'video' if ext in VIDEO_EXTENSIONS else ''
                if not media_kind or kind not in ('media', media_kind) or path in seen:
                    continue
                seen.add(path)
                info = _safe_path(path).stat()
                if stat.S_ISREG(info.st_mode):
                    yield dict(path=str(path), kind=media_kind, title=path.stem,
                               bytes=info.st_size, extension=ext, pages=[])


def scan_root(root, kind, include_loose, cancel, on_error, excluded_path, on_skipped):
    """Index a configured library without following links or expanding archives."""
    root = selected_path(root)
    if not root.is_dir():
        raise ValueError('媒体库目录不可用')

    def skip(path):
        return (path.name.startswith('.') or path.name.casefold() in ('$recycle.bin','system volume information')
                or path.name == '__MACOSX' or path.parent == root and path.name.casefold() == 'x2lt本地')

    page_folders = {}
    invalid_folders = set()
    for path in _directory_files(root, True, cancel, skip=skip, on_error=on_error):
        extension = _extension(path)
        image = extension in IMAGE_EXTENSIONS
        if not image and extension not in (COMIC_EXTENSIONS if kind == 'comic' else VIDEO_EXTENSIONS):
            continue
        if excluded_path(path):
            on_skipped()
            continue
        try:
            if kind == 'media':
                yield dict(path=str(path),kind='image' if image else 'video',title=path.stem,
                           bytes=_safe_path(path).stat().st_size,extension=extension,pages=[])
            elif not image:
                row = _comic_record(path, True, cancel)
                if row is not None:
                    yield row
                else:
                    on_error(path, ValueError('所选漫画没有可读的图片页面'))
            else:
                folder = path.parent
                if folder == root and not include_loose:
                    continue
                try:
                    _member_parts(path.name)
                    pages = page_folders.setdefault(folder, [])
                    if len(pages) >= MAX_COMIC_ENTRIES:
                        raise ValueError('漫画页数超出限制')
                    pages.append((path, _safe_path(path).stat().st_size))
                except (OSError, ValueError):
                    invalid_folders.add(folder)
                    raise
        except (OSError, ValueError) as exc:
            on_error(path, exc)

    def book_folder(folder):
        ancestors = [parent for parent in folder.parents if parent != root and root in parent.parents]
        for parent in reversed(ancestors):
            if parent in page_folders:
                return parent
            child = folder.relative_to(parent).parts[0]
            if _CHAPTER_FOLDER.fullmatch(child):
                return parent
        return folder

    books = {}
    invalid_books = {book_folder(folder) for folder in invalid_folders}
    for folder, items in page_folders.items():
        _cancel(cancel)
        book = book_folder(folder)
        if book in invalid_books:
            continue
        pages = books.setdefault(book, [])
        try:
            if len(pages) + len(items) > MAX_COMIC_ENTRIES:
                raise ValueError('漫画页数超出限制')
            for path, size in items:
                name = path.relative_to(book).as_posix()
                _member_parts(name)
                pages.append(_page(name, size))
        except ValueError as exc:
            on_error(book, exc)
            invalid_books.add(book)
    for book, pages in sorted(books.items(), key=lambda item: _natural_name(item[0].name)):
        _cancel(cancel)
        if book in invalid_books:
            continue
        try:
            pages = _validate_pages(pages)
            yield dict(path=str(book),kind='comic',title=book.name or '根目录图片',
                       bytes=sum(page['size'] for page in pages),extension='',pages=pages)
        except ValueError as exc:
            on_error(book, exc)


def read_page(path, page_id, size=(1280, 1280)):
    path = _safe_path(path)
    parts = _member_parts(page_id)
    if _extension(PurePosixPath(page_id)) not in IMAGE_EXTENSIONS:
        raise ValueError('不支持的漫画图片格式')
    if path.is_dir():
        with _open_file(path.joinpath(*parts)) as handle:
            if os.fstat(handle.fileno()).st_size > MAX_PAGE_BYTES:
                raise ValueError('漫画单页大小超出限制')
            return read_image(handle, size=size)
    with _open_zip(path) as archive:
        page = next((page for page in _zip_pages(archive) if page['id'] == page_id), None)
        if page is None:
            raise ValueError('该漫画页不存在')
        with archive.open(page_id) as handle:
            data = handle.read(page['size'] + 1)
        if len(data) != page['size']:
            raise ValueError('漫画页大小校验失败')
        return read_image(BytesIO(data), size=size)


def _file_digest(source, cancel, destination=None, expected_size=None):
    digest = sha256()
    with _open_file(source) as handle:
        actual = os.fstat(handle.fileno()).st_size
        if expected_size is not None and actual != expected_size:
            raise ValueError('漫画页在扫描后发生变化')
        copied = 0
        while block := handle.read(CHUNK_BYTES):
            _cancel(cancel)
            copied += len(block)
            if copied > actual:
                raise ValueError('本地文件在扫描后发生变化')
            digest.update(block)
            if destination is not None:
                destination.write(block)
        if copied != actual:
            raise ValueError('读取文件时字节数发生变化')
    _cancel(cancel)
    return digest.digest()


def _folder_digest(path, pages, cancel, dest=None):
    if not pages or len(pages) > MAX_COMIC_ENTRIES:
        raise ValueError('找不到有效的漫画页')
    digest = sha256(b'X2LT folder comic\0')
    _validate_pages(pages)
    for page in sorted(pages, key=lambda row: row['id']):
        _cancel(cancel)
        name = page['id']
        parts = _member_parts(name)
        if _extension(PurePosixPath(name)) not in IMAGE_EXTENSIONS:
            raise ValueError('不支持的漫画图片格式')
        source = path.joinpath(*parts)
        encoded = name.encode('utf-8')
        digest.update(struct.pack('>I', len(encoded)))
        digest.update(encoded)
        if dest is None:
            page_digest = _file_digest(source, cancel, expected_size=page['size'])
        else:
            child = _safe_path(dest.joinpath(*parts))
            child.parent.mkdir(parents=True, exist_ok=True)
            with _safe_path(child).open('xb') as target:
                page_digest = _file_digest(source, cancel, target, page['size'])
        digest.update(page_digest)
    return digest.hexdigest()


def fingerprint(path, kind, pages, cancel=None):
    """Hash a file as stored, or a comic folder by its named image pages."""
    _cancel(cancel)
    path = _safe_path(path)
    if kind == 'comic' and path.is_dir():
        return _folder_digest(path, pages, cancel)
    return _file_digest(path, cancel).hex()


def copy_into(source, kind, pages, dest, cancel=None):
    """Write to a new caller-owned staging path and verify it before promotion."""
    _cancel(cancel)
    source, dest = _safe_path(source), _safe_path(dest)
    if kind == 'comic' and source.is_dir():
        dest.mkdir()
        expected = _folder_digest(source, pages, cancel, dest)
    else:
        with dest.open('xb') as target:
            expected = _file_digest(source, cancel, target).hex()
    if fingerprint(dest, kind, pages, cancel) != expected:
        raise ValueError('本地复制后的完整性校验失败')
    return expected
