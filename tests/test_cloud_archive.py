import unittest

from local_video.cloud_archive import key, organize


def media(number, platform='x', group='alice', tags=(), known=False):
    return dict(content=number, title=str(number), tags=list(tags), platform=platform, group=group,
                group_label=group, source_label=platform, known=known)


class ArchiveLayoutTests(unittest.TestCase):
    def test_threshold_is_strict_and_merges_only_within_source(self):
        records = [media(1), media(2), media(3, group='bob'), media(4, 'telegram', 'channel'),
                   media(5, 'local', ''), media(6, group='bob', known=True)]
        decisions, _, _, _, _ = organize(records, 'source', 2, {}, {})
        self.assertEqual([folder for _, folder in decisions], [
            ['推特', 'alice'], ['推特', 'alice'], ['推特', '其他博主'],
            ['电报', '其他频道'], ['本地', '未分组'], ['推特', 'bob'],
        ])

    def test_primary_tag_and_alias_sanitize_without_duplicate_paths(self):
        records = [media(1, tags=('旅行', '参考')), media(2, tags=('参考',)), media(3)]
        decisions, folders, _, choices, _ = organize(
            records, 'tag', 0, {key(('tag', '参考')): '我的/资料'}, {'1': '参考'})
        self.assertEqual([folder for _, folder in decisions], [['我的_资料'], ['我的_资料'], ['未打标签']])
        self.assertEqual(sum(folder['count'] for folder in folders), len(records))
        self.assertEqual(choices[0]['selected'], '参考')
        with self.assertRaisesRegex(ValueError, '不能重复'):
            organize(records, 'tag', 0, {key(('tag', '参考')): 'same', key(('tag', '旅行')): 'Same'}, {})

    def test_different_group_cannot_reuse_a_previous_archive_name(self):
        _, _, _, _, claims = organize([media(1, tags=('来源',))], 'tag', 0, {}, {})
        reserved = {parent: {name: owner} for (parent, name), owner in claims.items()}
        decisions, _, _, _, _ = organize([media(2, tags=('资料',))], 'tag', 0,
                                        {key(('tag', '资料')): '来源书签'}, {}, reserved)
        self.assertEqual(decisions[0][1], ['来源书签'])
        with self.assertRaisesRegex(ValueError, '不能重复'):
            organize([media(2, tags=('资料',))], 'tag', 0, {key(('tag', '资料')): '来源'}, {}, reserved)
