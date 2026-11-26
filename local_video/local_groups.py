# SPDX-License-Identifier: GPL-2.0-only
"""Library-scoped, ordered groups of existing local collection entries."""
from collections import OrderedDict
from uuid import uuid4


MAX_MEMBERS = 200


class LocalGroups:
    def __init__(self, collection):
        self.collection = collection
        self.db, self.lock = collection.db, collection.lock
        self.undo = OrderedDict()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS local_groups(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, library TEXT NOT NULL,
                    title TEXT NOT NULL, cover INTEGER NOT NULL, body TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS local_groups_library ON local_groups(library);
                CREATE TABLE IF NOT EXISTS local_group_items(
                    library TEXT NOT NULL, group_id INTEGER NOT NULL, entry INTEGER NOT NULL,
                    position INTEGER NOT NULL, PRIMARY KEY(library,entry)
                );
                CREATE INDEX IF NOT EXISTS local_group_items_order ON local_group_items(group_id,position);
                CREATE TRIGGER IF NOT EXISTS local_group_unlink BEFORE DELETE ON local_memberships
                BEGIN
                    DELETE FROM local_groups
                    WHERE id=(SELECT group_id FROM local_group_items WHERE library=OLD.library AND entry=OLD.entry)
                    AND NOT EXISTS(
                        SELECT 1 FROM local_group_items WHERE group_id=local_groups.id LIMIT 1 OFFSET 2
                    );
                    DELETE FROM local_group_items
                    WHERE group_id=(SELECT group_id FROM local_group_items WHERE library=OLD.library AND entry=OLD.entry)
                    AND NOT EXISTS(SELECT 1 FROM local_groups WHERE id=local_group_items.group_id);
                    DELETE FROM local_group_items WHERE library=OLD.library AND entry=OLD.entry;
                END;
            """)
            if 'body' not in {row['name'] for row in self.db.execute('PRAGMA table_info(local_groups)')}:
                self.db.execute("ALTER TABLE local_groups ADD COLUMN body TEXT NOT NULL DEFAULT ''")

    def page(self, library, query, tags, untagged, offset, limit):
        with self.lock:
            if not self.collection.one("""SELECT 1 FROM local_groups WHERE library=?
                                         UNION ALL SELECT 1 FROM telegram_saved WHERE library=? LIMIT 1""",
                                       (library, library)):
                return self.collection.page(library,query,tags,untagged,offset,limit)
            suffix, params = self.collection.filters(query,tags,untagged,grouped=True,telegram=True)
            matches = """
                SELECT COALESCE(g.id,c.id,e.id) identifier,
                    CASE WHEN g.id IS NOT NULL THEN 'group' WHEN c.id IS NOT NULL THEN 'telegram'
                         ELSE 'entry' END card_type, MAX(e.added) added
                FROM local_memberships m JOIN local_entries e ON e.id=m.entry
                LEFT JOIN local_group_items gi ON gi.library=m.library AND gi.entry=e.id
                LEFT JOIN local_groups g ON g.id=gi.group_id
                LEFT JOIN telegram_saved t ON t.library=m.library AND t.entry=e.id AND g.id IS NULL
                LEFT JOIN model_variants v ON v.id=t.variant
                LEFT JOIN model_media mm ON mm.id=v.media_id
                LEFT JOIN model_contents c ON c.id=mm.content_id
                LEFT JOIN telegram_messages tm ON tm.content=c.id
                WHERE m.library=?
            """ + suffix + ' GROUP BY identifier,card_type'
            args = [library,*params]
            total = self.db.execute('SELECT COUNT(*) FROM (' + matches + ')',args).fetchone()[0]
            limit = min(60,max(1,limit))
            offset = min(max(0,offset),max(0,(total-1)//limit)*limit)
            cards = self.collection.rows(matches + ' ORDER BY added DESC,identifier DESC,card_type DESC LIMIT ? OFFSET ?',
                                         [*args,limit,offset])
            items = [self.telegram_card(library, card['identifier']) if card['card_type'] == 'telegram' else
                     self.card(library, card['identifier'], card['card_type'] == 'group') for card in cards]
            return dict(items=items,total=total,offset=offset)

    def telegram_card(self, library, content):
        rows = self.collection.rows("""
            SELECT e.id,e.title,e.display_title,e.body,e.kind,e.bytes,e.added,
                   CASE WHEN e.text_edited THEN e.body ELSE c.text END message_body
            FROM telegram_saved t JOIN model_variants v ON v.id=t.variant
            JOIN model_media mm ON mm.id=v.media_id JOIN model_contents c ON c.id=mm.content_id
            JOIN local_memberships m ON m.library=t.library AND m.entry=t.entry
            JOIN local_entries e ON e.id=t.entry
            WHERE t.library=? AND c.id=? AND NOT EXISTS(
                SELECT 1 FROM local_group_items gi JOIN local_groups g ON g.id=gi.group_id
                WHERE gi.library=t.library AND gi.entry=t.entry)
            GROUP BY e.id ORDER BY MIN(t.model_location)
        """, (library, content))
        body = rows[0]['message_body']
        for row in rows:
            del row['message_body']
        items = self.collection.describe(rows, library, content)
        tags = sorted({tag for item in items for tag in item['tags']}, key=str.casefold)
        return {**items[0], 'body': body, 'tags': tags, 'telegram_items': items}

    def card(self, library, identifier, grouped):
        if not grouped:
            rows = self.collection.rows('SELECT id,title,display_title,body,kind,bytes,added FROM local_entries WHERE id=?',
                                        (identifier,))
            return self.collection.describe(rows,library)[0]
        group = self.group(library,identifier)
        rows = self.collection.rows("""
            SELECT e.id,e.title,e.display_title,e.body,e.kind,e.bytes,e.added FROM local_group_items gi
            JOIN local_entries e ON e.id=gi.entry WHERE gi.group_id=? ORDER BY gi.position
            """,(identifier,))
        items = self.collection.describe(rows,library)
        cover = next((item for item in items if item['id'] == group['cover']),items[0])
        return {**cover,'title':group['title'],'body':group['body'],
                'group':dict(id=identifier,title=group['title'],body=group['body'],
                                                           items=items,cover_id=cover['id'])}

    def group(self, library, identifier):
        group = self.collection.one('SELECT * FROM local_groups WHERE library=? AND id=?',(library,identifier))
        if not group:
            raise ValueError('找不到当前媒体库的集合')
        return group

    def members(self, identifier):
        return [r['entry'] for r in self.collection.rows(
            'SELECT entry FROM local_group_items WHERE group_id=? ORDER BY position',(identifier,))]

    def entries(self, library, identifiers):
        if not identifiers:
            return {}
        placeholders = ','.join('?' for _ in identifiers)
        rows = self.collection.rows("""
            SELECT e.id,e.kind,COALESCE(NULLIF(e.display_title,''),e.title) title,gi.group_id
            FROM local_memberships m JOIN local_entries e ON e.id=m.entry
            LEFT JOIN local_group_items gi ON gi.entry=e.id AND gi.library=m.library
            WHERE m.library=? AND e.id IN (""" + placeholders + ')', [library,*identifiers])
        if len(rows) != len(identifiers):
            raise ValueError('只能管理当前媒体库内的本地媒体')
        return {r['id']:r for r in rows}

    def snapshot(self, library, keys):
        result = {}
        for key in keys:
            group = self.collection.one('SELECT * FROM local_groups WHERE id=? AND library=?',(key,library))
            if group:
                result[key] = group, self.members(key)
        return result

    def drop(self, identifier):
        self.db.execute('DELETE FROM local_group_items WHERE group_id=?',(identifier,))
        self.db.execute('DELETE FROM local_groups WHERE id=?',(identifier,))

    def save_members(self, library, identifier, members):
        self.db.execute('DELETE FROM local_group_items WHERE group_id=?',(identifier,))
        self.db.executemany('INSERT INTO local_group_items VALUES(?,?,?,?)',
                            ((library,identifier,entry,index) for index,entry in enumerate(members)))

    def retain(self, group, members):
        if len(members) < 2:
            self.drop(group['id'])
            return
        self.save_members(group['library'],group['id'],members)
        if group['cover'] not in members:
            self.db.execute('UPDATE local_groups SET cover=? WHERE id=?',(members[0],group['id']))

    def change(self, library, p):
        with self.lock, self.db:
            operation = p['operation']
            if operation == 'undo':
                return self.restore(library,p.get('undo'))
            if operation == 'merge':
                before, keys = self.merge(library,p)
            elif operation in ('edit','detach','dissolve'):
                group = self.group(library,p.get('group_id'))
                keys = {group['id']}
                before = self.snapshot(library,keys)
                self.edit(group,before[group['id']][1],operation,p)
            else:
                raise ValueError('集合操作无效')
            after = self.snapshot(library,keys)
            if before == after:
                return {}
            token = uuid4().hex
            self.undo[library] = token, keys, before, after
            self.undo.move_to_end(library)
            if len(self.undo) > 24:
                self.undo.popitem(last=False)
            return {'undo':token}

    def merge(self, library, p):
        selected = list(dict.fromkeys(p.get('ids',[])))
        group = self.group(library,p['group_id']) if 'group_id' in p else None
        if group and 'target_id' in p:
            raise ValueError('请选择一个聚合目标')
        if not selected:
            raise ValueError('请选择要聚合的媒体')
        if not group:
            target = p.get('target_id',selected[0])
            selected = list(dict.fromkeys([target,*selected]))
            target_row = self.entries(library,[target])[target]
            if target_row['group_id'] is not None:
                group = self.group(library,target_row['group_id'])
        members = list(dict.fromkeys([*(self.members(group['id']) if group else []),*selected]))
        if not 2 <= len(members) <= MAX_MEMBERS:
            raise ValueError(f'每个集合支持 2–{MAX_MEMBERS} 项媒体')
        rows = self.entries(library,members)
        if len({row['kind']=='comic' for row in rows.values()}) > 1:
            raise ValueError('漫画请与其他漫画合为集合')
        keys = {row['group_id'] for row in rows.values() if row['group_id'] is not None}
        before = self.snapshot(library,keys)
        destination = group['id'] if group else None
        for source_id,(source,original) in before.items():
            if source_id != destination:
                self.retain(source,[entry for entry in original if entry not in rows])
        if not group:
            destination = self.db.execute('INSERT INTO local_groups(library,title,cover) VALUES(?,?,?)',
                                          (library,rows[members[0]]['title'][:158]+' 等',members[0])).lastrowid
            keys.add(destination)
        self.save_members(library,destination,members)
        return before, keys

    def edit(self, group, members, operation, p):
        if operation == 'dissolve':
            self.drop(group['id'])
            return
        if operation == 'detach':
            if p.get('entry_id') not in members:
                raise ValueError('媒体不在此集合内')
            self.retain(group,[key for key in members if key != p['entry_id']])
            return
        title = p.get('title',group['title']).strip()
        body = p.get('body',group['body'])
        cover = p.get('cover_id',group['cover'] if group['cover'] in members else members[0])
        order = p.get('order',members)
        if not title or len(title)>160 or len(body)>8000 or any(char in title for char in '\r\n\0'):
            raise ValueError('集合名称或正文格式无效')
        if cover not in members or len(order)!=len(members) or set(order)!=set(members):
            raise ValueError('集合封面或顺序无效')
        self.db.execute('UPDATE local_groups SET title=?,body=?,cover=? WHERE id=?',(title,body,cover,group['id']))
        self.save_members(group['library'],group['id'],order)

    def restore(self, library, token):
        record = self.undo.get(library)
        if record is None or not token or record[0] != token:
            raise ValueError('撤销已失效')
        _, keys, before, after = record
        if self.snapshot(library,keys) != after:
            raise ValueError('集合已变化，无法撤销')
        all_members = list(dict.fromkeys(entry for _,members in before.values() for entry in members))
        self.entries(library,all_members)
        for key in keys:
            self.drop(key)
        for key,(group,members) in before.items():
            self.db.execute('INSERT INTO local_groups(id,library,title,cover,body) VALUES(?,?,?,?,?)',
                            (key,library,group['title'],group['cover'],group['body']))
            self.save_members(library,key,members)
        del self.undo[library]
        return {}
