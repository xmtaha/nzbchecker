#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NZB Checker GUI - tkinter (stdlib, ek bağımlılık yok)

Tasarım: koyu tema, üst başlık çubuğu, sayaç kartları, sekmeli ayarlar.
Binlerce NZB için: satırlar partiler halinde eklenir, tarama arka planda çalışır.
"""

import logging
import os
import queue
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from nzb_checker.engine import ConfigError, NZBChecker, connect_to_server
from nzb_checker.engine.checker import iter_nzb_files
from nzb_checker import __version__
from nzb_checker.updater import UpdateError, check_for_update

from .config import (CONFIG_PATH, DEFAULTS, DEFAULT_PROVIDER, STATUS_TEXT,
                     VISIBLE_WHEN_FILTERED, read_config, write_config)
from .dialogs import open_provider_dialog, open_update_dialog
from .theme import (ACCENT, ACCENT_DARK, AMBER, BG, BORDER, GREEN, MUTED,
                    PARTIAL_THRESHOLD, RED, SPEC, STATUS_COLOR, SURFACE, SURFACE_2,
                    SURFACE_3, TEXT, PATH_FIELDS, CHECK_FIELDS, pick_font)
from .widgets import QueueLogHandler, provider_text

# tkdnd Tcl paketi (sürükle-bırak); AppImage'da da yanında taşınır
DND_DIR = Path(__file__).resolve().parent.parent.parent / 'tkdnd'

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('NZB Checker')
        self.geometry('1240x840')
        self.minsize(1000, 640)
        self.configure(bg=BG)

        self.ui_font = pick_font('Noto Sans', 'Cantarell', 'Open Sans', 'Liberation Sans')
        self.mono_font = pick_font('Noto Sans Mono', 'Adwaita Mono', 'Liberation Mono')
        self.option_add('*Font', (self.ui_font, 10))

        self.q = queue.Queue()
        self.rows = {}          # dosya adı -> {'iid':..., 'status':..., 'visible':...}
        self.order = []         # eklenme sırası
        self._sort_column = None  # başlığa tıklanınca sıralama sütunu
        self._sort_desc = False
        self._sort_pending = False
        self.counts = {'complete': 0, 'missing': 0, 'error': 0}
        self.listed = 0
        self.checker = None
        self.worker = None
        self.update_running = False
        self.update_dialog = None

        self.config_data = read_config()
        self.nzb_folder = str(self.config_data.get('paths', {}).get('nzb_folder') or '')
        self.vars = {}

        self._setup_style()
        self._build_header()
        self._build_cards()
        self._build_tabs()
        self._build_footer()

        logging.getLogger().addHandler(QueueLogHandler(self.q))
        self.bind('<F5>', lambda _e: self.start())
        self.bind('<Escape>', lambda _e: self.stop())
        self.bind('<Control-s>', lambda _e: self._save_clicked())
        self.after(120, self._drain)
        self.after(900, self._check_updates_async)

    # ------------------------------------------------------------- stil


    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')

        style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure('TNotebook.Tab', background=SURFACE, foreground=MUTED,
                        padding=(16, 8), borderwidth=0, font=(self.ui_font, 10))
        style.map('TNotebook.Tab',
                  background=[('selected', SURFACE_2)],
                  foreground=[('selected', TEXT)])

        style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, borderwidth=0, rowheight=26,
                        font=(self.ui_font, 10))
        style.configure('Treeview.Heading', background=SURFACE_2, foreground=MUTED,
                        relief='flat', padding=(8, 6), font=(self.ui_font, 9, 'bold'))
        style.map('Treeview.Heading', background=[('active', SURFACE_3)])
        style.map('Treeview',
                  background=[('selected', ACCENT_DARK)],
                  foreground=[('selected', '#ffffff')])

        style.configure('Accent.Horizontal.TProgressbar', background=ACCENT,
                        troughcolor=SURFACE_2, bordercolor=SURFACE_2,
                        lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
        style.configure('Vertical.TScrollbar', background=SURFACE_3, troughcolor=SURFACE,
                        bordercolor=SURFACE, arrowcolor=MUTED, borderwidth=0,
                        width=12, arrowsize=11)
        style.map('Vertical.TScrollbar', background=[('active', '#3c4452')])
        # oksuz (sade) kaydirma cubugu
        for bar in ('Vertical', 'Horizontal'):
            style.layout(f'{bar}.TScrollbar', [
                (f'{bar}.Scrollbar.trough', {'children': [
                    (f'{bar}.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})],
                    'sticky': 'ns' if bar == 'Vertical' else 'we'})])

        style.configure('TCombobox', fieldbackground=SURFACE_2, background=SURFACE_3,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=SURFACE_2, darkcolor=SURFACE_2,
                        selectbackground=SURFACE_2, selectforeground=TEXT,
                        padding=(8, 5), borderwidth=0)
        style.map('TCombobox',
                  fieldbackground=[('readonly', SURFACE_2)],
                  foreground=[('readonly', TEXT)],
                  bordercolor=[('focus', ACCENT)],
                  arrowcolor=[('active', TEXT)])
        # açılır liste (listbox) koyu tema
        for option, value in (('*TCombobox*Listbox.background', SURFACE_2),
                              ('*TCombobox*Listbox.foreground', TEXT),
                              ('*TCombobox*Listbox.selectBackground', ACCENT_DARK),
                              ('*TCombobox*Listbox.selectForeground', '#ffffff')):
            self.option_add(option, value)

    def _button(self, parent, text, command, primary=False, width=None):
        return tk.Button(
            parent, text=text, command=command,
            bg=ACCENT if primary else SURFACE_3,
            fg='#ffffff' if primary else TEXT,
            activebackground=ACCENT_DARK if primary else SURFACE_2,
            activeforeground='#ffffff' if primary else TEXT,
            relief='flat', bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            padx=14, pady=7, cursor='hand2',
            font=(self.ui_font, 10, 'bold' if primary else 'normal'),
            width=width)



    def _build_header(self):
        header = tk.Frame(self, bg=SURFACE, padx=18, pady=14)
        header.pack(fill='x')

        left = tk.Frame(header, bg=SURFACE)
        left.pack(side='left')
        tk.Label(left, text='NZB Checker', bg=SURFACE, fg=TEXT,
                 font=(self.ui_font, 16, 'bold')).pack(anchor='w')
        self.status_label = tk.Label(left, text='●  Hazır', bg=SURFACE, fg=MUTED,
                                     font=(self.ui_font, 9))
        self.status_label.pack(anchor='w', pady=(2, 0))

        right = tk.Frame(header, bg=SURFACE)
        right.pack(side='right')
        self.stop_btn = self._button(right, 'Durdur', self.stop)
        self.stop_btn.pack(side='right', padx=(8, 0))
        self.stop_btn.config(state='disabled', disabledforeground='#5b6270')
        self.start_btn = self._button(right, '▶  Başlat', self.start, primary=True)
        self.start_btn.pack(side='right')


    def _build_cards(self):
        row = tk.Frame(self, bg=BG, padx=14, pady=12)
        row.pack(fill='x')

        self.card_values = {}
        for key, title, color in (('total', 'Listelenen', TEXT),
                                  ('complete', 'Tam', GREEN),
                                  ('missing', 'Eksik', AMBER),
                                  ('error', 'Hatalı', RED),
                                  ('pending', 'Bekleyen', MUTED)):
            card = tk.Frame(row, bg=SURFACE, highlightthickness=1,
                            highlightbackground=BORDER)
            card.pack(side='left', fill='x', expand=True, padx=(0, 10) if key != 'pending' else 0)
            value = tk.Label(card, text='0', bg=SURFACE, fg=color,
                             font=(self.ui_font, 22, 'bold'))
            value.pack(anchor='w', padx=14, pady=(10, 0))
            tk.Label(card, text=title.upper(), bg=SURFACE, fg=MUTED,
                     font=(self.ui_font, 8, 'bold')).pack(anchor='w', padx=14, pady=(0, 10))
            self.card_values[key] = value


    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=14, pady=(0, 8))

        self.files_tab = tk.Frame(self.notebook, bg=SURFACE)
        self.settings_tab = tk.Frame(self.notebook, bg=SURFACE)
        self.notebook.add(self.files_tab, text='Dosyalar')
        self.notebook.add(self.settings_tab, text='Ayarlar')

        self._build_files()
        self._build_settings()


    def _build_files(self):
        drop = tk.Frame(self.files_tab, bg=SURFACE_2, padx=16, pady=12,
                        highlightthickness=2, highlightbackground=BORDER)
        drop.pack(fill='x', padx=12, pady=(12, 0))
        self.drop_zone = drop

        self.drop_title = tk.Label(drop, text='NZB klasörünü buraya sürükleyin',
                                   bg=SURFACE_2, fg=TEXT, font=(self.ui_font, 12, 'bold'))
        self.drop_title.pack(anchor='w')
        self.drop_path = tk.Label(
            drop, text=self.nzb_folder or 'Henüz klasör seçilmedi (sürükleyin veya seçin)',
            bg=SURFACE_2, fg=TEXT if self.nzb_folder else MUTED, font=(self.ui_font, 9))
        self.drop_path.pack(anchor='w', pady=(2, 10))

        row = tk.Frame(drop, bg=SURFACE_2)
        row.pack(anchor='w')
        self._button(row, 'Klasör seç', self.choose_folder).pack(side='left')
        self._button(row, 'Klasörü tara', self.scan_folder).pack(side='left', padx=(8, 0))

        self._setup_dnd(drop, self.drop_title, self.drop_path, row)

        bar = tk.Frame(self.files_tab, bg=SURFACE, padx=12, pady=10)
        bar.pack(fill='x')

        self.only_problems = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text='Sadece eksik/hatalı', variable=self.only_problems,
                       command=self._apply_filter, bg=SURFACE, fg=TEXT,
                       activebackground=SURFACE, activeforeground=TEXT,
                       selectcolor=SURFACE_2, bd=0, highlightthickness=0,
                       font=(self.ui_font, 10)).pack(side='left', padx=12)

        tk.Label(bar, text='Ara', bg=SURFACE, fg=MUTED,
                 font=(self.ui_font, 9)).pack(side='left')
        self.partial_missing = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text='Bazı sağlayıcılarda eksik', variable=self.partial_missing,
                       command=self._apply_filter, bg=SURFACE, fg=TEXT,
                       activebackground=SURFACE, activeforeground=TEXT,
                       selectcolor=SURFACE_2, bd=0, highlightthickness=0,
                       font=(self.ui_font, 9)).pack(side='left', padx=(18, 0))

        self.search = tk.StringVar()
        entry = tk.Entry(bar, textvariable=self.search, width=22, bg=SURFACE_2, fg=TEXT,
                         insertbackground=TEXT, relief='flat', highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT,
                         font=(self.ui_font, 10))
        entry.pack(side='left', padx=(6, 0), ipady=4)
        entry.bind('<KeyRelease>', lambda _e: self._apply_filter())

        self._button(bar, 'Raporu dışa aktar', self.export_report).pack(
            side='left', padx=(12, 0))

        self.count_label = tk.Label(bar, text='0 dosya', bg=SURFACE, fg=MUTED,
                                    font=(self.ui_font, 9))
        self.count_label.pack(side='right')

        body = tk.Frame(self.files_tab, bg=SURFACE)
        body.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        columns = ('name', 'status', 'segments', 'checked', 'missing', 'percent',
                   'providers', 'elapsed')
        self.tree = ttk.Treeview(body, columns=columns, show='headings', selectmode='extended')
        for column, title, width, anchor in (
                ('name', 'Dosya', 340, 'w'),
                ('status', 'Durum', 110, 'w'),
                ('segments', 'Segment', 90, 'e'),
                ('checked', 'Kontrol', 90, 'e'),
                ('missing', 'Eksik', 90, 'e'),
                ('percent', 'Eksik %', 90, 'e'),
                ('providers', 'Sağlayıcılar', 300, 'w'),
                ('elapsed', 'Süre', 90, 'e')):
            self.tree.heading(column, text=title, anchor=anchor,
                              command=lambda c=column: self._sort_by(c))
            self.tree.column(column, width=width, anchor=anchor,
                             stretch=(column == 'name'))

        self.tree.tag_configure('odd', background=SURFACE_2)
        self.tree.tag_configure('even', background=SURFACE)
        for key, color in STATUS_COLOR.items():
            self.tree.tag_configure(key, foreground=color)

        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)


    def _build_settings(self):
        wrap = tk.Frame(self.settings_tab, bg=SURFACE, padx=12, pady=12)
        wrap.pack(fill='both', expand=True)
        for column in range(2):
            wrap.columnconfigure(column, weight=1, uniform='settings')

        self._build_providers(wrap)
        self._field_group(wrap, 'Klasörler', 1, PATH_FIELDS)
        self._field_group(wrap, 'Kontrol', 2, CHECK_FIELDS)

        footer = tk.Frame(self.settings_tab, bg=SURFACE, padx=12)
        footer.pack(fill='x', pady=(0, 12))
        self._button(footer, 'Ayarları kaydet', self._save_clicked, primary=True).pack(side='left')
        self._button(footer, 'Güncellemeleri kontrol et',
                     lambda: self._check_updates_async(force=True)).pack(
            side='left', padx=(8, 0))
        self.save_hint = tk.Label(footer, text='Değişiklikler config.json dosyasına yazılır',
                                  bg=SURFACE, fg=MUTED, font=(self.ui_font, 9))
        self.save_hint.pack(side='left', padx=10)

        tk.Label(footer, text=f'v{__version__}  ·  F5 başlat  ·  Esc durdur  ·  Ctrl+S kaydet',
                 bg=SURFACE, fg=MUTED, font=(self.ui_font, 9)).pack(side='right')


    def _build_providers(self, parent):
        """Provider listesi + ekle/düzenle/sil/test butonları"""
        group = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER, padx=14, pady=12)
        group.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        group.columnconfigure(0, weight=1)

        head = tk.Frame(group, bg=SURFACE)
        head.grid(row=0, column=0, sticky='ew')
        tk.Label(head, text='Usenet sağlayıcıları', bg=SURFACE, fg=TEXT,
                 font=(self.ui_font, 11, 'bold')).pack(side='left')
        self._button(head, '+ Ekle', self.add_provider).pack(side='right')

        self.provider_tree = ttk.Treeview(
            group, columns=('name', 'host', 'conn', 'ssl'), show='headings',
            height=6, selectmode='browse')
        for column, title, width, anchor in (('name', 'Ad', 150, 'w'),
                                             ('host', 'Sunucu', 210, 'w'),
                                             ('conn', 'Bağlantı', 70, 'center'),
                                             ('ssl', 'SSL', 50, 'center')):
            self.provider_tree.heading(column, text=title, anchor=anchor)
            self.provider_tree.column(column, width=width, anchor=anchor,
                                      stretch=(column == 'host'))
        self.provider_tree.tag_configure('odd', background=SURFACE_2)
        self.provider_tree.grid(row=1, column=0, sticky='nsew', pady=(10, 8))
        group.rowconfigure(1, weight=1)
        self.provider_tree.bind('<Double-1>', lambda _e: self.edit_provider())
        self.provider_tree.bind('<Return>', lambda _e: self.edit_provider())

        actions = tk.Frame(group, bg=SURFACE)
        actions.grid(row=2, column=0, sticky='w')
        self._button(actions, 'Düzenle', self.edit_provider).pack(side='left')
        self._button(actions, 'Sil', self.remove_provider).pack(side='left', padx=(8, 0))
        self._button(actions, 'Bağlantıyı test et', self.test_providers).pack(
            side='left', padx=(8, 0))

        self._refresh_providers()

    def _refresh_providers(self):
        tree = self.provider_tree
        tree.delete(*tree.get_children())
        for position, provider in enumerate(self.config_data.get('providers') or []):
            tree.insert('', 'end', iid=str(position),
                        values=(provider.get('name') or provider.get('host') or '-',
                                f"{provider.get('host') or '-'}:{provider.get('port')}",
                                provider.get('connections'),
                                'açık' if provider.get('use_ssl') else 'kapalı'),
                        tags=('odd',) if position % 2 else ())

    def _selected_provider(self):
        selection = self.provider_tree.selection()
        if not selection:
            messagebox.showinfo('Sağlayıcı', 'Önce listeden bir sağlayıcı seçin.')
            return None
        return int(selection[0])

    def add_provider(self):
        providers = self.config_data.setdefault('providers', [])
        provider = dict(DEFAULT_PROVIDER)
        provider['name'] = f'Sağlayıcı {len(providers) + 1}'
        provider = self._provider_dialog(provider, 'Sağlayıcı ekle')
        if provider:
            providers.append(provider)
            self._refresh_providers()
            self.provider_tree.selection_set(str(len(providers) - 1))

    def edit_provider(self):
        index = self._selected_provider()
        if index is None:
            return
        providers = self.config_data['providers']
        provider = self._provider_dialog(dict(providers[index]), 'Sağlayıcıyı düzenle')
        if provider:
            providers[index] = provider
            self._refresh_providers()
            self.provider_tree.selection_set(str(index))

    def remove_provider(self):
        index = self._selected_provider()
        if index is None:
            return
        providers = self.config_data['providers']
        if len(providers) <= 1:
            messagebox.showinfo('Sağlayıcı', 'En az bir sağlayıcı kalmalı.')
            return
        name = providers[index].get('name') or 'Sağlayıcı'
        if messagebox.askyesno('Sağlayıcı', f"'{name}' silinsin mi?"):
            del providers[index]
            self._refresh_providers()

    def _provider_dialog(self, provider, title):
        """Sağlayıcı ekleme/düzenleme penceresi (modal)"""
        return open_provider_dialog(self, provider, title)

    def _test_provider_async(self, provider, label):
        """Bağlantıyı arka planda dener, sonucu etikete yazar"""
        label.config(text='Bağlanılıyor…', fg=MUTED)

        def worker():
            started = time.time()
            try:
                server = connect_to_server(provider)
                try:
                    server.quit()
                except Exception:
                    pass
                text, color = 'Bağlantı başarılı', GREEN
            except Exception as exc:
                text, color = f'Bağlantı başarısız: {exc}', RED
            elapsed = time.time() - started
            self.q.put(('provider_test', {'label': label,
                                          'text': f'{text} ({elapsed:.1f} sn)',
                                          'color': color}))

        threading.Thread(target=worker, daemon=True).start()

    def test_providers(self):
        """Listedeki tüm sağlayıcıları dener, sonucu satırın altındaki etikete yazar"""
        providers = self.config_data.get('providers') or []
        if not providers:
            return
        self._log('Sağlayıcı bağlantıları test ediliyor…', 'INFO')
        for provider in providers:
            self._test_provider_async(provider, self.save_hint)


    def _field_group(self, parent, title, column, fields):
        group = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER, padx=14, pady=12)
        group.grid(row=0, column=column, sticky='nsew',
                   padx=(0, 12) if column < 2 else 0)

        tk.Label(group, text=title, bg=SURFACE, fg=TEXT,
                 font=(self.ui_font, 11, 'bold')).grid(row=0, column=0, columnspan=3,
                                                       sticky='w', pady=(0, 10))
        group.columnconfigure(1, weight=1)

        for index, (section, key, label, kind) in enumerate(fields, start=1):
            value = self.config_data.get(section, {}).get(key, DEFAULTS.get(section, {}).get(key))

            if kind is bool:
                var = tk.BooleanVar(value=bool(value))
                widget = tk.Checkbutton(group, variable=var, bg=SURFACE,
                                        activebackground=SURFACE, selectcolor=SURFACE_2,
                                        bd=0, highlightthickness=0)
            elif isinstance(kind, tuple):
                var = tk.StringVar(value=str(value))
                widget = ttk.Combobox(group, textvariable=var, values=list(kind),
                                      width=12, state='readonly')
            else:
                var = tk.StringVar(value='' if value is None else str(value))
                widget = tk.Entry(group, textvariable=var, bg=SURFACE_2, fg=TEXT,
                                  insertbackground=TEXT, relief='flat', highlightthickness=1,
                                  highlightbackground=BORDER, highlightcolor=ACCENT,
                                  show='*' if kind == 'secret' else '',
                                  font=(self.ui_font, 10))

            tk.Label(group, text=label, bg=SURFACE, fg=MUTED,
                     font=(self.ui_font, 9)).grid(row=index, column=0, sticky='w',
                                                  padx=(0, 10), pady=4)
            widget.grid(row=index, column=1, sticky='w' if kind is bool else 'we',
                        pady=4, ipady=0 if kind is bool else 4)

            if kind == 'dir':
                self._button(group, '…', lambda v=var: self._pick_dir(v)).grid(
                    row=index, column=2, padx=(6, 0))

            self.vars[key] = var


    def _build_footer(self):
        footer = tk.Frame(self, bg=BG, padx=14)
        footer.pack(fill='x', pady=(0, 12))

        self.progress = ttk.Progressbar(footer, mode='determinate',
                                        style='Accent.Horizontal.TProgressbar')
        self.progress.pack(fill='x', pady=(0, 6))

        self.summary_label = tk.Label(footer, text='Hazır', bg=BG, fg=MUTED,
                                      font=(self.ui_font, 9))
        self.summary_label.pack(anchor='w', pady=(0, 6))

        log_box = tk.Frame(footer, bg=SURFACE, highlightthickness=1,
                           highlightbackground=BORDER)
        log_box.pack(fill='both', expand=True)
        self.log_text = tk.Text(log_box, height=6, wrap='none', state='disabled',
                                bg=SURFACE, fg=MUTED, relief='flat', bd=0,
                                insertbackground=TEXT, padx=10, pady=8,
                                font=(self.mono_font, 9))
        scroll = ttk.Scrollbar(log_box, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.log_text.pack(fill='both', expand=True)
        self.log_text.tag_configure('ERROR', foreground=RED)
        self.log_text.tag_configure('WARNING', foreground=AMBER)
        self.log_text.tag_configure('FATAL', foreground=RED)


    # ------------------------------------------------------------- ayarlar

    def _provider_values(self):
        """Listedeki provider'ları config biçiminde döndürür"""
        providers = self.config_data.get('providers') or []
        if not providers:
            raise ValueError('En az bir sağlayıcı gerekli')
        return [dict(provider) for provider in providers]

    def _pick_dir(self, var):
        folder = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if folder:
            var.set(folder)

    def _save_config(self):
        data = read_config()
        data['providers'] = self._provider_values()
        for section, key, _label, kind in SPEC:
            raw = self.vars[key].get()
            if kind is bool:
                raw = bool(raw)
            elif kind is int:
                raw = int(float(str(raw).strip()))
            elif kind is float:
                raw = float(str(raw).strip().replace(',', '.'))
            else:
                raw = str(raw).strip()
            data.setdefault(section, {})[key] = raw
        if self.nzb_folder:
            data.setdefault('paths', {})['nzb_folder'] = self.nzb_folder
        write_config(data)
        self.config_data = data

    def _save_clicked(self):
        try:
            self._save_config()
        except Exception as e:
            messagebox.showerror('Ayarlar', f'Kaydedilemedi: {e}')
            return
        self.save_hint.config(text=f'Kaydedildi: {CONFIG_PATH}', fg=GREEN)
        self.summary_label.config(text=f'Ayarlar kaydedildi: {CONFIG_PATH}')

    # ------------------------------------------------------------- güncelleme

    UPDATE_INTERVAL = 20 * 3600  # saniye: otomatik kontrol sıklığı

    def _running_appimage(self) -> str:
        """AppImage içinden çalışıyorsa dosya yolu, değilse boş"""
        return os.environ.get('APPIMAGE') or ''

    def _check_updates_async(self, force: bool = False):
        """Release kontrolünü arka planda başlat (ağ beklemesi arayüzü durdurmaz)"""
        if self.update_running:
            if force:
                self._log('INFO', 'Sürüm kontrolü zaten çalışıyor')
            return
        if self.update_dialog is not None and force:
            self._log('INFO', 'Güncelleme penceresi zaten açık')
            return
        settings = self.config_data.get('settings') or {}
        if not force:
            if not settings.get('check_updates', True):
                return
            try:
                last = float(settings.get('update_last_check') or 0)
            except (TypeError, ValueError):
                last = 0.0
            if time.time() - last < self.UPDATE_INTERVAL:
                return
        if force:
            self.summary_label.config(text='Sürüm kontrol ediliyor…')
        self.update_running = True
        threading.Thread(target=self._update_worker, args=(force,), daemon=True).start()

    def _update_worker(self, force: bool):
        try:
            release = check_for_update()
        except UpdateError as exc:
            self.q.put(('update_done', {'error': str(exc), 'forced': force}))
            return
        except Exception as exc:  # beklenmeyen hata kilidi bırakmasın
            self.q.put(('update_done', {'error': str(exc), 'forced': force}))
            return
        self.q.put(('update_done', {'release': release, 'forced': force}))

    def _record_update_state(self, key: str, value):
        """Güncelleme durumunu (son kontrol / atlanan sürüm) config'e yaz"""
        data = read_config()
        data.setdefault('settings', {})[key] = value
        try:
            write_config(data)
        except Exception as exc:
            logging.getLogger(__name__).debug('Güncelleme durumu yazılamadı: %s', exc)

    def _show_update(self, release: dict):
        settings = self.config_data.get('settings') or {}
        version = release.get('version') or ''
        if version and version == (settings.get('update_skipped_version') or ''):
            return
        if self.update_dialog is not None:
            return
        self._log('INFO', f"Yeni sürüm mevcut: v{version} (şu an v{__version__})")
        path = self._running_appimage()
        # bloklamayan pencere: tarama olayları akmaya devam eder
        self.update_dialog = open_update_dialog(
            self, release,
            running_name=Path(path).name if path else '',
            running_path=path,
            on_close=self._on_update_dialog_closed)

    def _on_update_dialog_closed(self, result: dict):
        self.update_dialog = None
        if not result.get('skip'):
            return
        version = result.get('version') or ''
        self._record_update_state('update_skipped_version', version)
        self.config_data.setdefault('settings', {})['update_skipped_version'] = version

    # ------------------------------------------------------------- satır işlemleri


    def _clear_rows(self):
        self.tree.delete(*self.tree.get_children(''))
        self.rows.clear()
        self.order.clear()
        self.counts = {'complete': 0, 'missing': 0, 'error': 0}
        self.listed = 0
        self.progress['value'] = 0
        self.summary_label.config(text='Hazır')
        self.count_label.config(text='0 dosya')
        self._refresh_counts()

    def _upsert(self, name, status, **fields):
        """Satırı ekler/günceller. Yalnızca verilen alanlar değişir; böylece
        önce yazılan segment/süre değerleri sonraki güncellemede sıfırlanmaz."""
        entry = self.rows.get(name)
        if entry is None:
            stripe = 'odd' if len(self.order) % 2 else 'even'
            iid = self.tree.insert('', 'end', tags=(stripe, status))
            entry = {'iid': iid, 'status': status, 'visible': True, 'stripe': stripe,
                     'segments': 0, 'checked': 0, 'missing': 0, 'percent': None,
                     'providers': [], 'elapsed': 0.0, 'checked_all': True}
            self.rows[name] = entry
            self.order.append(name)

        entry['status'] = status
        if 'providers' in fields:
            entry['providers'] = fields['providers'] or []
        if fields.get('checked_all') is not None:
            entry['checked_all'] = bool(fields['checked_all'])
        for key in ('segments', 'checked', 'missing', 'percent', 'elapsed'):
            if key in fields and fields[key] is not None:
                entry[key] = fields[key]

        label = STATUS_TEXT.get(status, status)
        if status == 'complete' and not entry['checked_all']:
            label += ' (örnek)'
        values = (name, label, entry['segments'],
                  entry['checked'], entry['missing'],
                  f"%{entry['percent']:.1f}" if entry['percent'] else '',
                  provider_text(entry['providers']),
                  f"{entry['elapsed']:.1f}s" if entry['elapsed'] else '')
        self.tree.item(entry['iid'], values=values, tags=(entry['stripe'], status))
        self._show(name, self._row_wanted(name))

    def _row_wanted(self, name):
        entry = self.rows[name]
        providers = entry.get('providers') or []
        partial = (entry['status'] == 'complete'
                   and any((p.get('percent') or 0) >= PARTIAL_THRESHOLD
                           for p in providers))
        entry['partial'] = partial
        if self.only_problems.get() and entry['status'] not in VISIBLE_WHEN_FILTERED:
            return False
        if self.partial_missing.get() and not partial:
            return False
        text = self.search.get().strip().lower()
        return not text or text in name.lower()

    def _show(self, name, wanted):
        """Filtre durumunu önbellekteki bayrakla karşılaştır, gerekirse değiştir"""
        entry = self.rows[name]
        if wanted == entry['visible']:
            return
        if wanted:
            self.tree.move(entry['iid'], '', 'end')
            # sıralama açıksa satırlar eklenirken sıra bozulur; topluca düzelt
            if self._sort_column and not self._sort_pending:
                self._sort_pending = True
                self.after(200, self._sort_tick)
        else:
            self.tree.detach(entry['iid'])
        entry['visible'] = wanted

    def _sort_tick(self):
        self._sort_pending = False
        self._apply_sort()

    def _sort_by(self, column):
        """Başlığa tıklayınca sırala; aynı sütunda ikinci tıklama yönü çevirir"""
        descending = self._sort_column == column and not self._sort_desc
        self._sort_column, self._sort_desc = column, descending
        self._apply_sort()

    def _apply_sort(self):
        """Görünen satırları seçili sütuna göre yeniden dizer"""
        column = self._sort_column
        if not column:
            return
        if column == 'name':
            key = lambda name: name.lower()
        elif column == 'providers':
            key = lambda name: len(self.rows[name].get('providers') or [])
        elif column == 'status':
            key = lambda name: STATUS_TEXT.get(self.rows[name]['status'],
                                               self.rows[name]['status'])
        else:
            key = lambda name: self.rows[name].get(column) or 0

        # yalnızca görünen satırları taşı; filtreden gizlenenler detached kalır
        for name in sorted(self.order, key=key, reverse=self._sort_desc):
            entry = self.rows[name]
            if entry['visible']:
                self.tree.move(entry['iid'], '', 'end')

    def _apply_filter(self):
        for name in self.order:
            self._show(name, self._row_wanted(name))
        self._apply_sort()

    def _refresh_counts(self):
        done = self.counts['complete'] + self.counts['missing'] + self.counts['error']
        values = {'total': self.listed, 'complete': self.counts['complete'],
                  'missing': self.counts['missing'], 'error': self.counts['error'],
                  'pending': max(0, self.listed - done) if self.listed else 0}
        for key, value in values.items():
            self.card_values[key].config(text=str(value))

    def _set_status(self, text, color):
        self.status_label.config(text=f'●  {text}', fg=color)

    # ------------------------------------------------------------- tarama

    # ---------------------------------------------------- sürükle-bırak (tkdnd)

    def _setup_dnd(self, *widgets):
        """
        Klasör sürükle-bırak desteği.

        tkdnd Tcl paketi beklenir (tkdnd/ klasörü); yoksa sessizce atlanır,
        kullanıcı "Klasör seç" düğmesini kullanmaya devam eder.
        """
        try:
            self.tk.eval(f'lappend auto_path {{{DND_DIR}}}')
            self.tk.eval('package require tkdnd')
        except tk.TclError as exc:
            self.dnd_ready = False
            logging.getLogger(__name__).debug('tkdnd yüklenemedi: %s', exc)
            return

        self.dnd_ready = True
        for widget in widgets:
            self.tk.call('tkdnd::drop_target', 'register', widget._w, 'DND_Files')
            widget.bind('<<Drop>>', self._on_drop, add='+')
            widget.bind('<<DropEnter>>', lambda _e: self.drop_zone.config(
                highlightbackground=ACCENT), add='+')
            widget.bind('<<DropLeave>>', lambda _e: self.drop_zone.config(
                highlightbackground=BORDER), add='+')

    def _on_drop(self, event):
        """Bırakılan dosya/klasör yolunu al -> klasör olarak ayarla"""
        self.drop_zone.config(highlightbackground=BORDER)
        paths = [p for p in self.tk.splitlist(event.data) if p]
        if not paths:
            return
        self.set_folder(paths[0], scan=True)

    def choose_folder(self):
        """Klasör seç ve tara"""
        folder = filedialog.askdirectory(initialdir=self.nzb_folder or str(Path.home()),
                                         title='NZB klasörü seç')
        if folder:
            self.set_folder(folder, scan=True)

    def set_folder(self, folder, scan=False):
        """
        NZB klasörünü ayarla (sürükle-bırak veya seçim).

        Sürükle-bırak bir dosya yolu da verebilir; o zaman üst klasörü kullanılır.
        """
        path = Path(str(folder).strip().strip('{}').strip()).expanduser()
        if path.is_file():
            path = path.parent
        if not path.is_dir():
            messagebox.showerror('Klasör', f'Klasör bulunamadı: {path}')
            return False
        self.nzb_folder = str(path)
        self.drop_path.config(text=self.nzb_folder, fg=TEXT)
        if scan:
            self.scan_folder()
        return True

    def scan_folder(self):
        folder = Path(self.nzb_folder) if self.nzb_folder else None
        if not folder or not folder.is_dir():
            self.choose_folder()
            return

        paths = self.config_data.get('paths', {})
        skip = [folder / paths[key] for key in
                ('complete_folder', 'missing_folder', 'error_folder')
                if paths.get(key)]
        root = folder.resolve()
        files = sorted(
            str(p.name if p.parent.resolve() == root else p.relative_to(folder))
            for p in iter_nzb_files(folder, skip))
        self._clear_rows()

        def fill(index=0):
            for name in files[index:index + 500]:
                self._upsert(name, 'queued')
            if index + 500 < len(files):
                self.after(1, fill, index + 500)
            else:
                self.listed = len(files)
                self.count_label.config(text=f'{len(files)} dosya')
                self.summary_label.config(text=f'{len(files)} NZB listelendi')
                self._refresh_counts()

        fill()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            self._save_config()
        except Exception as e:
            messagebox.showerror('Ayarlar', f'Ayarlar kaydedilemedi: {e}')
            return

        self._clear_rows()
        self._scan_started_at = None
        self._scan_checked = 0
        self._scan_total_to_check = 0
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal', fg=TEXT)
        self.summary_label.config(text='Tarama başlıyor…')
        self._set_status('Çalışıyor', ACCENT)
        self.notebook.select(0)

        self.checker = None
        try:
            self.checker = NZBChecker(str(CONFIG_PATH), events=self._on_event)
        except ConfigError as exc:
            messagebox.showerror('Ayarlar', str(exc))
            self.start_btn.config(state='normal')
            self.stop_btn.config(state='disabled', fg='#5b6270')
            self.summary_label.config(text='Tarama başlatılamadı')
            self._set_status('Hazır', MUTED)
            return
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def stop(self):
        if self.checker:
            self.checker.cancel()
            self.summary_label.config(text='Durduruluyor…')
            self._set_status('Durduruluyor', AMBER)

    def export_report(self):
        """Son taramanın raporunu (CSV + eksik segment listeleri) dışa aktar"""
        checker = self.checker
        if not checker or not getattr(checker, 'results', None):
            messagebox.showinfo('Rapor', 'Önce bir tarama çalıştırın.')
            return
        folder = filedialog.askdirectory(
            initialdir=self.nzb_folder or str(Path.home()),
            title='Rapor klasörü seç')
        if not folder:
            return
        try:
            info = checker.export_report(Path(folder))
        except Exception as exc:
            messagebox.showerror('Rapor', f'Rapor yazılamadı: {exc}')
            return
        self._log('INFO', f"Rapor yazıldı: {info['csv']} "
                          f"({info['files']} dosya, {info['missing_files']} eksik dosya, "
                          f"{info['missing_ids']} eksik segment)")
        self.summary_label.config(text=f"Rapor: {info['folder']}")

    def _run(self):
        try:
            self.checker.process_nzb_files()
        except Exception as e:
            self.q.put(('fatal', {'message': str(e)}))
        finally:
            self.q.put(('worker_done', {}))

    def _on_event(self, kind, **data):
        """Engine worker thread'lerinden çağrılır - sadece kuyruğa yaz"""
        self.q.put((kind, data))

    # ------------------------------------------------------------- olay döngüsü

    def _drain(self):
        try:
            for _ in range(3000):
                kind, data = self.q.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _handle(self, kind, data):
        if kind == 'scan_start':
            self.progress['maximum'] = max(1, data.get('total') or 1)
            self.progress['value'] = 0
            self._scan_started_at = data.get('started') or time.time()
            self._scan_checked = 0
            self._scan_total_to_check = 0

        elif kind == 'file_start':
            to_check = data.get('to_check') or 0
            self._scan_total_to_check = getattr(self, '_scan_total_to_check', 0) + to_check
            self._upsert(data['name'], 'running', segments=data.get('segments', 0),
                         checked=0, missing=0)
            self._set_status('Kontrol ediliyor', ACCENT)

        elif kind == 'file_progress':
            to_check = data.get('to_check') or 0
            checked = data.get('checked') or 0
            self._upsert(data['name'], 'running', checked=checked,
                         segments=data.get('segments'))
            percent = 100.0 * checked / to_check if to_check else 0.0
            scan_checked = data.get('scan_checked')
            if scan_checked is not None:
                self._scan_checked = scan_checked
            elapsed = data.get('elapsed') or 0.0
            speed = (self._scan_checked / elapsed) if elapsed > 0 else 0.0
            parts = [f'%{percent:.0f}']
            if speed > 0:
                parts.append(f'{speed:.1f} seg/sn')
                remaining = max(0, getattr(self, '_scan_total_to_check', 0) - self._scan_checked)
                if remaining > 0:
                    eta = int(remaining / speed)
                    m, s = divmod(eta, 60)
                    h, m = divmod(m, 60)
                    parts.append(f'ETA {h:d}:{m:02d}:{s:02d}' if h else f'ETA {m:d}:{s:02d}')
            self._set_status('Kontrol ediliyor — ' + ' · '.join(parts), ACCENT)

        elif kind == 'file_done':
            status = data.get('status', 'error')
            providers = data.get('providers') or []
            self._upsert(data['name'], status, segments=data.get('segments', 0),
                         checked=data.get('checked', 0), missing=data.get('missing', 0),
                         elapsed=data.get('elapsed', 0.0),
                         percent=data.get('percent') or None,
                         providers=providers,
                         checked_all=data.get('checked_all'))
            if data.get('moved') is False and status != 'cancelled':
                self._log('WARNING', f"{data['name']}: taşınamadı, yerinde bırakıldı")
            if len(providers) > 1:
                level = 'INFO' if status == 'complete' else 'WARNING'
                self._log(level, f"{data['name']} -> {provider_text(providers, True)}")
            if status in self.counts:
                self.counts[status] += 1
            self._refresh_counts()

        elif kind == 'file_error':
            self._log('ERROR', f"{data.get('name')}: {data.get('message')}")

        elif kind == 'progress':
            self.progress['value'] = data.get('completed', 0)
            self.summary_label.config(
                text=f"{data.get('completed', 0)}/{data.get('total', 0)} dosya işlendi")

        elif kind == 'scan_end':
            self.progress['value'] = self.progress['maximum']
            error = data.get('error')
            if error:
                self._log('ERROR', error)
                self.summary_label.config(text=error)
                self._set_status('Hata', RED)
                self.start_btn.config(state='normal')
                self.stop_btn.config(state='disabled', fg='#5b6270')
                messagebox.showerror('Tarama', error)
                return
            cancelled = data.get('cancelled')
            moved_failed = data.get('move_failed') or 0
            elapsed = data.get('elapsed')
            missing_segs = data.get('missing_segments')
            prefix = 'Durduruldu' if cancelled else 'Bitti'
            if elapsed is not None:
                prefix += f" — {elapsed} sn"
            summary = (f"{prefix} — Tam: {data.get('complete', 0)}, "
                       f"Eksik: {data.get('missing', 0)}")
            if missing_segs is not None:
                summary += f" ({missing_segs} segment)"
            summary += f", Hatalı: {data.get('error', 0)}"
            if moved_failed:
                summary += f", Taşınamayan: {moved_failed} (kaynakta kaldı)"
                self._log('WARNING', f'{moved_failed} dosya hedef klasöre taşınamadı (kaynakta bırakıldı)')
            provider_stats = data.get('provider_stats') or []
            if provider_stats:
                parts = []
                for p in provider_stats:
                    part = f"{p['name']} {p.get('stat', 0)} STAT, {p.get('cache', 0)} önbellek"
                    if p.get('errors'):
                        part += f", {p['errors']} hata"
                    if not p.get('active', True):
                        part += " (erişilemedi)"
                    parts.append(part)
                self._log('INFO', 'Sağlayıcı istekleri: ' + '; '.join(parts))
            self.summary_label.config(text=summary)
            self._set_status('Durduruldu' if cancelled else 'Bitti', AMBER if cancelled else GREEN)
            self.start_btn.config(state='normal')
            self.stop_btn.config(state='disabled', fg='#5b6270')

        elif kind == 'fatal':
            self._log('FATAL', data.get('message', ''))
            self._set_status('Hata', RED)
            messagebox.showerror('Hata', data.get('message', 'Bilinmeyen hata'))

        elif kind == 'worker_done':
            self.start_btn.config(state='normal')
            self.stop_btn.config(state='disabled', fg='#5b6270')

        elif kind == 'provider_test':
            label = data['label']
            if label.winfo_exists():
                label.config(text=data['text'], fg=data['color'])

        elif kind == 'update_done':
            self.update_running = False
            if data.get('error'):
                if data.get('forced'):
                    self._log('WARNING', f"Sürüm kontrolü başarısız: {data['error']}")
                return
            self._record_update_state('update_last_check', time.time())
            release = data.get('release')
            if release:
                self._show_update(release)
            elif data.get('forced'):
                self._log('INFO', f'Sürüm güncel: v{__version__}')

        elif kind == 'log':
            self._log(data.get('level', 'INFO'), data.get('message', ''))

    def _log(self, level, message):

        self.log_text.configure(state='normal')
        tag = level if level in ('ERROR', 'WARNING', 'FATAL') else ()
        self.log_text.insert('end', f'{level:7s} {message}\n', tag)
        if int(self.log_text.index('end-1c').split('.')[0]) > 800:
            self.log_text.delete('1.0', '300.0')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
