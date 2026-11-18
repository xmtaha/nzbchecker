"""Sağlayıcı ekleme/düzenleme penceresi (modal) ve bağlantı testi."""
import logging
import tkinter as tk
import webbrowser
from tkinter import ttk

from .config import DEFAULT_PROVIDER
from .theme import (BORDER, MUTED, ACCENT, RED, SURFACE, SURFACE_2, TEXT,
                    PROVIDER_FIELDS)
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
                widget = tk.Checkbutton(body, variable=var, bg=SURFACE,
                                        activebackground=SURFACE, selectcolor=SURFACE_2,
                                        bd=0, highlightthickness=0)
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
                       selectcolor=SURFACE_2, bd=0, highlightthickness=0,
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
