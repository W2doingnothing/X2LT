# SPDX-License-Identifier: GPL-2.0-only
"""A shuffle session over saved video records from selected libraries."""
from dataclasses import dataclass
import random
import secrets


@dataclass(frozen=True)
class Video:
    source: str
    id: int
    title: str
    library: str
    keys: tuple


def saved_videos(store, selected):
    names = {item['id']: item['name'] for item in selected}
    outputs = {item['path']: item['name'] for item in selected}
    output_parameters = ','.join('?' for _ in outputs)
    library_parameters = ','.join('?' for _ in names)
    with store.lock:
        downloads = store.db.execute(f"""
            SELECT id, tweet_id, media_id, author, substr(tweet_text, 1, 160) AS tweet_text, output, sha256
            FROM jobs WHERE output IN ({output_parameters})
            AND state='completed' AND media_type='video'
            ORDER BY id
        """, tuple(outputs)).fetchall()
        local = store.db.execute(f"""
            SELECT e.id, e.display_title, e.title, e.digest, MIN(m.library) AS library
            FROM local_memberships m JOIN local_entries e ON e.id=m.entry
            WHERE m.library IN ({library_parameters}) AND e.kind='video'
            AND EXISTS (SELECT 1 FROM local_locations l WHERE l.entry=e.id AND l.directory=0)
            GROUP BY e.id
        """, tuple(names)).fetchall()
    for row in downloads:
        text = row['tweet_text'].strip().splitlines()
        title = text[0] if text else '@'+row['author'] if row['author'] else 'X 视频'
        keys = [('x', row['tweet_id'], row['media_id'])]
        if row['sha256']:
            keys.append(('hash', row['sha256'].lower()))
        yield Video('x', row['id'], title[:160], outputs[row['output']], tuple(keys))
    for row in local:
        title = row['display_title'] or row['title'] or '本地视频'
        key = ('hash', row['digest'].lower()) if row['digest'] else ('local', row['id'])
        yield Video('local', row['id'], title[:160], names[row['library']], (key,))


def unique_videos(videos):
    """Coalesce X variants and byte-identical local copies into queue entries."""
    items = list(videos)
    parents = list(range(len(items)))
    owners = {}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, item in enumerate(items):
        for key in item.keys:
            original = root(owners.setdefault(key, index))
            parents[root(index)] = original
    groups = {}
    for index, item in enumerate(items):
        groups.setdefault(root(index), []).append(item)
    return list(groups.values())


class RandomFeed:
    def __init__(self, store, libraries, library_ids):
        if not library_ids:
            raise ValueError('请至少选择一个媒体库')
        with libraries.lock:
            selected = [libraries.get(identifier) for identifier in library_ids]
        self._items = unique_videos(saved_videos(store, selected))
        if not self._items:
            raise ValueError('所选媒体库没有已保存的视频')
        self._seed = secrets.randbits(128)
        self._round = 0
        self._index = 0
        self._order = self._round_order(0)
        self._revision = secrets.token_hex(16)

    def _shuffled(self, round_number):
        order = list(range(len(self._items)))
        random.Random(self._seed + round_number).shuffle(order)
        return order

    def _round_order(self, round_number):
        if len(self._items) <= 2:
            return self._shuffled(0)
        order = self._shuffled(round_number)
        # The adjustment only touches the first two positions, so the final
        # item of another round is reproducible without retaining its order.
        if round_number and order[0] == self._shuffled(round_number - 1)[-1]:
            order[0], order[1] = order[1], order[0]
        return order

    def _choices(self):
        return self._items[self._order[self._index]]

    def step(self, direction):
        if direction not in ('current', 'next', 'previous'):
            raise ValueError('随机播放切换方向无效')
        count = len(self._items)
        if direction == 'next':
            if self._index + 1 == count:
                self._round += 1
                self._order = self._round_order(self._round)
            self._index = (self._index + 1) % count
        elif direction == 'previous' and count > 1:
            if self._index:
                self._index -= 1
            elif self._round:
                self._round -= 1
                self._order = self._round_order(self._round)
                self._index = count - 1
        if direction != 'current':
            self._revision = secrets.token_hex(16)
        item = self._choices()[0]
        return dict(revision=self._revision, title=item.title, library=item.library,
                    index=self._index, count=count,
                    canPrevious=count > 1 and bool(self._index or self._round))

    def play(self, revision, issue):
        if revision != self._revision:
            raise ValueError('视频已切换')
        for item in self._choices():
            try:
                ticket = issue(dict(source=item.source, id=item.id))
            except (ValueError, OSError):
                continue
            return {**ticket, 'library': item.library}
        raise ValueError('视频文件不可用')
