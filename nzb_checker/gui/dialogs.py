"""Sağlayıcı ekleme/düzenleme penceresi (modal), klasör seçici ve bağlantı testi."""
import logging
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from .config import DEFAULT_PROVIDER
from .theme import (ACCENT, ACCENT_DARK, AMBER, BORDER, MUTED, RED, STRIPE,
                    SURFACE, SURFACE_2, SURFACE_3, TEXT, PROVIDER_FIELDS)
from ..updater import pick_asset
from .widgets import provider_from_vars


class ProviderDialog(tk.Toplevel):
    """Sağlayıcı formu; Kaydet'e basılırsa sonuç self.result'a yazılır."""

    def __init__(self, app, provider, title):
        super().__init__(app)
        self.app = app
        self.result = {}
        self.title(title)
        self.configure(bg=SURFACE)
        self.transient(app)
        self.resizable(False, False)

        body = tk.Frame(self, bg=SURFACE, padx=20, pady=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        tk.Label(body, text=title, bg=SURFACE, fg=TEXT,
                 font=(app.ui_font, 12, 'bold')).grid(row=0, column=0, columnspan=2,
                                                      sticky='w', pady=(0, 12))

        self.vars = {}
        for position, (key, label, kind) in enumerate(PROVIDER_FIELDS, start=1):
            value = provider.get(key, DEFAULT_PROVIDER.get(key))
            if kind is bool:
                var = tk.BooleanVar(value=bool(value))
                widget = tk.Checkbutton(body, variable=var, bg=SURFACE, fg=TEXT,
                                        activebackground=SURFACE, activeforeground=TEXT,
                                        selectcolor=ACCENT, bd=0, highlightthickness=0)
            else:
                var = tk.StringVar(value='' if value is None else str(value))
                widget = tk.Entry(body, textvariable=var, bg=SURFACE_2, fg=TEXT,
                                  insertbackground=TEXT, relief='flat', highlightthickness=1,
                                  highlightbackground=BORDER, highlightcolor=ACCENT,
                                  show='*' if kind == 'secret' else '',
                                  font=(app.ui_font, 10))
            tk.Label(body, text=label, bg=SURFACE, fg=MUTED,
                     font=(app.ui_font, 9)).grid(row=position, column=0, sticky='w',
                                                 padx=(0, 12), pady=4)
            widget.grid(row=position, column=1, sticky='w' if kind is bool else 'we',
                        pady=4, ipady=0 if kind is bool else 4)
            self.vars[key] = var

        self.status = tk.Label(body, text='', bg=SURFACE, fg=MUTED,
                               font=(app.ui_font, 9), wraplength=340, justify='left')
        self.status.grid(row=len(PROVIDER_FIELDS) + 1, column=0, columnspan=2,
                         sticky='w', pady=(10, 0))

        buttons = tk.Frame(body, bg=SURFACE)
        buttons.grid(row=len(PROVIDER_FIELDS) + 2, column=0, columnspan=2,
                     sticky='e', pady=(14, 0))
        app._button(buttons, 'Bağlantıyı test et', self._on_test).pack(side='left', padx=(0, 8))
        app._button(buttons, 'Kaydet', self._on_save, primary=True).pack(side='left')

        self.bind('<Return>', lambda _e: self._on_save())
        self.bind('<Escape>', lambda _e: self.destroy())
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f'+{max(x, 0)}+{max(y, 0)}')
        self.grab_set()
        self.wait_window()

    def _on_test(self):
        try:
            item = provider_from_vars(self.vars)
        except ValueError as exc:
            self.status.config(text=str(exc), fg=RED)
            return
        self.status.config(text='Bağlanılıyor…', fg=MUTED)
        self.app._test_provider_async(item, self.status)

    def _on_save(self):
        try:
            self.result.update(provider_from_vars(self.vars))
        except ValueError as exc:
            self.status.config(text=str(exc), fg=RED)
            return
        self.destroy()


class FolderDialog(tk.Toplevel):
    """
    Koyu temalı klasör seçici.

    filedialog.askdirectory sistem temasını kullanır ve koyu arayüzde
    yamalı görünür; bu pencere uygulamanın paletiyle çizilir.
    """

    PLACES = (('Ana dizin', Path.home()),
              ('İndirilenler', Path.home() / 'İndirilenler'),
              ('Belgeler', Path.home() / 'Belgeler'),
              ('Resimler', Path.home() / 'Resimler'),
              ('Video', Path.home() / 'Videolar'),
              ('Müzik', Path.home() / 'Müzik'))

    def __init__(self, app, initial='', title='Klasör seç'):
        super().__init__(app)
        self.app = app
        self.result = ''
        self.current = Path.home()
        self._entries = []
        self.title(title)
        self.configure(bg=SURFACE)
        self.transient(app)
        self.geometry('780x520')
        self.minsize(560, 380)

        body = tk.Frame(self, bg=SURFACE, padx=18, pady=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(2, weight=1)

        # --- başlık ---------------------------------------------------
        head = tk.Frame(body, bg=SURFACE)
        head.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 12))
        tk.Label(head, text='▤', bg=SURFACE, fg=ACCENT,
                 font=(app.ui_font, 16, 'bold')).pack(side='left', padx=(0, 10))
        text = tk.Frame(head, bg=SURFACE)
        text.pack(side='left', fill='x', expand=True)
        tk.Label(text, text=title, bg=SURFACE, fg=TEXT,
                 font=(app.ui_font, 13, 'bold')).pack(anchor='w')
        self.where = tk.Label(text, text='', bg=SURFACE, fg=MUTED,
                              font=(app.ui_font, 9))
        self.where.pack(anchor='w', pady=(2, 0))

        # --- sol taraf: kısayollar ------------------------------------
        side = tk.Frame(body, bg=SURFACE_2, padx=10, pady=10)
        side.grid(row=1, column=0, rowspan=2, sticky='ns', padx=(0, 14))
        tk.Label(side, text='HIZLI ERİŞİM', bg=SURFACE_2, fg=MUTED,
                 font=(app.ui_font, 8, 'bold')).pack(anchor='w', pady=(0, 8))
        self.place_buttons = {}
        for label, path in self.PLACES:
            if not path.is_dir():
                continue
            button = tk.Button(side, text=label, command=lambda p=path: self._load(p),
                               bg=SURFACE_2, fg=TEXT, activebackground=SURFACE_3,
                               activeforeground=TEXT, relief='flat', bd=0, anchor='w',
                               padx=8, pady=5, cursor='hand2',
                               font=(app.ui_font, 10))
            button.pack(fill='x', pady=(0, 2))
            self.place_buttons[path] = button

        # --- sağ üst: yol çubuğu --------------------------------------
        bar = tk.Frame(body, bg=SURFACE)
        bar.grid(row=1, column=1, sticky='ew', pady=(0, 8))
        app._button(bar, '↑', self._go_up).pack(side='left', padx=(0, 8))
        self.path_var = tk.StringVar()
        entry = tk.Entry(bar, textvariable=self.path_var, bg=SURFACE_2, fg=TEXT,
                         insertbackground=TEXT, relief='flat', highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=ACCENT,
                         font=(app.mono_font, 9))
        entry.pack(side='left', fill='x', expand=True, ipady=5)
        entry.bind('<Return>', lambda _e: self._load(self.path_var.get()))
        self.path_entry = entry

        # --- sağ alt: liste -------------------------------------------
        list_frame = tk.Frame(body, bg=SURFACE, highlightthickness=1,
                              highlightbackground=BORDER)
        list_frame.grid(row=2, column=1, sticky='nsew')
        self.listbox = tk.Listbox(list_frame, bg=SURFACE, fg=TEXT, relief='flat',
                                  bd=0, highlightthickness=0, activestyle='none',
                                  selectbackground=ACCENT_DARK,
                                  selectforeground='#ffffff',
                                  exportselection=False, font=(app.ui_font, 10))
        scroll = ttk.Scrollbar(list_frame, orient='vertical',
                               command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.listbox.pack(side='left', fill='both', expand=True, padx=6, pady=6)
        self.listbox.bind('<Double-1>', self._enter)
        self.listbox.bind('<Return>', self._enter)
        self.listbox.bind('<BackSpace>', lambda _e: self._go_up())
        self.listbox.bind('<Left>', lambda _e: self._go_up())
        self.listbox.bind('<space>', lambda _e: self._enter())

        # --- alt: ipucu ve düğmeler ----------------------------------
        self.hint = tk.Label(body, text='', bg=SURFACE, fg=RED,
                             font=(app.ui_font, 9), anchor='w')
        self.hint.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(10, 0))

        buttons = tk.Frame(body, bg=SURFACE)
        buttons.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        app._button(buttons, 'Bu klasörü seç', self._select,
                    primary=True).pack(side='right')
        app._button(buttons, 'İptal', self.destroy).pack(side='right', padx=(0, 8))
        self.selected_label = tk.Label(buttons, text='', bg=SURFACE, fg=MUTED,
                                       font=(app.ui_font, 9))
        self.selected_label.pack(side='left')

        self._load(initial or str(Path.home()))
        self.bind('<Escape>', lambda _e: self.destroy())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f'+{max(x, 0)}+{max(y, 0)}')
        self.grab_set()
        self.wait_window()

    def _load(self, path):
        target = Path(str(path).strip().strip('{}')).expanduser()
        if not target.is_dir():
            self.hint.config(text=f'Klasör bulunamadı: {target}')
            return
        try:
            children = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            self.hint.config(text=f'Okunamadı: {exc}')
            return

        folders = [p for p in children if p.is_dir() and not p.name.startswith('.')]
        files = [p for p in children if p.is_file() and not p.name.startswith('.')]

        self.current = target
        self.path_var.set(str(target))
        self.where.config(text=f'{target.name or target}')
        self.hint.config(text='')

        self.listbox.delete(0, 'end')
        self._entries = []
        if target.parent != target:
            self.listbox.insert('end', '  ↑  üst klasör')
            self._entries.append((target.parent, 'parent'))
        for folder in folders:
            self.listbox.insert('end', f'  ▸  {folder.name}')
            self._entries.append((folder, 'dir'))
        for item in files:
            index = self.listbox.size()
            self.listbox.insert('end', f'     {item.name}')
            self.listbox.itemconfig(index, foreground=MUTED)
            self._entries.append((item, 'file'))

        for index in range(self.listbox.size()):
            if index % 2:
                background = STRIPE
                if self._entries[index][1] != 'file':
                    self.listbox.itemconfig(index, background=background)

        for path, button in self.place_buttons.items():
            button.config(fg=ACCENT if path == target else TEXT)
        self.listbox.selection_clear(0, 'end')
        self.selected_label.config(
            text=f'{len(folders)} klasör · {len(files)} dosya')

    def _chosen(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        path, kind = self._entries[selection[0]]
        return (path, kind)

    def _enter(self, _event=None):
        chosen = self._chosen()
        if not chosen:
            return
        path, kind = chosen
        if kind == 'file':
            self.hint.config(text='Dosya değil, bir klasör seçin.')
            return
        self._load(path)

    def _go_up(self):
        self._load(self.current.parent)

    def _select(self):
        chosen = self._chosen()
        if chosen and chosen[1] != 'file' and self.listbox.curselection():
            self.result = str(chosen[0])
        else:
            self.result = str(self.current)
        self.destroy()



class MessageDialog(tk.Toplevel):
    """
    Koyu temalı ileti penceresi.

    messagebox sistem temasını kullanır ve koyu arayüzde yamalı görünür;
    bu pencere uygulamanın paletiyle çizilir.
    """

    ICONS = {'info': ('i', ACCENT), 'question': ('?', ACCENT),
             'warning': ('!', AMBER), 'error': ('!', RED)}

    def __init__(self, app, title, message, kind='info', ask=False,
                 yes='Evet', no='Vazgeç'):
        super().__init__(app)
        self.result = False
        self.title(title)
        self.configure(bg=SURFACE)
        self.transient(app)
        self.resizable(False, False)

        body = tk.Frame(self, bg=SURFACE, padx=22, pady=18)
        body.pack(fill='both', expand=True)

        icon, color = self.ICONS.get(kind, self.ICONS['info'])
        tk.Label(body, text=icon, bg=SURFACE_2, fg=color, width=2,
                 font=(app.ui_font, 15, 'bold')).grid(row=0, column=0, sticky='n',
                                                     padx=(0, 14), ipady=8)
        tk.Label(body, text=title, bg=SURFACE, fg=TEXT,
                 font=(app.ui_font, 12, 'bold')).grid(row=0, column=1, sticky='w')
        tk.Label(body, text=message, bg=SURFACE, fg=MUTED, justify='left',
                 wraplength=400, font=(app.ui_font, 10)).grid(row=1, column=1,
                                                             sticky='w', pady=(6, 0))

        buttons = tk.Frame(body, bg=SURFACE)
        buttons.grid(row=2, column=0, columnspan=2, sticky='e', pady=(18, 0))
        if ask:
            app._button(buttons, no, self.destroy).pack(side='right', padx=(8, 0))
            app._button(buttons, yes, self._ok, primary=True).pack(side='right')
        else:
            app._button(buttons, 'Tamam', self._ok, primary=True).pack(side='right')

        self.bind('<Return>', lambda _e: self._ok())
        self.bind('<Escape>', lambda _e: self.destroy())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f'+{max(x, 0)}+{max(y, 0)}')
        self.grab_set()
        self.wait_window()

    def _ok(self):
        self.result = True
        self.destroy()


def show_info(app, title, message):
    """messagebox.showinfo yerine koyu temalı pencere"""
    MessageDialog(app, title, message, 'info')


def show_error(app, title, message):
    """messagebox.showerror yerine koyu temalı pencere"""
    MessageDialog(app, title, message, 'error')


def ask_yes_no(app, title, message, yes='Evet', no='Vazgeç'):
    """messagebox.askyesno yerine koyu temalı pencere; Evet ise True"""
    return MessageDialog(app, title, message, 'question', ask=True,
                         yes=yes, no=no).result


def open_folder_dialog(app, initial='', title='Klasör seç'):
    """Koyu temalı klasör seçiciyi açar; seçilen yol, iptalde boş dize döner."""
    dialog = FolderDialog(app, initial, title)
    return dialog.result


def open_provider_dialog(app, provider, title):
    """Modal pencereyi açar; kaydedilirse provider sözlüğü döner."""
    dialog = ProviderDialog(app, provider, title)
    return dialog.result or None


class UpdateDialog(tk.Toplevel):
    """
    Yeni sürüm bildirimi.

    İndirme/yer değiştirme yapmaz; yalnızca release sayfasını veya AppImage
    bağlantısını tarayıcıda açar. Kullanıcı dosyayı kendisi indirip değiştirir.
    """

    NOTES_LIMIT = 6000

    def __init__(self, app, release, running_name='', running_path='', on_close=None):
        super().__init__(app)
        self.app = app
        self.result = {'skip': False, 'version': release.get('version', '')}
        self.release = release
        self.on_close = on_close
        self._closed = False
        self.is_appimage = bool(running_path)
        self.title('Yeni sürüm mevcut')
        self.configure(bg=SURFACE)
        self.transient(app)
        self.resizable(True, True)

        asset = pick_asset(release.get('assets') or [], running_name)
        self.download_url = (asset or {}).get('url') or release.get('html_url') or ''

        body = tk.Frame(self, bg=SURFACE, padx=20, pady=16)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        tk.Label(body, text='Yeni sürüm mevcut', bg=SURFACE, fg=TEXT,
                 font=(app.ui_font, 14, 'bold')).grid(row=0, column=0, sticky='w')
        versions = (f"v{release.get('current', '?')}  →  v{release.get('version', '?')}"
                    f"   ·   {release.get('name', '')}")
        tk.Label(body, text=versions, bg=SURFACE, fg=ACCENT,
                 font=(app.ui_font, 10, 'bold')).grid(row=1, column=0, sticky='w',
                                                     pady=(4, 2))
        published = str(release.get('published_at') or '')[:10]
        if published:
            tk.Label(body, text=f'Yayın tarihi: {published}', bg=SURFACE, fg=MUTED,
                     font=(app.ui_font, 9)).grid(row=2, column=0, sticky='w')

        notes = tk.Frame(body, bg=SURFACE_2, highlightthickness=1,
                         highlightbackground=BORDER)
        notes.grid(row=3, column=0, sticky='nsew', pady=(12, 0))
        notes.columnconfigure(0, weight=1)
        notes.rowconfigure(0, weight=1)
        self.notes_text = tk.Text(notes, height=10, wrap='word', state='disabled',
                                  bg=SURFACE_2, fg=TEXT, relief='flat', bd=0,
                                  insertbackground=TEXT, padx=10, pady=8,
                                  font=(app.ui_font, 9))
        scroll = ttk.Scrollbar(notes, orient='vertical', command=self.notes_text.yview)
        self.notes_text.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky='ns')
        self.notes_text.grid(row=0, column=0, sticky='nsew')

        text = release.get('body') or 'Sürüm notu yok.'
        if len(text) > self.NOTES_LIMIT:
            text = text[:self.NOTES_LIMIT] + '\n… (notlar kısaltıldı)'
        self.notes_text.configure(state='normal')
        self.notes_text.insert('1.0', text)
        self.notes_text.configure(state='disabled')

        if running_path:
            hint = (f'Mevcut dosya: {running_path}\n'
                    'İndirdikten sonra bu dosyanın yerine koyun; uygulama '
                    'güncellemeyi kendisi indirmez veya değiştirmez.')
        else:
            hint = ('Kaynaktan çalıştırıyorsunuz: `git pull` ile güncelleyin ya da '
                    'sürüm sayfasından indirin. Uygulama hiçbir dosyayı kendisi '
                    'indirmez veya değiştirmez.')
        tk.Label(body, text=hint, bg=SURFACE, fg=MUTED, justify='left',
                 wraplength=560, font=(app.ui_font, 9)).grid(
            row=4, column=0, sticky='w', pady=(10, 0))

        self.skip_var = tk.BooleanVar(value=False)
        tk.Checkbutton(body, text='Bu sürümü bir daha gösterme',
                       variable=self.skip_var, bg=SURFACE, fg=TEXT,
                       activebackground=SURFACE, activeforeground=TEXT,
                       selectcolor=ACCENT, bd=0, highlightthickness=0,
                       font=(app.ui_font, 9)).grid(row=5, column=0, sticky='w',
                                                   pady=(10, 0))

        buttons = tk.Frame(body, bg=SURFACE)
        buttons.grid(row=6, column=0, sticky='e', pady=(14, 0))
        app._button(buttons, 'Kapat', self._on_close).pack(side='left', padx=(0, 8))
        if self.is_appimage:
            if release.get('html_url'):
                app._button(buttons, 'Sürüm sayfası',
                            lambda: self._open(release['html_url'])).pack(
                    side='left', padx=(0, 8))
            app._button(buttons, 'AppImage\'i indir',
                        lambda: self._open(self.download_url), primary=True).pack(side='left')
        else:
            app._button(buttons, 'Sürüm sayfasını aç',
                        lambda: self._open(release.get('html_url') or self.download_url),
                        primary=True).pack(side='left')

        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.bind('<Escape>', lambda _e: self._on_close())
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_width()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_height()) // 3
        self.geometry(f'+{max(x, 0)}+{max(y, 0)}')
        self.lift()

    def _open(self, url):
        if url:
            webbrowser.open(url)

    def _on_close(self):
        if self._closed:
            return
        self._closed = True
        self.result['skip'] = bool(self.skip_var.get())
        try:
            self.destroy()
        finally:
            if self.on_close is not None:
                try:
                    self.on_close(self.result)
                except Exception:
                    logging.getLogger(__name__).exception(
                        'Güncelleme penceresi kapanış işleyicisi hata verdi')

    def close(self):
        """Pencereyi kapat (programatik)"""
        self._on_close()


def open_update_dialog(app, release, running_name='', running_path='', on_close=None):
    """Yeni sürüm penceresini açar (bloklamaz); pencere nesnesini döndürür."""
    return UpdateDialog(app, release, running_name=running_name,
                        running_path=running_path, on_close=on_close)