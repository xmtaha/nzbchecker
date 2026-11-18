"""GUI yardımcıları: sağlayıcı metni/dönüşümü ve log köprüsü."""
import logging

from .theme import PARTIAL_THRESHOLD, PROVIDER_FIELDS


def provider_text(providers, detailed=False):
    """Sağlayıcı sonuçlarını tek satırda özetler: 'Frugal ✓ · Eweka %3.2'"""
    parts = []
    for p in providers or []:
        name = p.get('name') or '?'
        percent = p.get('percent') or 0.0
        if not detailed:
            parts.append(f"{name} "
                         f"{'✓' if percent < PARTIAL_THRESHOLD else f'%{percent:.1f}'}")
        elif percent < PARTIAL_THRESHOLD:
            parts.append(f"{name}: tam")
        else:
            parts.append(f"{name}: %{percent:.1f} eksik ({p.get('missing', 0)} segment)")
    return ' · '.join(parts)


def provider_from_vars(vars_, fields=PROVIDER_FIELDS):
    """Form değişkenlerini provider sözlüğüne çevirir"""
    item = {}
    for key, label, kind in fields:
        raw = vars_[key].get()
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
        raise ValueError('Sunucu adresi boş')
    if not item['name']:
        item['name'] = item['host']
    return item


class QueueLogHandler(logging.Handler):
    """Engine loglarını GUI kuyruğuna taşır"""

    def __init__(self, q):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(('log', {'level': record.levelname, 'message': record.getMessage()}))
