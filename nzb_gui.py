#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NZB Checker GUI - tkinter (stdlib, ek bağımlılık yok)

Tasarım: koyu tema, üst başlık çubuğu, sayaç kartları, sekmeli ayarlar.
Binlerce NZB için: satırlar partiler halinde eklenir, tarama arka planda çalışır.
"""

import json
import logging
import os
import queue
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from nzb_checker import NZBChecker

# Ayar dosyası: NZB_CHECKER_CONFIG > ~/.config/nzb_checker/config.json
# (AppImage salt okunur olduğu için script klasörüne yazılamaz)
# tkdnd Tcl paketi (sürükle-bırak); AppImage'da da yanında taşınır
DND_DIR = Path(__file__).with_name('tkdnd')

CONFIG_PATH = Path(os.environ.get('NZB_CHECKER_CONFIG')
                   or Path.home() / '.config' / 'nzb_checker' / 'config.json')


def ensure_config():
    """İlk çalıştırmada ayar dosyasını varsayılanlarla oluştur."""
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).with_name('config.json')
    text = (src.read_text(encoding='utf-8') if src.exists()
            else json.dumps(DEFAULTS, indent=4, ensure_ascii=False))
    CONFIG_PATH.write_text(text, encoding='utf-8')

STATUS_TEXT = {
    'queued': 'Bekliyor',
    'running': 'Kontrol ediliyor',
    'complete': 'Tam',
    'missing': 'Eksik',
    'error': 'Hatalı',
    'cancelled': 'İptal',
}
VISIBLE_WHEN_FILTERED = ('missing', 'error', 'cancelled')

DEFAULT_PROVIDER = {'name': 'Sağlayıcı 1', 'host': '', 'port': 563, 'username': '',
                    'password': '', 'use_ssl': True, 'connections': 20}

DEFAULTS = {
    'providers': [dict(DEFAULT_PROVIDER)],
    'paths': {'nzb_folder': str(Path.home() / 'nzb'), 'complete_folder': 'complete',
              'missing_folder': 'missing', 'error_folder': 'error'},
    'settings': {'check_all_articles': False, 'sample_check_count': 20,
                 'sample_ratio': 0.02, 'timeout': 30, 'log_level': 'INFO'},
}

# provider düzenleyicisi: (anahtar, etiket, tip)
PROVIDER_FIELDS = [
    ('name', 'Ad', str),
    ('host', 'Sunucu', str),
    ('port', 'Port', int),
    ('username', 'Kullanıcı adı', str),
    ('password', 'Şifre', 'secret'),
    ('connections', 'Eşzamanlı bağlantı', int),
    ('use_ssl', 'SSL kullan', bool),
]
PATH_FIELDS = [
    ('paths', 'complete_folder', 'Tam klasörü', str),
    ('paths', 'missing_folder', 'Eksik klasörü', str),
    ('paths', 'error_folder', 'Hatalı klasörü', str),
]
CHECK_FIELDS = [
    ('settings', 'check_all_articles', 'Tüm segmentleri kontrol et', bool),
    ('settings', 'sample_check_count', 'En az örnek segment', int),
    ('settings', 'sample_ratio', 'Örnek oranı (0.02 = %2)', float),
    ('settings', 'timeout', 'Timeout (sn)', int),
    ('settings', 'log_level', 'Log seviyesi', ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
]
SPEC = PATH_FIELDS + CHECK_FIELDS

# --- palet -----------------------------------------------------------------
BG = '#131519'
SURFACE = '#1b1e24'
SURFACE_2 = '#23272f'
SURFACE_3 = '#2b303a'
BORDER = '#333945'
TEXT = '#e7e9ee'
MUTED = '#949cab'
ACCENT = '#4c8dff'
ACCENT_DARK = '#2f6ad0'
GREEN = '#3fb950'
AMBER = '#d9a220'
RED = '#f2545b'
GREY = '#7c8697'

STATUS_COLOR = {'complete': GREEN, 'missing': AMBER, 'error': RED,
                'cancelled': GREY, 'running': ACCENT, 'queued': MUTED}


def pick_font(*names):
    """Sistemde bulunan ilk font ailesini seç"""
    available = set(font.families())
    for name in names:
        if name in available:
            return name
    return 'TkDefaultFont'


class QueueLogHandler(logging.Handler):
    """Engine loglarını GUI kuyruğuna taşır"""

    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(('log', {'level': record.levelname, 'message': record.getMessage()}))


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
        self.counts = {'complete': 0, 'missing': 0, 'error': 0}
        self.listed = 0
        self.checker = None
        self.worker = None

        self.config_data = self._read_config()
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

    # ------------------------------------------------------------- başlık

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
        self.search = tk.StringVar()
        entry = tk.Entry(bar, textvariable=self.search, width=22, bg=SURFACE_2, fg=TEXT,
                         insertbackground=TEXT, relief='flat', highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT,
                         font=(self.ui_font, 10))
        entry.pack(side='left', padx=(6, 0), ipady=4)
        entry.bind('<KeyRelease>', lambda _e: self._apply_filter())

        self.count_label = tk.Label(bar, text='0 dosya', bg=SURFACE, fg=MUTED,
                                    font=(self.ui_font, 9))
        self.count_label.pack(side='right')

        body = tk.Frame(self.files_tab, bg=SURFACE)
        body.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        columns = ('name', 'status', 'segments', 'checked', 'missing', 'percent', 'elapsed')
        self.tree = ttk.Treeview(body, columns=columns, show='headings', selectmode='extended')
        for column, title, width, anchor in (
                ('name', 'Dosya', 400, 'w'),
                ('status', 'Durum', 120, 'w'),
                ('segments', 'Segment', 90, 'e'),
                ('checked', 'Kontrol', 90, 'e'),
                ('missing', 'Eksik', 90, 'e'),
                ('percent', 'Eksik %', 90, 'e'),
                ('elapsed', 'Süre', 90, 'e')):
            self.tree.heading(column, text=title, anchor=anchor)
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

    def _build_providers(self, parent):
        """Provider listesi: kullanıcı istediği kadar ekleyip silebilir"""
        group = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER, padx=14, pady=12)
        group.grid(row=0, column=0, sticky='nsew', padx=(0, 12))

        tk.Label(group, text='Usenet sağlayıcıları', bg=SURFACE, fg=TEXT,
                 font=(self.ui_font, 11, 'bold')).grid(row=0, column=0, sticky='w')
        self._button(group, '+ Ekle', self.add_provider).grid(row=0, column=1, sticky='e')
        group.columnconfigure(0, weight=1)

        self.provider_box = tk.Frame(group, bg=SURFACE)
        self.provider_box.grid(row=1, column=0, columnspan=2, sticky='nsew', pady=(10, 0))
        group.rowconfigure(1, weight=1)

        self.provider_forms = []
        for provider in self.config_data.get('providers') or [dict(DEFAULT_PROVIDER)]:
            self._add_provider_form(provider)

    def _add_provider_form(self, provider):
        """Tek provider kartı"""
        card = tk.Frame(self.provider_box, bg=SURFACE_2, highlightthickness=1,
                        highlightbackground=BORDER, padx=12, pady=10)
        card.pack(fill='x', pady=(0, 8))

        head = tk.Frame(card, bg=SURFACE_2)
        head.pack(fill='x')
        title = tk.Label(head, text=provider.get('name') or 'Sağlayıcı', bg=SURFACE_2,
                         fg=TEXT, font=(self.ui_font, 10, 'bold'))
        title.pack(side='left')

        vars_ = {}
        remove = self._button(head, 'Kaldır', None)
        remove.config(command=lambda: self.remove_provider(card))
        remove.pack(side='right')

        rows = tk.Frame(card, bg=SURFACE_2)
        rows.pack(fill='x', pady=(8, 0))
        rows.columnconfigure(1, weight=1)
        rows.columnconfigure(3, weight=1)

        for position, (key, label, kind) in enumerate(PROVIDER_FIELDS):
            value = provider.get(key, DEFAULT_PROVIDER.get(key))
            if kind is bool:
                var = tk.BooleanVar(value=bool(value))
                widget = tk.Checkbutton(rows, variable=var, bg=SURFACE_2,
                                        activebackground=SURFACE_2, selectcolor=SURFACE,
                                        bd=0, highlightthickness=0)
            else:
                var = tk.StringVar(value='' if value is None else str(value))
                widget = tk.Entry(rows, textvariable=var, bg=SURFACE, fg=TEXT,
                                  insertbackground=TEXT, relief='flat', highlightthickness=1,
                                  highlightbackground=BORDER, highlightcolor=ACCENT,
                                  show='*' if kind == 'secret' else '',
                                  font=(self.ui_font, 10))

            row, column = divmod(position, 2)
            tk.Label(rows, text=label, bg=SURFACE_2, fg=MUTED,
                     font=(self.ui_font, 9)).grid(row=row * 2, column=column * 2,
                                                  sticky='w', padx=(0, 8), pady=(4, 0))
            widget.grid(row=row * 2 + 1, column=column * 2, columnspan=2,
                        sticky='w' if kind is bool else 'we',
                        padx=(0, 14), pady=(0, 6), ipady=0 if kind is bool else 4)
            vars_[key] = var

        self.provider_forms.append({'card': card, 'vars': vars_, 'title': title})

    def add_provider(self):
        number = len(self.provider_forms) + 1
        provider = dict(DEFAULT_PROVIDER)
        provider['name'] = f'Sağlayıcı {number}'
        self._add_provider_form(provider)
        self.provider_box.update_idletasks()

    def remove_provider(self, card):
        if len(self.provider_forms) <= 1:
            messagebox.showinfo('Sağlayıcı', 'En az bir sağlayıcı kalmalı.')
            return
        for form in list(self.provider_forms):
            if form['card'] is card:
                self.provider_forms.remove(form)
        card.destroy()

        footer = tk.Frame(self.settings_tab, bg=SURFACE, padx=12)
        footer.pack(fill='x', pady=(0, 12))
        self._button(footer, 'Ayarları kaydet', self._save_clicked, primary=True).pack(side='left')
        self.save_hint = tk.Label(footer, text='Değişiklikler config.json dosyasına yazılır',
                                  bg=SURFACE, fg=MUTED, font=(self.ui_font, 9))
        self.save_hint.pack(side='left', padx=10)

        tk.Label(footer, text='F5 başlat  ·  Esc durdur  ·  Ctrl+S kaydet',
                 bg=SURFACE, fg=MUTED, font=(self.ui_font, 9)).pack(side='right')

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

    def _read_config(self):
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            data = {}
        for section, values in DEFAULTS.items():
            if isinstance(values, list):
                continue
            merged = dict(values)
            merged.update(data.get(section, {}) or {})
            data[section] = merged
        # eski tek sunucu biçimini listeye çevir
        if not data.get('providers'):
            legacy = data.get('usenet_provider')
            data['providers'] = [legacy] if legacy else [dict(DEFAULT_PROVIDER)]
        data.pop('usenet_provider', None)
        return data

    def _provider_values(self):
        """Formdaki provider kartlarını config biçimine çevir"""
        providers = []
        for form in self.provider_forms:
            item = {}
            for key, label, kind in PROVIDER_FIELDS:
                raw = form['vars'][key].get()
                if kind is bool:
                    item[key] = bool(raw)
                elif kind is int:
                    try:
                        item[key] = int(float(str(raw).strip().replace(',', '.')))
                    except ValueError:
                        raise ValueError(f'{label}: sayı bekleniyor ({raw!r})')
                else:
                    item[key] = str(raw).strip()
            if not item['host']:
                raise ValueError(f"{item['name'] or 'Sağlayıcı'}: sunucu adresi boş")
            if not item['name']:
                item['name'] = item['host']
            providers.append(item)
        if not providers:
            raise ValueError('En az bir sağlayıcı gerekli')
        return providers

    def _pick_dir(self, var):
        folder = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if folder:
            var.set(folder)

    def _save_config(self):
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8')) if CONFIG_PATH.exists() else {}
        data.pop('usenet_provider', None)
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
        data.setdefault('_comment', 'NZB Checker ayarları (GUI tarafından yazılır)')
        CONFIG_PATH.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding='utf-8')

    def _save_clicked(self):
        try:
            self._save_config()
        except Exception as e:
            messagebox.showerror('Ayarlar', f'Kaydedilemedi: {e}')
            return
        self.save_hint.config(text=f'Kaydedildi: {CONFIG_PATH}', fg=GREEN)
        self.summary_label.config(text=f'Ayarlar kaydedildi: {CONFIG_PATH}')

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

    def _upsert(self, name, status, segments=0, checked=0, missing=0, elapsed=0.0,
                percent=None):
        values = (name, STATUS_TEXT.get(status, status), segments, checked, missing,
                  f'%{percent:.1f}' if percent else '',
                  f'{elapsed:.1f}s' if elapsed else '')
        entry = self.rows.get(name)
        if entry is None:
            stripe = 'odd' if len(self.order) % 2 else 'even'
            iid = self.tree.insert('', 'end', values=values, tags=(stripe, status))
            entry = {'iid': iid, 'status': status, 'visible': True, 'stripe': stripe}
            self.rows[name] = entry
            self.order.append(name)
        else:
            self.tree.item(entry['iid'], values=values, tags=(entry['stripe'], status))
            entry['status'] = status
        self._show(name, self._row_wanted(name))

    def _row_wanted(self, name):
        entry = self.rows[name]
        if self.only_problems.get() and entry['status'] not in VISIBLE_WHEN_FILTERED:
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
        else:
            self.tree.detach(entry['iid'])
        entry['visible'] = wanted

    def _apply_filter(self):
        for name in self.order:
            self._show(name, self._row_wanted(name))

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

        files = sorted(p.name for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() == '.nzb')
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
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal', fg=TEXT)
        self.summary_label.config(text='Tarama başlıyor…')
        self._set_status('Çalışıyor', ACCENT)
        self.notebook.select(0)

        self.checker = NZBChecker(str(CONFIG_PATH), events=self._on_event)
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def stop(self):
        if self.checker:
            self.checker.cancel()
            self.summary_label.config(text='Durduruluyor…')
            self._set_status('Durduruluyor', AMBER)

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

        elif kind == 'file_start':
            self._upsert(data['name'], 'running', segments=data.get('segments', 0))
            self._set_status('Kontrol ediliyor', ACCENT)

        elif kind == 'file_done':
            status = data.get('status', 'error')
            self._upsert(data['name'], status, segments=data.get('segments', 0),
                         checked=data.get('checked', 0), missing=data.get('missing', 0),
                         elapsed=data.get('elapsed', 0.0),
                         percent=data.get('percent') or None)
            providers = data.get('providers') or []
            if len(providers) > 1:
                self._log('INFO', f"{data['name']}: " + ', '.join(
                    f"{p['name']} %{p['percent']:.1f}" for p in providers))
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
            cancelled = data.get('cancelled')
            self.summary_label.config(
                text=(f"{'Durduruldu' if cancelled else 'Bitti'} — "
                      f"Tam: {data.get('complete', 0)}, Eksik: {data.get('missing', 0)}, "
                      f"Hatalı: {data.get('error', 0)}"))
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


def main():
    ensure_config()
    os.chdir(Path.home())
    App().mainloop()


if __name__ == '__main__':
    main()
