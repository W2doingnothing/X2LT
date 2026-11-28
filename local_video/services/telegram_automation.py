# SPDX-License-Identifier: GPL-2.0-only
"""Coordinate startup and interval triggers for durable Telegram incremental scans."""
import re
from time import time


def scan_rule_key(rule):
    return f"{rule['mode']}:{rule['source']}:{rule['library_id']}"


def telegram_scan_rules(value):
    fields = {'mode', 'source', 'title', 'library_id', 'media_mode', 'on_start', 'in_window', 'interval'}
    if type(value) is not list or len(value) > 64:
        raise ValueError('最多可设置 64 条 Telegram 自动扫描规则')
    result, seen = [], set()
    for rule in value:
        if (type(rule) is not dict or rule.keys() != fields
                or rule['mode'] not in ('saved', 'channel')
                or type(rule['source']) is not str
                or (rule['source'] != '' if rule['mode'] == 'saved' else
                    not re.fullmatch(r'-[0-9]{1,17}', rule['source']))
                or type(rule['title']) is not str or len(rule['title']) > 240
                or type(rule['library_id']) is not str or not re.fullmatch(r'[\w-]{1,128}', rule['library_id'])
                or rule['media_mode'] not in ('all', 'image', 'video')
                or type(rule['on_start']) is not bool or type(rule['in_window']) is not bool
                or type(rule['interval']) is not int or not 5 <= rule['interval'] <= 720):
            raise ValueError('Telegram 自动扫描规则无效')
        key = scan_rule_key(rule)
        if key in seen:
            raise ValueError('该 Telegram 来源在此媒体库已有规则')
        seen.add(key)
        result.append(dict(rule))
    return result


class TelegramAutomation:
    def __init__(self, rules, start_scan, scan_result, journal, *, clock=time):
        self.rules = rules
        self.start_scan = start_scan
        self.scan_result = scan_result
        self.journal = journal
        self.clock = clock
        self.startup_due = {scan_rule_key(rule) for rule in rules() if rule['on_start']}
        self.active = None
        self.retry_at = {}
        self.status = {}

    def tick(self, window_open, can_scan):
        now = self.clock()
        if self.active:
            key, identifier = self.active
            result = self.scan_result(identifier)
            if result and result['state'] == 'scanning':
                return
            self.active = None
            if result and result['state'] == 'ready':
                self.journal.record_automatic_run(key, now)
                self.startup_due.discard(key)
                self.retry_at.pop(key, None)
            else:
                self.retry_at[key] = now + max(60, (result or {}).get('retry_after', 0))
            if result:
                self.status[key] = result
        if not can_scan:
            return
        for rule in self.rules():
            key = scan_rule_key(rule)
            if self.retry_at.get(key, 0) > now:
                continue
            startup = key in self.startup_due and rule['on_start']
            last = self.journal.last_automatic_run(key) if rule['in_window'] and window_open else None
            timed = (rule['in_window'] and window_open
                     and (last is None or last > now or now - last >= rule['interval'] * 60))
            if not (startup or timed):
                continue
            try:
                identifier = self.start_scan(rule)
            except (OSError, ValueError) as exc:
                self.retry_at[key] = now + 60
                self.status[key] = {'state': 'failed', 'error': str(exc)}
                continue
            if identifier is None:
                return
            self.active = key, identifier
            break

    def results(self):
        results = {**self.status}
        if self.active:
            key, identifier = self.active
            if result := self.scan_result(identifier):
                results[key] = result
        return results
