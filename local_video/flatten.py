# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Verified video-only relocation; completion receipts stay in the archive."""
import os
from pathlib import Path
import re
import shutil
import tempfile

from .archive import original_path, manifest, read_json, save_receipt, valid_receipt, migrate_receipt
from .runner import destination_for
from .images import extension_for
from .security import redact, atomic_json
from .transfer import file_hash, Paused


def _publish(temp, target):
    if os.name == 'nt':
        os.rename(temp, target)
    else:
        os.link(temp, target)
        Path(temp).unlink()


def _write_manifest(path, data):
    atomic_json(path, data)


def copy_verified(source, target, size, digest, cancel, progress=None):
    if shutil.disk_usage(target.parent).free < size + 1024 * 1024:
        raise OSError('目标目录所在磁盘空间不足')
    fd, name = tempfile.mkstemp(prefix='.flatten-', suffix='.tmp', dir=target.parent)
    temp = Path(name)
    try:
        done = 0
        with os.fdopen(fd, 'wb') as output, source.open('rb') as original:
            while block := original.read(1024 * 1024):
                if cancel.is_set():
                    raise Paused('整理已暂停，原文件保留')
                output.write(block)
                done += len(block)
                if progress:
                    progress(done, size)
            output.flush()
            os.fsync(output.fileno())
        if temp.stat().st_size != size or file_hash(temp, cancel, progress) != digest:
            raise RuntimeError('迁移副本校验失败，已保留原视频')
        _publish(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def cleanup_source(source, target, output, digest, cancel):
    if source.is_symlink() or target.is_symlink():
        raise ValueError('不整理符号链接')
    source, target = source.resolve(), target.resolve()
    if source == target:
        return
    if source.suffix.lower() not in ('.mp4','.jpg','.png','.webp') or target.suffix.lower() != source.suffix.lower():
        raise ValueError('视频路径无效')
    if file_hash(target, cancel) != digest:
        raise RuntimeError('目标视频校验失败，原视频保留')
    if source.exists():
        if file_hash(source, cancel) != digest:
            raise RuntimeError('原视频在整理期间被修改，保留原文件')
        source.unlink()
    # Original JSON and its directory deliberately remain in place.


def _valid(path, row, cancel, progress=None):
    return (not path.is_symlink() and path.is_file() and
            path.stat().st_size == row['bytes'] and
            file_hash(path, cancel, progress) == row['sha256'])


def _move(store, row, source, target, cancel, emit, source_verified=False):
    progress = lambda done, total: emit({'type': 'file_progress', 'done': done, 'total': total, 'text': '复制 / 校验'})
    if not source_verified and not _valid(source, row, cancel, progress):
        raise RuntimeError('找不到原视频或校验不符；下载记录保留')
    target.parent.mkdir(parents=True, exist_ok=True)
    if source == target:
        store.relocate(row['id'], target, target)
        store.finish_relocation(row['id'])
        save_receipt(row, target, clear_pending=True)
        return
    # Persist the exact reservation in the ORIGINAL receipt before copying.
    # This permits recovery after publishing the copy but before DB commit.
    prior = valid_receipt(row) or {}
    reserved = prior.get('pending_target') == str(target) and prior.get('pending_source') == str(source)
    if target.exists() or target.is_symlink():
        restored = target == original_path(row)
        if not (reserved or restored) or not _valid(target, row, cancel, progress):
            raise FileExistsError('目标已有文件，已保留双方，不覆盖')
    save_receipt(row, source, writer=_write_manifest, pending_target=str(target), pending_source=str(source))
    if not target.exists():
        copy_verified(source, target, row['bytes'], row['sha256'], cancel, progress)
    store.relocate(row['id'], target, source)
    save_receipt(row, target, clear_pending=True, pending_source=str(source))
    cleanup_source(source, target, row['output'], row['sha256'], cancel)
    store.finish_relocation(row['id'])
    save_receipt(row, target, clear_pending=True)


def _prepare(store, row, cancel, emit):
    source = destination_for(row)
    # Preserve any durable reservation when migrating legacy metadata.
    receipt = valid_receipt(row) or {}
    migrate_receipt(row, source, emit)
    if receipt.get('pending_target'):
        save_receipt(row, source, pending_target=receipt['pending_target'], pending_source=receipt.get('pending_source', ''))
    if row.get('relocation_source') and _valid(source, row, cancel):
        cleanup_source(Path(row['relocation_source']), source, row['output'], row['sha256'], cancel)
        store.finish_relocation(row['id'])
    return source


def _rows(store, output):
    return store.completed_for_output(output)


def flatten_completed(store, output, target, cancel, emit):
    output, target = Path(output).resolve(), Path(target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    rows = _rows(store, output)
    result = {'moved': 0, 'skipped': 0, 'failed': 0}
    emit({'type': 'operation_progress', 'done': 0, 'total': len(rows), 'text': '脱壳'})
    for index, row in enumerate(rows, 1):
        if cancel.is_set():
            break
        try:
            source = _prepare(store, row, cancel, emit)
            if not _valid(source, row, cancel):
                raise RuntimeError('找不到原视频或校验不符；下载记录保留')
            if source.parent == target:
                result['skipped'] += 1
                continue
            stem = f"{row['author']}_{row['tweet_id']}_{row['media_id']}_{row['quality']}"
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,260}', stem):
                raise ValueError('任务名称含有不安全字符')
            receipt = valid_receipt(row) or {}
            count = 0
            while True:
                destination = target / (stem + (f'_{count}' if count else '') + '.' + extension_for(row))
                reserved = receipt.get('pending_target') == str(destination) and receipt.get('pending_source') == str(source)
                # A foreign sidecar also reserves its filename; do not disturb it.
                if (not destination.exists() and not destination.is_symlink() and not manifest(destination).exists()) or (reserved and _valid(destination, row, cancel)):
                    break
                count += 1
            _move(store, row, source, destination, cancel, emit, source_verified=True)
            result['moved'] += 1
            emit({'type': 'changed', 'id': row['id']})
        except Paused:
            break
        except Exception as exc:
            result['failed'] += 1
            emit({'type': 'message', 'text': f"整理任务 {row['id']} 未完成：{redact(exc)}"})
        finally:
            emit({'type': 'operation_progress', 'done': index, 'total': len(rows), 'text': '脱壳'})
    return result


def _find_video(root, row, cancel, emit):
    # Never follow directory symlinks or search beyond the selected folder.
    root = Path(root).resolve()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in files:
            if cancel.is_set():
                raise Paused('查找已暂停')
            path = Path(directory) / name
            if path.suffix.lower() == '.' + extension_for(row) and _valid(path, row, cancel, lambda d, t: emit({'type': 'file_progress', 'done': d, 'total': t, 'text': '查找校验'})):
                return path.resolve()
    return None


def rollback_completed(store, output, cancel, emit, search=None):
    output = Path(output).resolve()
    rows = _rows(store, output)
    result = {'moved': 0, 'skipped': 0, 'failed': 0}
    emit({'type': 'operation_progress', 'done': 0, 'total': len(rows), 'text': '回退'})
    for index, row in enumerate(rows, 1):
        if cancel.is_set():
            break
        try:
            source = _prepare(store, row, cancel, emit)
            target = original_path(row)
            if source == target and _valid(source, row, cancel):
                result['skipped'] += 1
                continue
            if not _valid(source, row, cancel):
                source = _find_video(search, row, cancel, emit) if search else target if _valid(target, row, cancel) else None
            if not source:
                raise RuntimeError('找不到视频；可用“查找并回退…”选择手动移入的文件夹')
            _move(store, row, source, target, cancel, emit, source_verified=True)
            result['moved'] += 1
            emit({'type': 'changed', 'id': row['id']})
        except Paused:
            break
        except Exception as exc:
            result['failed'] += 1
            emit({'type': 'message', 'text': f"回退任务 {row['id']} 未完成：{redact(exc)}"})
        finally:
            emit({'type': 'operation_progress', 'done': index, 'total': len(rows), 'text': '回退'})
    return result
