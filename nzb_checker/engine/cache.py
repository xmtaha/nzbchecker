#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sağlayıcı yanıt önbelleği (SQLite).

Aynı message-id'ler tekrar sorulduğunda STAT gönderilmez. Anahtar
(provider, message_id) çiftidir; kayıtlar TTL sonunda silinir. SQLite
açılamazsa önbellek sessizce devre dışı kalır - tarama normal sürer.
"""

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

DEFAULT_TTL_DAYS = 14
logger = logging.getLogger(__name__)

# SQLite eski sürümlerinde değişken sınırı 999; IN (...) sorgularını böl
_QUERY_BATCH = 400


def default_cache_path() -> Path:
    """Önbellek dosyası: NZB_CHECKER_CACHE > $XDG_CACHE_HOME/nzb_checker/cache.db"""
    override = os.environ.get('NZB_CHECKER_CACHE')
    if override:
        return Path(override).expanduser()
    base = os.environ.get('XDG_CACHE_HOME') or (Path.home() / '.cache')
    return Path(base) / 'nzb_checker' / 'cache.db'


def provider_key(provider: dict) -> str:
    """Sağlayıcı kimliği (şifre hariç): host/port/kullanıcı adı"""
    raw = '{}:{}:{}'.format(
        str(provider.get('host') or '').lower(),
        int(provider.get('port') or 563),
        provider.get('username') or '',
    )
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


class ResultCache:
    """Thread-safe, toplu yazımlı sonuç önbelleği"""

    def __init__(self, path=None, ttl_days: int = DEFAULT_TTL_DAYS):
        self.path = Path(path).expanduser() if path else default_cache_path()
        self.ttl_seconds = max(1, int(ttl_days)) * 86400
        self._lock = threading.Lock()
        self.conn: Optional[sqlite3.Connection] = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute(
                'CREATE TABLE IF NOT EXISTS results ('
                ' provider TEXT NOT NULL,'
                ' message_id TEXT NOT NULL,'
                ' present INTEGER NOT NULL,'
                ' checked_at REAL NOT NULL,'
                ' PRIMARY KEY (provider, message_id)'
                ') WITHOUT ROWID')
            conn.commit()
            self.conn = conn
        except Exception as exc:  # salt okunur dizin, bozuk DB, ...
            logger.warning(f"Önbellek açılamadı ({self.path}): {exc}")
            self.close()

    @property
    def enabled(self) -> bool:
        return self.conn is not None

    def get_many(self, key: str, message_ids: Iterable[str]) -> Dict[str, bool]:
        """Verilen id'ler için taze kayıtları döndürür: {message_id: var mı}"""
        ids = list(message_ids)
        if self.conn is None or not ids:
            return {}
        cutoff = time.time() - self.ttl_seconds
        found: Dict[str, bool] = {}
        with self._lock:
            try:
                for start in range(0, len(ids), _QUERY_BATCH):
                    part = ids[start:start + _QUERY_BATCH]
                    marks = ','.join('?' * len(part))
                    rows = self.conn.execute(
                        f'SELECT message_id, present FROM results '
                        f'WHERE provider = ? AND checked_at >= ? '
                        f'AND message_id IN ({marks})',
                        [key, cutoff, *part])
                    found.update({mid: bool(present) for mid, present in rows})
            except Exception as exc:
                logger.debug(f"Önbellek okunamadı: {exc}")
        return found

    def put_many(self, key: str, results: Dict[str, bool]):
        """Kontrol sonuçlarını topluca yaz"""
        if self.conn is None or not results:
            return
        now = time.time()
        rows = [(key, mid, 1 if exists else 0, now)
                for mid, exists in results.items()]
        with self._lock:
            try:
                self.conn.executemany(
                    'INSERT INTO results (provider, message_id, present, checked_at) '
                    'VALUES (?, ?, ?, ?) '
                    'ON CONFLICT(provider, message_id) DO UPDATE SET '
                    'present = excluded.present, checked_at = excluded.checked_at',
                    rows)
                self.conn.commit()
            except Exception as exc:
                logger.debug(f"Önbellek yazılamadı: {exc}")

    def prune(self):
        """TTL'i geçmiş kayıtları sil"""
        if self.conn is None:
            return
        cutoff = time.time() - self.ttl_seconds
        with self._lock:
            try:
                self.conn.execute('DELETE FROM results WHERE checked_at < ?', (cutoff,))
                self.conn.commit()
            except Exception as exc:
                logger.debug(f"Önbellek temizlenemedi: {exc}")

    def close(self):
        """Bağlantıyı kapat - tekrar çağrılabilir"""
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
