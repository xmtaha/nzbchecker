"""Sağlayıcı ekleme/düzenleme penceresi (modal) ve bağlantı testi."""
import tkinter as tk

from .config import DEFAULT_PROVIDER
from .theme import (BORDER, MUTED, ACCENT, RED, SURFACE, SURFACE_2, TEXT,
                    PROVIDER_FIELDS)
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
