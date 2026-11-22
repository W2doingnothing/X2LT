# SPDX-License-Identifier: GPL-2.0-only
"""Deterministic directory planning for cloud library uploads."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
import re
import unicodedata


SOURCES = {'x': '推特', 'twitter': '推特', 'telegram': '电报', 'local': '本地', 'guangyapan': '光鸭'}


def safe_segment(value):
    value = unicodedata.normalize('NFC', value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', value).strip(' .')
    return value[:80].rstrip(' .') or '未分组'


def key(parts):
    return json.dumps(parts, ensure_ascii=False, separators=(',', ':'))


def organize(records, mode, threshold, aliases, tag_overrides, reserved=None):
    """Return frozen archive paths, visible group settings and per-content tag choices."""
    if mode not in ('tag', 'source') or not 0 <= threshold <= 100000:
        raise ValueError('归档方式或合并阈值无效')
    if len(aliases) > 10000 or len(tag_overrides) > 10000:
        raise ValueError('目录设置过多')
    for mapping in (aliases, tag_overrides):
        if any(not isinstance(k, str) or len(k) > 1000 or not isinstance(v, str) or len(v) > 200
               for k, v in mapping.items()):
            raise ValueError('目录设置格式无效')

    counts = Counter((row['platform'], row['group']) for row in records if not row['known'])
    choices = {}
    components = {}
    routes = []
    for row in records:
        if mode == 'tag':
            tags = row['tags']
            content = str(row['content'])
            tag = tag_overrides.get(content, tags[0] if tags else '')
            if tag and tag not in tags:
                tag = tags[0] if tags else ''
            if len(tags) > 1:
                choices[content] = dict(id=content, title=row['title'], tags=tags, selected=tag)
            route = (key(('tag', tag)),)
            components[route[0]] = tag or '未打标签'
        else:
            source, group = row['platform'], row['group']
            source_key = key(('source', source))
            components[source_key] = SOURCES.get(source, row['source_label'] or source)
            if group and threshold and counts[source, group] < threshold and not row['known']:
                group_key = key(('small', source))
                label = '其他频道' if source == 'telegram' else '其他博主' if source in ('x', 'twitter') else '其他分组'
            else:
                group_key = key(('group', source, group))
                label = row['group_label'] or '未分组'
            components[group_key] = label
            route = source_key, group_key
        routes.append((row, route))

    names = {}
    sibling_keys = defaultdict(set)
    for _, route in routes:
        for i, part in enumerate(route):
            sibling_keys[route[:i]].add(part)
    reserved = reserved or {}
    claims = {}
    for ancestors in sorted(sibling_keys, key=lambda path: (len(path), path)):
        siblings = sibling_keys[ancestors]
        parent_key = key([names[part].casefold() for part in ancestors])
        used = dict(reserved.get(parent_key, {}))
        # Explicit names get first claim. Generated names stay stable and distinct.
        for part in sorted(siblings, key=lambda k: (not bool(aliases.get(k, '').strip()), k)):
            custom = aliases.get(part, '').strip()
            proposed = safe_segment(custom or components[part])
            if used.get(proposed.casefold(), part) != part:
                if custom:
                    raise ValueError('同一层级的目录别名不能重复')
                proposed = proposed[:66] + '-' + sha256(part.encode()).hexdigest()[:10]
                while used.get(proposed.casefold(), part) != part:
                    proposed += '_'
            used[proposed.casefold()] = part
            claims[parent_key, proposed.casefold()] = part
            names[part] = proposed

    folders = {}
    editable = {}
    decisions = []
    for row, route in routes:
        path = tuple(names[part] for part in route)
        folder = folders.setdefault(path, dict(path=list(path), count=0, known=0))
        folder['count'] += 1
        folder['known'] += int(row['known'])
        decisions.append((row, list(path)))
        for index, part in enumerate(route):
            editable[part] = dict(key=part, label=names[part], path=list(path[:index + 1]))
    return decisions, [folders[path] for path in sorted(folders)], list(editable.values()), list(choices.values()), claims
