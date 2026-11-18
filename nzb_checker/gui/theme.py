"""Renk paleti, yazı tipleri ve ayar alanı tanımları."""
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

# yuvarlama gürültüsü: bu eşiğin altındaki eksik yüzdesi "tam" sayılır
PARTIAL_THRESHOLD = 0.05

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
    ('settings', 'cache', 'Sonuçları önbellekle', bool),
    ('settings', 'cache_ttl_days', 'Önbellek ömrü (gün)', int),
    ('settings', 'check_updates', 'Güncellemeleri kontrol et', bool),
    ('settings', 'log_level', 'Log seviyesi', ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
]
SPEC = PATH_FIELDS + CHECK_FIELDS


def pick_font(*names):
    """Sistemde bulunan ilk font ailesini seç"""
    from tkinter import font
    available = set(font.families())
    for name in names:
        if name in available:
            return name
    return 'TkDefaultFont'
