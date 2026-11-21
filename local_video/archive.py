# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Persistent completion receipts kept at the original download location."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .security import atomic_json
from .images import extension_for


def comparable_path(path):
    # Windows resolve() can transiently retain its extended prefix when a
    # concurrent worker creates a previously missing parent directory.
    value=str(path)
    if os.name=='nt':
        if value.startswith('\\\\?\\UNC\\'):value='\\\\'+value[8:]
        elif value.startswith('\\\\?\\'):value=value[4:]
    return Path(value)


def original_path(row):
    for key in ('author', 'tweet_id', 'media_id', 'quality'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(row[key])):
            raise ValueError('任务文件名含有不安全字符')
    root = Path(row['output']).resolve()
    extension = extension_for(row)
    folder = 'images' if row.get('media_type') == 'image' else row['quality']
    path = root / folder / row['author'] / row['tweet_id'] / f"{row['tweet_id']}_{row['media_id']}.{extension}"
    if not comparable_path(path.resolve()).is_relative_to(comparable_path(root)) or path.is_symlink():
        raise ValueError('目标路径超出保存目录或为符号链接')
    return path


def manifest(path):
    return Path(path).with_suffix(Path(path).suffix + '.verified.json')


def read_json(path):
    try:
        if Path(path).is_symlink():
            return {}
        value = json.loads(Path(path).read_text(encoding='utf8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def valid_receipt(row):
    data = read_json(manifest(original_path(row)))
    if (data.get('downloaded') is True and
            data.get('receipt_generation',0) == row.get('receipt_generation',0) and
            all(data.get(key) == row[key] for key in ('tweet_id', 'media_id', 'quality')) and
            data.get('archive_output') == str(Path(row['output']).resolve()) and
            isinstance(data.get('local_path'), str) and
            Path(data['local_path']).is_absolute() and
            Path(data['local_path']).suffix.lower() == '.' + extension_for(row) and
            type(data.get('bytes')) is int and data['bytes'] > 0 and
            re.fullmatch('[0-9a-f]{64}', str(data.get('sha256', ''))) and
            data.get('verification') in ('length+sha256', 'sha256-only')):
        return data
    return None


def save_receipt(row, path=None, clear_pending=False, writer=atomic_json, **extra):
    original = original_path(row)
    location = str(Path(path or row.get('local_path') or original).resolve())
    data = {
        'receipt_version': 1, 'downloaded': True,
        'receipt_generation': row.get('receipt_generation',0),
        'media_type': row.get('media_type','video'), 'extension': extension_for(row), 'tweet_text': row.get('tweet_text',''),
        'tweet_id': row['tweet_id'], 'media_id': row['media_id'], 'quality': row['quality'],
        'archive_job': row['id'], 'archive_output': str(Path(row['output']).resolve()),
        'source': urlsplit(row['url'])._replace(query='', fragment='').geturl(),
        'bytes': row['bytes'], 'sha256': row['sha256'],
        'verification': row['verification'], 'local_path': location,
    }
    # Verification/location refresh must not discard an in-flight move journal.
    if not clear_pending:
        prior = valid_receipt(row) or {}
        data.update({key: prior[key] for key in ('pending_source', 'pending_target') if key in prior})
    data.update(extra)
    writer(manifest(original), data)
    return data


def migrate_receipt(row, current, emit):
    """Only remove a legacy sidecar after a matching original receipt is durable."""
    save_receipt(row, current)
    sidecar = manifest(current)
    if sidecar == manifest(original_path(row)) or not sidecar.exists():
        return
    data = read_json(sidecar)
    owned = (data.get('archive_job') == row['id'] and
             data.get('archive_output') == str(Path(row['output']).resolve()))
    # 0.1 downloader manifests had no archive ownership; match URL, size and hash.
    legacy = ('archive_job' not in data and data.get('source') ==
              urlsplit(row['url'])._replace(query='', fragment='').geturl())
    if (owned or legacy) and data.get('sha256') == row['sha256'] and data.get('bytes') == row['bytes']:
        sidecar.unlink()
    else:
        emit({'type': 'message', 'text': '有一个旧 JSON 无法确认归属，已保留'})
