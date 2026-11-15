# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Chinese Windows UI for the local video downloader."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .flatten import flatten_completed, rollback_completed
from .policy import REQUEST_STRATEGIES, likes_url, normalize_proxy, validate_source_url
from .runner import InstanceLock, Runner, destination_for
from .security import Vault, atomic_json, import_legacy, private_directory, redact
from .store import Store

STATES = {'pending': '待下载', 'downloading': '下载 / 校验', 'paused': '已暂停', 'failed': '失败，可重试', 'completed': '本地已完成'}
MEDIA_MODES = {'视频＋图片':'all', '仅视频':'video', '仅图片':'image'}
QUALITIES = {'最高画质': 'best', '省流 · 720p': '720p', '省流 · 480p': '480p'}


def app_home():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


class App:
    def __init__(self, root, home=None, data_dir=None):
        self.root = root
        self.home = Path(home or app_home())
        self.data_dir = private_directory(data_dir or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'X2TLocalVideo')
        self.instance_lock = InstanceLock(self.data_dir / 'instance.lock')
        self.vault = Vault(self.data_dir / 'credentials.dpapi')
        self.settings_file = self.data_dir / 'settings.json'
        self.store = Store(self.data_dir / 'queue.sqlite3')
        self.store.recover()
        self.events = queue.Queue()
        self.worker = None
        self.runner = None
        self.closed = False
        self.closing = False
        self.rates = {}
        self.discovering = False
        self.controls = []
        try:
            settings = json.loads(self.settings_file.read_text(encoding='utf8'))
        except (OSError, ValueError):
            settings = {}
        try:
            credentials = self.vault.load()
        except Exception:
            credentials = {}
            root.after(100, lambda: messagebox.showwarning('无法解密凭证', '保存的凭证不能由当前 Windows 用户解密，请重新导入。', parent=root))
        self.username = tk.StringVar(value=settings.get('username', ''))
        self.proxy = tk.StringVar(value=credentials.get('proxy', ''))
        self.auth = tk.StringVar(value=credentials.get('auth_token', ''))
        self.ct0 = tk.StringVar(value=credentials.get('ct0', ''))
        self.output = tk.StringVar(value=settings.get('output', str(self.home / 'downloads')))
        self.quality = tk.StringVar(value=settings.get('quality_label', '最高画质'))
        self.connections = tk.IntVar(value=settings.get('connections', 4))
        self.max_mb = tk.IntVar(value=settings.get('max_mb', 4096))
        self.request_strategy = tk.StringVar(value=next((label for label, key in REQUEST_STRATEGIES.items() if key == settings.get('request_strategy')), '保守 · 1 MiB'))
        self.media_mode = tk.StringVar(value=next((label for label,key in MEDIA_MODES.items() if key == settings.get('media_mode')), '视频＋图片'))
        self.links = tk.StringVar()
        self.status = tk.StringVar(value='就绪 · 只保存到本地')
        self.summary = tk.StringVar()
        self.theme = tk.StringVar(value=settings.get('theme', 'light') if settings.get('theme') in ('light', 'dark') else 'light')
        from .preview_ui import PreviewUI
        self.preview = PreviewUI(self)
        self._build()
        self._refresh()
        self.poll_id = root.after(150, self._poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def _build(self):
        from .ui import Shell
        self.shell = Shell(self, QUALITIES)
        try:
            self.root.iconbitmap(str(self.home / 'app.ico'))
        except tk.TclError:
            pass

    def set_theme(self, theme):
        if theme not in ('light', 'dark'):
            raise ValueError('无效主题')
        self.theme.set(theme)
        self.shell.apply_theme()
        self.preview.style()
        try:
            saved = json.loads(self.settings_file.read_text(encoding='utf8'))
            if not isinstance(saved, dict):
                saved = {}
        except (OSError, ValueError):
            saved = {}
        saved['theme'] = theme
        atomic_json(self.settings_file, saved)

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.controls.append(button)
        return button

    def _credentials(self):
        return {'auth_token': self.auth.get().strip(), 'ct0': self.ct0.get().strip(), 'proxy': normalize_proxy(self.proxy.get())}

    def _error_text(self, exc):
        return redact(exc, (self.auth.get(), self.ct0.get(), self.proxy.get()))

    def _settings(self):
        connections = int(self.connections.get())
        maximum = int(self.max_mb.get())
        if connections not in (1, 2, 4) or not 1 <= maximum <= 65536:
            raise ValueError('连接数或文件上限无效')
        return {'media_mode': MEDIA_MODES[self.media_mode.get()], 'request_strategy': REQUEST_STRATEGIES[self.request_strategy.get()], 'theme': self.theme.get(), 'username': self.username.get().strip().lstrip('@'), 'output': str(Path(self.output.get()).expanduser().resolve()), 'quality': QUALITIES[self.quality.get()], 'quality_label': self.quality.get(), 'connections': connections, 'max_mb': maximum, 'proxy': normalize_proxy(self.proxy.get())}

    def save(self, notify=True):
        try:
            settings = self._settings()
            self.vault.save(self._credentials())
            atomic_json(self.settings_file, {key: value for key, value in settings.items() if key != 'proxy'})
            self.shell.refresh_summary()
            if notify:
                self.status.set('设置已保存，凭证已加密（仅当前 Windows 用户可解密）')
            return True
        except Exception as exc:
            messagebox.showerror('保存失败', self._error_text(exc), parent=self.root)
            return False

    def import_old(self, notify=True):
        try:
            legacy_home = self.home
            if not (legacy_home / 'cookies.txt').is_file() and (legacy_home.parent / 'X2T.exe').is_file():
                legacy_home = legacy_home.parent
            data = import_legacy(legacy_home)
            if not data.get('auth_token'):
                raise ValueError('软件目录或上一级原 X2T 目录的 cookies.txt 中没有找到 X 登录凭证')
            self.username.set(data.get('username', self.username.get()))
            self.proxy.set(data.get('proxy', self.proxy.get()))
            self.auth.set(data['auth_token'])
            self.ct0.set(data.get('ct0', ''))
            if self.save(notify=False):
                self.status.set('X 配置已导入并加密；原软件明文文件保留，未导入 Telegram 会话')
                if notify:
                    messagebox.showinfo('导入成功', '已导入 X 配置并加密保存。\n原 X2T 的明文凭证仍在原目录，不会自动删除。', parent=self.root)
        except Exception as exc:
            messagebox.showerror('导入失败', self._error_text(exc), parent=self.root)

    def choose_dir(self):
        selected = filedialog.askdirectory(parent=self.root, initialdir=self.output.get())
        if selected:
            self.output.set(selected)

    def open_dir(self):
        try:
            path = Path(self.output.get()).resolve()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror('无法打开', str(exc), parent=self.root)

    def _busy(self, active):
        self.controls[:] = [control for control in self.controls if control.winfo_exists()]
        for control in self.controls:
            control.configure(state='disabled' if active else 'readonly' if isinstance(control, ttk.Combobox) else 'normal')
        self.pause_button.configure(state='normal' if active else 'disabled')
        if active:
            self.total_bar.stop()
            self.total_bar.configure(mode='indeterminate', value=0)
            self.total_bar.start(15)
            self.file_bar.stop()
            self.file_bar.configure(mode='determinate', value=0)
            self.total_text.set('准备任务…')
            self.file_text.set('等待文件…')

    def start(self, mode):
        if self.worker and self.worker.is_alive():
            return
        try:
            settings = self._settings()
            source = None
            if mode in {'latest', 'all'}:
                source = likes_url(settings['username'])
                if not self.auth.get().strip():
                    raise ValueError('读取点赞需要 Cookie，请先导入或填写 X 凭证')
            elif mode == 'link':
                source = validate_source_url(self.links.get())
            force_id = None
            if mode == 'force':
                selected = self.tree.selection()
                if not selected:
                    raise ValueError('请先在列表中选中需要重新下载的视频')
                force_id = int(selected[0])
                target_rows=self.store.get_many([force_id])
                if target_rows and self.store.is_ignored(target_rows[0]['tweet_id']):
                    raise ValueError('这条推文已忽略，请先取消忽略再强制下载')
            if not self.save(notify=False):
                return
            self.rates.clear()
            self.discovering = bool(source)
            self.runner = Runner(self.store, settings, self._credentials(), self.events.put)
            self._busy(True)
            self.status.set('任务运行中；关闭或暂停窗口会保留已下载的分段')
            self.worker = threading.Thread(target=self.runner.run, kwargs={'source': source, 'limit': 50 if mode == 'latest' else 0, 'recheck': mode == 'verify', 'force_id': force_id}, daemon=True)
            self.worker.start()
        except Exception as exc:
            messagebox.showerror('不能开始', self._error_text(exc), parent=self.root)

    def force_media(self,job_id):
        self.shell.select(job_id)
        self.start('force')

    def ignore_archive_selection(self):
        selected=self.tree.selection()
        if selected:
            rows=self.store.get_many([int(selected[0])])
            if rows:self.ignore_tweets([rows[0]['tweet_id']])

    def ignore_tweets(self, tweet_ids, ignored=True):
        try:
            self.store.ignore_tweets(tweet_ids,ignored)
            self.shell.selected_tweets.difference_update(tweet_ids)
            rows=self.store.rows_for_tweets(tweet_ids)
            self._refresh([r['id'] for r in rows])
            self.status.set(f"{'已忽略' if ignored else '已取消忽略'} {len(set(tweet_ids))} 条推文；"+('已开始的媒体允许完成，其余媒体跳过，文件和记录保留' if ignored else '不会自动开始下载，可手动继续或同步'))
        except Exception as exc:
            messagebox.showerror('无法更新忽略名单',self._error_text(exc),parent=self.root)

    def enrich(self, job_id):
        if self.worker and self.worker.is_alive():
            return
        rows = self.store.get_many([job_id])
        if not rows:
            return
        try:
            self.runner = Runner(self.store, self._settings(), self._credentials(), self.events.put)
            self.discovering = False
            self._busy(True)
            self.status.set('正在补全此推文正文和封面；不新增下载')
            self.worker = threading.Thread(target=self.runner.enrich, args=(rows[0],), daemon=True)
            self.worker.start()
        except Exception as exc:
            self._busy(False)
            messagebox.showerror('不能补全预览', self._error_text(exc), parent=self.root)

    def pause(self):
        if self.runner:
            self.runner.cancel.set()
        self.status.set('正在暂停并保存进度；网络读取最多等待当前超时（约 20 秒）')

    def on_flatten(self, custom=False):
        if self.worker and self.worker.is_alive():
            return
        try:
            output = Path(self.output.get()).resolve()
            target = output / '集中视频'
            if custom:
                selected = filedialog.askdirectory(parent=self.root, title='选择集中存放视频的目录', initialdir=str(output if output.is_dir() else self.home))
                if not selected:
                    return
                target = Path(selected)
            self.start_flatten(target)
        except Exception as exc:
            messagebox.showerror('无法整理', self._error_text(exc), parent=self.root)

    def on_rollback(self, search=False):
        if self.worker and self.worker.is_alive():
            return
        folder = None
        if search:
            folder = filedialog.askdirectory(parent=self.root, title='选择媒体被手动移入的文件夹（按大小及 SHA256 查找）')
            if not folder:
                return
        self.start_flatten(None, rollback=True, search=folder)

    def start_flatten(self, target, rollback=False, search=None):
        if self.worker and self.worker.is_alive():
            return
        output = Path(self.output.get()).resolve()
        # Reuse the cancellation interface; no discovery or downloader runs here.
        self.runner = Runner(self.store, {}, {}, self.events.put)
        cancel = self.runner.cancel
        self.discovering = False
        self._busy(True)
        action = '回退' if rollback else '脱壳'
        self.status.set(f'正在{action}：先复制校验，再更新记录；JSON 留在原目录')

        def organize():
            text = '整理结束'
            try:
                result = rollback_completed(self.store, output, cancel, self.events.put, search=search) if rollback else flatten_completed(self.store, output, target, cancel, self.events.put)
                text = f"{action}{'已暂停' if cancel.is_set() else '完成'}：转移 {result['moved']} 个，已在目标目录 {result['skipped']} 个，未完成 {result['failed']} 个。下载记录已保留。"
                self.events.put({'type': 'message', 'text': text})
            except Exception as exc:
                text = f'整理失败：{redact(exc)}'
                self.events.put({'type': 'message', 'text': text})
            finally:
                self.events.put({'type': 'changed', 'summary_only': True})
                self.events.put({'type': 'finished', 'text': text})

        self.worker = threading.Thread(target=organize, daemon=True)
        self.worker.start()

    def _refresh(self, ids=None):
        # Full snapshot only at startup or explicit manual reload. Events carry IDs.
        rows = self.store.list() if ids is None else self.store.get_many(ids)
        counts = {}
        for row in rows:
            key = str(row['id'])
            counts[row['state']] = counts.get(row['state'], 0) + 1
            verified = '已校验长度及 SHA256' if row.get('verification') == 'length+sha256' else '仅本地 SHA256；缺少服务器长度' if row.get('verification') == 'sha256-only' else '校验级别未知，请重新校验'
            state = STATES[row['state']]
            ignored=self.store.is_ignored(row['tweet_id'])
            if row['state'] == 'completed':
                try:
                    found = destination_for(row).is_file()
                except ValueError:
                    found = False
                state = '有完成记录' if not found else '本地文件在'
                verified = ('位置可找到；' if found else '媒体已移走 / 缺失，仍跳过重复下载；') + verified
            if ignored:state='已忽略 · '+state
            values = (f"{row['author']} / {row['tweet_id']} · {row['media_id']}", '原图' if row.get('media_type') == 'image' else row['quality'], state, f"{row['bytes'] / 1048576:.1f} MB" if row['state'] == 'completed' else '', '', row['error'] or (verified if row['state'] == 'completed' else ''))
            if self.tree.exists(key):
                old = self.tree.item(key, 'values')
                if row['state'] == 'downloading' and len(old) == 6:
                    values = (*values[:3], old[3], old[4], values[5])
                self.tree.item(key, values=values)
            else:
                self.tree.insert('', 0, iid=key, values=values)
        self.shell.render(rows, incremental=ids is not None)
        self.shell.refresh_archive_preview()
        if self.preview.detail is not None and self.preview.detail.winfo_exists() and self.preview.detail_job in {r["id"] for r in rows}:
            self.preview.show_detail(self.preview.detail_job)
        counts = self.shell.counts
        self.summary.set(f"共 {sum(counts.values())} 个媒体  ·  完成 {counts.get('completed', 0)}  ·  待下载 {counts.get('pending', 0)}  ·  暂停 {counts.get('paused', 0)}  ·  失败 {counts.get('failed', 0)}")

    def _poll(self):
        if self.closed:
            return
        changed = set()
        refresh_summary = False
        full_refresh = False
        for _ in range(300):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['type']
            if kind == 'changed':
                refresh_summary = True
                if 'id' in event:
                    changed.add(event['id'])
                elif not event.get('summary_only'):
                    full_refresh = True
            elif kind == 'preview':
                self.preview.receive(event)
            elif kind == 'message':
                self.status.set(event['text'])
                self.log.configure(state='normal')
                self.log.insert('end', time.strftime('%H:%M:%S ') + event['text'] + '\n')
                if int(self.log.index('end-1c').split('.')[0]) > 200:
                    self.log.delete('1.0', '50.0')
                self.log.see('end')
                self.log.configure(state='disabled')
            elif kind == 'progress':
                key = str(event['id'])
                done, total, now = event['done'], event['total'], event['time']
                previous = self.rates.get(key)
                speed = max(0, done - previous[0]) / max(.001, now - previous[1]) if previous else 0
                self.rates[key] = (done, now)
                self._file_progress(done, total, '下载 / 校验')
                self.shell.progress(event['id'], done, total, speed)
                if self.tree.exists(key):
                    self.tree.set(key, 'progress', f'{done / total:.1%} · {done / 1048576:.1f}/{total / 1048576:.1f} MB' if total else f'{done / 1048576:.1f} MB / 未知')
                    self.tree.set(key, 'speed', f'{speed / 1048576:.2f} MB/s' if previous else '测速中')
            elif kind == 'file_progress':
                self._file_progress(event['done'], event['total'], event.get('text', '文件'))
            elif kind == 'discovery_finished':
                self.discovering = False
            elif kind == 'operation_progress':
                done, total = event['done'], event['total']
                self.total_text.set(f"{event.get('text', '任务')} {done} / {total}" if not self.discovering else f'正在读取列表 · 已处理 {done} 个')
                if not self.discovering:
                    self.total_bar.stop()
                    self.total_bar.configure(mode='determinate', value=100 * done / total if total else 0)
            elif kind == 'finished':
                # ttk stop() resets the current value, even in determinate mode.
                for bar in (self.total_bar, self.file_bar):
                    value = bar['value'] if str(bar['mode']) == 'determinate' else 0
                    bar.stop()
                    bar.configure(mode='determinate', value=value)
                self.discovering = False
                self._busy(False)
                self.status.set(event.get('text') or ('已暂停，进度保留' if self.runner and self.runner.cancel.is_set() else '本轮结束；失败项可点继续未完成 / 重试'))
                refresh_summary = True
        if changed or refresh_summary or full_refresh:
            self._refresh(None if full_refresh else changed)
        self.preview.refresh_visible()
        self.shell.animate_media()
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self._dispose()
            return
        self.poll_id = self.root.after(150, self._poll)

    def _file_progress(self, done, total, text):
        self.file_bar.stop()
        if total:
            self.file_bar.configure(mode='determinate', value=min(100, 100 * done / total))
            self.file_text.set(f'{text} {done / total:.0%} · {done / 1048576:.1f}/{total / 1048576:.1f} MB')
        else:
            self.file_bar.configure(mode='indeterminate')
            self.file_bar.start(15)
            self.file_text.set(f'{text} {done / 1048576:.1f} MB · 总量未知')

    def close(self):
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.pause()
        else:
            self._dispose()

    def _dispose(self):
        self.closed = True
        self.root.after_cancel(self.poll_id)
        self.preview.close()
        while not self.events.empty():
            event = self.events.get_nowait()
            if event.get("image") is not None:
                event["image"].close()
        self.store.close()
        self.instance_lock.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        App(root)
    except Exception as exc:
        root.withdraw()
        messagebox.showerror('无法启动本地下载器', redact(exc), parent=root)
        root.destroy()
        return 1
    root.mainloop()
    return 0
