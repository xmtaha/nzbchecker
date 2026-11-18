"""Ayar yükleme/kaydetme ve sabitler (GUI)."""
import json
import os
from pathlib import Path

# Ayar dosyası: NZB_CHECKER_CONFIG > ~/.config/nzb_checker/config.json
# (AppImage salt okunur olduğu için script klasörüne yazılamaz)
CONFIG_PATH = Path(os.environ.get('NZB_CHECKER_CONFIG')
                   or Path.home() / '.config' / 'nzb_checker' / 'config.json')

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
    'settings': {'check_all_articles': False,
                 'sample_check_count': 20,
                 'sample_ratio': 0.02, 'timeout': 30, 'cache': True,
                 'cache_ttl_days': 14, 'check_updates': True,
                 'update_skipped_version': '', 'update_last_check': 0,
                 'log_level': 'INFO'},
}


def ensure_config():
    """İlk çalıştırmada ayar dosyasını varsayılanlarla oluştur."""
    if CONFIG_PATH.exists():
        return
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=4, ensure_ascii=False), encoding='utf-8')
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def read_config():
    """CONFIG_PATH'i okur, eksik bölümleri varsayılanlarla doldurur."""
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


def write_config(data):
    """Ayar dosyasına yazar."""
    data.pop('usenet_provider', None)
    data.setdefault('_comment', 'NZB Checker ayarları (GUI tarafından yazılır)')
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding='utf-8')
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass

