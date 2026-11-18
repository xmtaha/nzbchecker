#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NZBChecker - NZB dosyalarını paralel kontrol eder ve eksik parçalara göre sınıflandırır
"""

import csv
import json
import logging
import math
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event, Lock, Semaphore, local
from typing import Dict, Iterable, List, Optional, Set, Tuple, Union

from .cache import (ResultCache, default_state_dir, load_scan_history,
                    provider_key, save_scan_history)
from .nntp_client import NNTP, NNTP_SSL


# Boru hattı penceresi: tek seferde gönderilen STAT sayısı. Sunucular
# sınırsız komut kuyruğuna izin vermez; 32 güvenli ve RTT'yi ~32 kat gizler.
PIPELINE_WINDOW = 32


class ConfigError(Exception):
    """Ayar dosyası okunamadı veya kullanılabilir sağlayıcı yok"""


def output_folders(paths: dict) -> Dict[str, Path]:
    """Yapılandırmadaki çıktı klasörlerini (complete/missing/error) çözer"""
    base = Path(paths['nzb_folder'])
    return {key: base / paths[key]
            for key in ('complete', 'missing', 'error')
            if paths.get(f'{key}_folder')}


def iter_nzb_files(nzb_folder: Path, exclude: Iterable[Path] = ()) -> List[Path]:
    """
    NZB dosyalarını alt klasörler dahil listeler.

    Çıktı klasörleri (complete/missing/error) nzb_folder içinde olduğu için
    atlanır; yoksa taşınmış dosyalar bir sonraki taramada yeniden taranır.
    """
    skip = {Path(p).resolve() for p in exclude}
    found = []
    for path in sorted(Path(nzb_folder).rglob('*')):
        if not path.is_file() or path.suffix.lower() != '.nzb':
            continue
        if any(parent.resolve() in skip for parent in path.parents):
            continue
        found.append(path)
    return found


def connect_to_server(provider: dict, timeout: float = 30) -> Union[NNTP_SSL, NNTP]:
    """Provider sözlüğüyle NNTP bağlantısı açar (motor ve GUI aynı yolu kullanır)"""
    cls = NNTP_SSL if provider.get('use_ssl') else NNTP
    return cls(
        provider['host'],
        port=provider.get('port', 563),
        user=provider.get('username') or '',
        password=provider.get('password') or '',
        timeout=timeout
    )


class NZBChecker:
    """NZB dosyalarını kontrol eden ve sınıflandıran sınıf"""

    def __init__(self, config_path: str = "config.json", events=None):
        """
        Args:
            config_path: Config dosyasının yolu
            events: olay dinleyicisi: events(kind: str, **data) - GUI/progress için
        """
        self.config = self.load_config(config_path)
        self.setup_logging()
        self.setup_folders()
        self.stats = {
            'total': 0,
            'complete': 0,
            'missing': 0,
            'error': 0
        }
        self.stats_lock = Lock()
        self.scan_history = load_scan_history()
        self.providers = self._order_providers(self.load_providers())
        if not self.providers:
            raise ConfigError(
                'Kullanılabilir sağlayıcı yok: en az bir sağlayıcı için sunucu adresi girin')
        self.max_workers = sum(p['connections'] for p in self.providers)
        # ön uçuş kontrolü sonucu: bağlanılamayan sağlayıcılar taranmaz
        self._provider_ok = [True] * len(self.providers)
        # rapor için dosya başına sonuçlar
        self.results: List[Dict] = []
        settings = self.config.get('settings', {})
        self.check_all = bool(settings.get('check_all_articles', False))
        self.auto_escalate = bool(settings.get('auto_escalate', False))
        self.sample_count = max(1, int(settings.get('sample_check_count', 20)))
        self.sample_ratio = float(settings.get('sample_ratio', 0.02))
        self.timeout = settings.get('timeout', 30)
        self.move_failed = 0

        # thread-local bağlantı havuzu: her worker thread provider başına bir bağlantı açar,
        # aynı görevde yeniden kullanır
        self._local = local()
        self._connections: List = []
        # provider başına eşzamanlı bağlantı sınırı (thread'ler ortak havuzdan görev alır)
        self._slots = [Semaphore(p['connections']) for p in self.providers]
        self._provider_keys = [provider_key(p) for p in self.providers]

        # sonuç önbelleği: aynı message-id tekrar STAT edilmesin
        cache_enabled = bool(settings.get('cache', True))
        cache_path = settings.get('cache_path') or None
        ttl_days = int(settings.get('cache_ttl_days', 14))
        self.cache = ResultCache(cache_path, ttl_days) if cache_enabled else None

        # sağlayıcı bazlı metrikler (kademeli tarama etkisini ölçmek için)
        self.provider_metrics = [
            {'name': p['name'], 'stat': 0, 'cache': 0, 'errors': 0,
             'missing': 0, 'active': True}
            for p in self.providers
        ]
        self._scan_started = None
        self._scan_segments_done = 0

        self.events = events or (lambda kind, **data: None)
        self.cancel_event = Event()

    def _order_providers(self, providers: List[Dict]) -> List[Dict]:
        """
        Sağlayıcıları geçmiş başarı oranına göre sıralar.

        Kademeli taramada ilk sağlayıcı tüm segmentleri, sonrakiler yalnızca
        bulunamayanları kontrol eder; en yüksek isabetli sağlayıcıyı başa almak
        toplam STAT sayısını düşürür. Geçmişi olmayanlar 0.5 (nötr) sayılır.
        """
        if len(providers) < 2:
            return providers
        stats = (self.scan_history or {}).get('providers') or {}
        if not isinstance(stats, dict):
            return providers

        def rate(index: int, provider: Dict) -> float:
            item = stats.get(provider.get('name')) or {}
            try:
                asked = int(item.get('stat') or 0)
                missing = int(item.get('missing') or 0)
            except (TypeError, ValueError):
                return 0.5
            return (asked - missing) / asked if asked else 0.5

        order = sorted(range(len(providers)),
                       key=lambda i: rate(i, providers[i]), reverse=True)  # stabil
        return [providers[i] for i in order]

    def load_providers(self) -> List[Dict]:
        """
        Tanımlı provider'ları normalize eder.

        Yeni biçim: config['providers'] = [{name, host, port, username, password,
        use_ssl, connections}, ...]
        Eski biçim (tek sunucu): config['usenet_provider'] otomatik listenin içine alınır.
        """
        raw = self.config.get('providers') or [self.config.get('usenet_provider') or {}]
        providers = []
        for index, item in enumerate(raw, 1):
            host = str(item.get('host') or '').strip()
            if not host:
                continue
            providers.append({
                'name': str(item.get('name') or host or f'Provider {index}'),
                'host': host,
                'port': int(item.get('port') or 563),
                'username': item.get('username') or '',
                'password': item.get('password') or '',
                'use_ssl': bool(item.get('use_ssl', True)),
                'connections': max(1, int(item.get('connections') or 10)),
            })
        return providers

    def load_config(self, config_path: str) -> Dict:
        """Config dosyasını yükler

        Raises:
            ConfigError: dosya yok veya geçersiz JSON
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError as e:
            raise ConfigError(f"Config dosyası bulunamadı: {config_path}") from e
        except json.JSONDecodeError as e:
            raise ConfigError(f"Config dosyası geçersiz JSON: {e}") from e

    def setup_logging(self):
        """Log ayarlarını yapar"""
        log_level = getattr(logging, self.config.get('settings', {}).get('log_level', 'INFO'), logging.INFO)
        root = logging.getLogger()
        if not root.handlers:  # GUI tekrar çalıştırınca handler'ları çoğaltma
            handlers = [logging.StreamHandler()]
            # log'u çalışma dizinine değil kullanıcı cache dizinine yaz:
            # AppImage salt-okunur yerden açıldığında da yazılabilir olur
            try:
                log_dir = default_state_dir()
                log_dir.mkdir(parents=True, exist_ok=True)
                handlers.insert(0, logging.FileHandler(
                    log_dir / 'nzb_checker.log', encoding='utf-8'))
            except OSError as e:
                print(f"Log dosyası açılamadı, yalnızca konsola yazılacak: {e}",
                      file=sys.stderr)
            logging.basicConfig(
                level=log_level,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=handlers
            )
        root.setLevel(log_level)
        self.logger = logging.getLogger(__name__)

    def setup_folders(self):
        """Output klasörlerini oluşturur"""
        nzb_folder = Path(self.config['paths']['nzb_folder'])

        self.folders = {
            'complete': nzb_folder / self.config['paths']['complete_folder'],
            'missing': nzb_folder / self.config['paths']['missing_folder'],
            'error': nzb_folder / self.config['paths']['error_folder']
        }

        for folder in self.folders.values():
            folder.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Klasör hazır: {folder}")

    def _emit(self, kind: str, **data):
        """Olay yayınla (GUI/progress). Dinleyici hatası iş akışını bozmaz."""
        try:
            self.events(kind, **data)
        except Exception:
            self.logger.exception(f"Olay dinleyicisi hata verdi ({kind})")

    def cancel(self):
        """Devam eden taramayı durdurur (işlenmemiş dosyalar yerinde kalır)"""
        self.cancel_event.set()

    # ------------------------------------------------------------------ bağlantı

    def connect_to_server(self, index: int) -> Union[NNTP_SSL, NNTP]:
        """Provider'a bağlanır"""
        provider = self.providers[index]

        try:
            server = connect_to_server(provider, self.timeout)
            self.logger.info(f"Sunucuya bağlanıldı: {provider['name']} "
                             f"({provider['host']}:{provider['port']})")
            return server
        except Exception as e:
            self.logger.error(f"Sunucuya bağlanırken hata ({provider['name']}): {e}")
            raise

    def _connection(self, index: int) -> Union[NNTP_SSL, NNTP]:
        """Worker thread'in bağlantısı: ilk kullanımda açılır, sonra yeniden kullanılır"""
        conns = getattr(self._local, 'conns', None)
        if conns is None:
            conns = self._local.conns = {}
        conn = conns.get(index)
        if conn is None:
            conn = self.connect_to_server(index)
            conns[index] = conn
            with self.stats_lock:
                self._connections.append(conn)
        return conn

    def _drop_connection(self, index: int):
        """Bozulan bağlantıyı kapat (sonraki görevde yenisi açılır)"""
        conns = getattr(self._local, 'conns', None) or {}
        conn = conns.pop(index, None)
        if conn is None:
            return
        with self.stats_lock:
            if conn in self._connections:
                self._connections.remove(conn)
        conn.quit()

    def _close_connections(self):
        """Tüm açık bağlantıları kapat"""
        with self.stats_lock:
            conns, self._connections = self._connections, []
        for conn in conns:
            conn.quit()
        self.logger.debug(f"{len(conns)} bağlantı kapatıldı")

    # ------------------------------------------------------------------ nzb

    def parse_nzb(self, nzb_path: str) -> List[Dict]:
        """
        NZB dosyasını parse eder

        Raises:
            ValueError: dosya okunamaz / içinde segment yok
        """
        try:
            tree = ET.parse(nzb_path)
            root = tree.getroot()

            ns = {'nzb': 'http://www.newzbin.com/DTD/2003/nzb'}

            segments = []
            for file_elem in root.findall('nzb:file', ns):
                for segments_elem in file_elem.findall('nzb:segments', ns):
                    for segment in segments_elem.findall('nzb:segment', ns):
                        message_id = (segment.text or '').strip()
                        if message_id:
                            segments.append({
                                'message_id': message_id,
                                'number': segment.get('number'),
                                'bytes': segment.get('bytes')
                            })

            if not segments:
                raise ValueError("NZB içinde segment yok")
            return segments
        except Exception as e:
            raise ValueError(f"NZB okunamadı: {e}") from e

    def segments_to_check(self, segments: List[Dict]) -> List[Dict]:
        """
        Kontrol edilecek segmentleri seçer: check_all ise hepsi, değilse
        dosyaya eşit yayılmış örnek (ilk ve son segment dahil).
        """
        total = len(segments)
        # örnek sayısı: taban (sample_check_count) veya dosyanın sample_ratio kadarı - büyüğü
        sample = min(total, max(self.sample_count, int(total * self.sample_ratio)))

        if self.check_all or sample >= total:
            return segments

        step = (total - 1) / (sample - 1) if sample > 1 else 0
        if sample > 1:
            indexes = sorted({round(i * step) for i in range(sample)})
        else:
            indexes = [total - 1]
        return [segments[i] for i in indexes]

    def _check_chunk(self, index: int, message_ids: List[str]) -> Tuple[Set[str], Set[str]]:
        """
        Bir segment dilimini tek provider üzerinde kontrol eder.

        Önce önbelleğe bakılır; önbellekte olmayan id'ler için STAT gönderilir.
        Dilim içinde hata olursa sonuçlar kaydedilmez (sonraki provider tekrar sorar).

        Returns:
            (kontrol edilen id'ler, eksik id'ler)
        """
        key = self._provider_keys[index]
        cache = self.cache
        checked: Set[str] = set()
        missing: Set[str] = set()
        updates: Dict[str, bool] = {}

        with self._slots[index]:
            try:
                cached = cache.get_many(key, message_ids) if cache is not None else {}
                cache_hits = 0
                for message_id in message_ids:
                    if message_id not in cached:
                        continue
                    cache_hits += 1
                    checked.add(message_id)
                    if not cached[message_id]:
                        missing.add(message_id)

                conn = None
                to_ask = [mid for mid in message_ids if mid not in cached]
                for start in range(0, len(to_ask), PIPELINE_WINDOW):
                    if self.cancel_event.is_set():
                        break
                    window = to_ask[start:start + PIPELINE_WINDOW]
                    if conn is None:
                        conn = self._connection(index)
                    for message_id, exists in zip(window, conn.stat_many(window)):
                        checked.add(message_id)
                        updates[message_id] = exists
                        if not exists:
                            missing.add(message_id)
                if cache_hits or updates:
                    with self.stats_lock:
                        self.provider_metrics[index]['cache'] += cache_hits
                        self.provider_metrics[index]['stat'] += len(updates)
                return checked, missing
            except Exception:
                self._drop_connection(index)
                raise
            finally:
                if cache is not None and updates:
                    cache.put_many(key, updates)

    # ------------------------------------------------------------------ dosya işleme

    def move_nzb_file(self, nzb_path: Path, destination_folder: Path) -> bool:
        """NZB dosyasını hedef klasöre taşır (ad çakışırsa numaralandırır)"""
        try:
            destination = destination_folder / nzb_path.name
            with self.stats_lock:
                if destination.exists():
                    stem, suffix = nzb_path.stem, nzb_path.suffix
                    for number in range(1, 1000):
                        candidate = destination_folder / f'{stem} ({number}){suffix}'
                        if not candidate.exists():
                            destination = candidate
                            break
                    else:
                        raise FileExistsError(f"benzersiz ad bulunamadı: {destination}")
                shutil.move(str(nzb_path), str(destination))
            self.logger.info(f"Taşındı: {nzb_path.name} -> {destination_folder.name}/{destination.name}")
            return True
        except Exception as e:
            self.logger.error(f"Dosya taşıma hatası ({nzb_path}): {e}")
            return False

    def _display_name(self, nzb_path: Path) -> str:
        """Alt klasördeki dosyaları ayırt etmek için göreli yol (üst düzeyde ad)"""
        try:
            return str(nzb_path.relative_to(Path(self.config['paths']['nzb_folder'])))
        except ValueError:
            return nzb_path.name

    def _classify(self, nzb_path: Path, status: str, total_segments: int = 0,
                  missing: int = 0, checked: int = 0, started: Optional[float] = None,
                  percent: float = 0.0, per_provider: Optional[List[Dict]] = None,
                  missing_ids: Optional[List[str]] = None,
                  checked_all: Optional[bool] = None):
        """Dosyayı ilgili klasöre taşır, sayacı artırır ve rapor satırını saklar"""
        name = self._display_name(nzb_path)
        detail = ''
        if per_provider and len(per_provider) > 1:
            detail = ' [' + ', '.join(
                f"{p['name']}: %{p['percent']:.1f}" for p in per_provider) + ']'

        if status == 'complete':
            self.logger.info(f"✓ Tam: {name} ({total_segments} segment){detail}")
        elif status == 'missing':
            self.logger.warning(
                f"✗ Eksik: {name} ({missing}/{total_segments} segment, "
                f"%{percent:.1f} eksik){detail}")
        else:
            self.logger.error(f"✗ Hatalı: {name}")

        moved = self.move_nzb_file(nzb_path, self.folders[status])
        if not moved:
            self.move_failed += 1
        elapsed = round(time.time() - started, 2) if started else 0.0
        self.results.append({
            'name': name,
            'status': status,
            'segments': total_segments,
            'checked': checked,
            'missing': missing,
            'percent': round(percent, 1),
            'providers': per_provider or [],
            'elapsed': elapsed,
            'moved': moved,
            'checked_all': self.check_all if checked_all is None else checked_all,
            'missing_ids': missing_ids or [],
        })
        with self.stats_lock:
            self.stats[status] += 1
        self._emit('file_done', name=name, status=status,
                   segments=total_segments, checked=checked, missing=missing,
                   percent=round(percent, 1), providers=per_provider or [],
                   moved=moved, elapsed=elapsed,
                   checked_all=self.check_all if checked_all is None else checked_all)

    def _new_state(self, segments: List[Dict], targets: List[Dict]) -> Dict:
        """Bir dosyanın kademeli kontrol durumu"""
        return {
            'total': len(segments),
            'ids': [s['message_id'] for s in targets],
            'all_ids': [s['message_id'] for s in segments],
            'escalated': False,      # örnek tarama tam taramaya yükseltildi mi
            'found': set(),          # herhangi bir sağlayıcının bulduğu id'ler
            'answered': set(),       # en az bir sağlayıcının yanıtladığı id'ler
            'stage': 0,              # sıradaki provider
            'active': None,          # uçuşta dilimi olan provider
            'left': 0,               # aktif aşamadaki kalan dilim
            'stage_checked': set(),
            'stage_missing': set(),
            'stage_failed': False,
            'providers': {},         # index -> {'checked': int, 'missing': set}
            'done': set(),           # aşamasını hatasız bitiren provider'lar
            'started': time.time(),
            'last_progress': 0.0,
            'finished': False,
            'counted': False,
        }

    def _start_next_stage(self, executor: ThreadPoolExecutor, nzb_path: Path,
                          state: Dict, futures: Dict):
        """
        Sıradaki provider için yalnızca henüz bulunamayan segmentleri gönderir.

        Kademeli tarama: P1 tüm örnekleri kontrol eder, P2 yalnızca P1'in
        bulamadıklarını... Böylece sağlam dosyalarda STAT sayısı sağlayıcı
        sayısıyla çarpılmaz.
        """
        while True:
            if self.cancel_event.is_set():
                self._finish_file(nzb_path, state)
                return
            if state['stage'] >= len(self.providers):
                if self._escalate(nzb_path, state):
                    continue
                self._finish_file(nzb_path, state)
                return

            index = state['stage']
            if not self._provider_ok[index]:  # ön uçuşta bağlanılamadı
                state['stage'] += 1
                continue
            candidates = [mid for mid in state['ids'] if mid not in state['found']]
            if not candidates:  # bu sağlayıcıya soracak bir şey kalmadı
                state['done'].add(index)
                state['providers'].setdefault(index, {'checked': 0, 'missing': set()})
                state['stage'] += 1
                continue

            state['stage'] += 1
            state['active'] = index
            state['stage_checked'] = set()
            state['stage_missing'] = set()
            state['stage_failed'] = False
            state['providers'].setdefault(index, {'checked': 0, 'missing': set()})

            size = max(1, math.ceil(len(candidates) / self.providers[index]['connections']))
            chunks = [candidates[i:i + size] for i in range(0, len(candidates), size)]
            state['left'] = len(chunks)
            for chunk in chunks:
                futures[executor.submit(self._check_chunk, index, chunk)] = (nzb_path, index)
            return

    def _stage_finished(self, executor: ThreadPoolExecutor, nzb_path: Path,
                        state: Dict, futures: Dict):
        """Aktif aşama bitti: bulunanları işle, sonraki sağlayıcıya geç"""
        index = state['active']
        if index is not None:
            # yalnızca gerçekten yanıtlanan id'ler "bulundu" sayılır
            state['found'] |= (state['stage_checked'] - state['stage_missing'])
            if state['stage_failed']:
                self.logger.warning(
                    f"{nzb_path.name}: {self.providers[index]['name']} taraması "
                    f"tamamlanamadı, kalan sağlayıcılarla devam ediliyor")
            else:
                state['done'].add(index)
        self._start_next_stage(executor, nzb_path, state, futures)

    def _escalate(self, nzb_path: Path, state: Dict) -> bool:
        """
        Örnek taramada eksik görülen dosyayı tam taramaya yükseltir.

        'auto_escalate' ayarı kapalıysa (varsayılan) yükseltme yapılmaz: örnek
        tarama sonucu olduğu gibi raporlanır. Açıksa örnekte tek bir eksik bile
        şüpheli sayılır ve dosya tam taranır. Yükseltme bir kez yapılır.

        Returns:
            True ise aşamalar baştan kuruldu, tarama devam etmeli
        """
        if self.check_all or not self.auto_escalate or state['escalated']:
            return False
        # örnekte hiç eksik yoksa yükseltmeye gerek yok (zaten "Tam")
        if all(mid in state['found'] for mid in state['ids']):
            return False
        rest = [mid for mid in state['all_ids'] if mid not in state['ids']]
        if not rest:  # zaten tüm segmentler taranmış (örnek = tam)
            state['escalated'] = True
            return False
        if not any(self._provider_ok):
            return False
        self.logger.info(
            f"{nzb_path.name}: örnekte eksik segment görüldü, tam taramaya geçildi "
            f"({len(rest)} ek segment)")
        state['escalated'] = True
        state['ids'] = list(state['all_ids'])
        state['stage'] = 0
        state['active'] = None
        state['left'] = 0
        state['stage_checked'] = set()
        state['stage_missing'] = set()
        state['stage_failed'] = False
        state['done'] = set()
        state['providers'] = {}
        return True

    def _finish_file(self, nzb_path: Path, state: Dict):
        """
        Tüm aşamalar biten dosyayı sınıflandırır.

        Bir segment ancak kontrol edilebilen sağlayıcıların hiçbirinde
        bulunamazsa eksik sayılır. Hiçbir sağlayıcı aşamasını tamamlayamadıysa
        dosya hatalı kabul edilir (ağ/kimlik sorunu).
        """
        if state['finished']:
            return
        state['finished'] = True

        total = state['total']
        ids = state['ids']
        started = state['started']

        def summary():
            # yalnızca gerçekten taranan sağlayıcıları göster
            out = []
            for i in sorted(state['providers']):
                info = state['providers'][i]
                checked = info['checked']
                pm = self.provider_metrics[i]
                out.append({
                    'name': self.providers[i]['name'],
                    'checked': checked,
                    'missing': len(info['missing']),
                    'percent': round(100.0 * len(info['missing']) / checked, 1) if checked else 0.0,
                    'stat': pm['stat'],
                    'cache': pm['cache'],
                    'errors': pm['errors'],
                })
            return out

        if self.cancel_event.is_set():
            self._emit('file_done', name=self._display_name(nzb_path), status='cancelled',
                       segments=total, checked=0, missing=0, percent=0.0,
                       providers=summary(), moved=False,
                       elapsed=round(time.time() - started, 2))
            return

        if not state['done']:
            self.logger.error(
                f"✗ Hatalı: {nzb_path.name} (hiçbir sağlayıcı taraması tamamlanmadı)")
            self._classify(nzb_path, 'error', total, started=started,
                           per_provider=summary())
            return

        found_missing = sum(1 for mid in ids if mid not in state['found'])
        answered = len(state['answered'])
        base = len(ids) if answered >= len(ids) else answered
        if base and base < total:
            estimated_missing = min(total, int(round(total * found_missing / base)))
        else:
            estimated_missing = found_missing

        percent = 100.0 * estimated_missing / total if total else 0.0
        status = 'complete' if estimated_missing == 0 else 'missing'
        missing_ids = sorted(mid for mid in ids if mid not in state['found'])
        self._classify(nzb_path, status, total, estimated_missing, answered, started,
                       percent, summary(), missing_ids=missing_ids,
                       checked_all=self.check_all or state['escalated'])

    def process_nzb_files(self):
        """
        Tüm NZB dosyalarını paralel işler.

        Bir dosyanın segmentleri sağlayıcılara kademeli dağıtılır: bir sonraki
        sağlayıcıya yalnızca öncekilerin bulamadığı segmentler sorulur. Tek
        büyük NZB bile tüm bağlantıları kullanır; worker thread'ler bağlantılarını
        yeniden kullanır, böylece dosya başına yeniden el sıkışma olmaz.
        """
        nzb_folder = Path(self.config['paths']['nzb_folder'])

        if not nzb_folder.exists():
            self.logger.error(f"NZB klasörü bulunamadı: {nzb_folder}")
            self._scan_started = time.time()
            self._scan_segments_done = 0
            self._emit_scan_end(cancelled=False)
            return

        # alt klasörler dahil tara; çıktı klasörleri (complete/missing/error) hariç
        nzb_files = iter_nzb_files(nzb_folder, self.folders.values())

        self._scan_started = time.time()
        self._scan_segments_done = 0
        self.results = []
        self.move_failed = 0
        self.provider_metrics = [
            {'name': p['name'], 'stat': 0, 'cache': 0, 'errors': 0,
             'missing': 0, 'active': True}
            for p in self.providers
        ]

        if not nzb_files:
            self.logger.warning(f"NZB dosyası bulunamadı: {nzb_folder}")
            self._emit_scan_end(cancelled=False)
            return

        total_files = len(nzb_files)
        self.stats = {'total': total_files, 'complete': 0, 'missing': 0, 'error': 0}
        self.logger.info(f"Toplam {total_files} NZB dosyası bulundu")
        self.logger.info(f"{len(self.providers)} provider, toplam paralel bağlantı: {self.max_workers}")
        if self.cache is not None and self.cache.enabled:
            self.logger.info(f"Sonuç önbelleği: {self.cache.path}")
        for provider in self.providers:
            self.logger.info(f"  - {provider['name']}: {provider['host']}:{provider['port']} "
                             f"({provider['connections']} bağlantı, "
                             f"{'SSL' if provider['use_ssl'] else 'düz'})")
        self._emit('scan_start', total=total_files, started=self._scan_started)

        self._provider_ok = self._preflight()
        if not any(self._provider_ok):
            message = ('Hiçbir sağlayıcıya bağlanılamadı (sunucu/hesap ayarlarını '
                       've ağ bağlantısını kontrol edin). Dosyalar taşınmadı.')
            self.logger.error(message)
            self._emit_scan_end(cancelled=False, error=message)
            return

        usable = sum(1 for ok in self._provider_ok if ok)
        workers = max(1, sum(p['connections'] for i, p in enumerate(self.providers)
                             if self._provider_ok[i]))
        if self.cache is not None:
            self.cache.prune()

        pending: Dict[Path, Dict] = {}
        futures: Dict = {}
        completed = 0

        def done_file(nzb_path: Path, state: Dict):
            nonlocal completed
            if not state.get('finished') or state.get('counted'):
                return
            state['counted'] = True
            completed += 1
            self._emit('progress', completed=completed, total=total_files)
            if completed % 10 == 0:
                self.logger.info(f"İlerleme: {completed}/{total_files} dosya işlendi")

        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for nzb_path in nzb_files:
                    if self.cancel_event.is_set():
                        break
                    try:
                        segments = self.parse_nzb(str(nzb_path))
                    except Exception as e:
                        self.logger.error(f"NZB okuma hatası ({nzb_path.name}): {e}")
                        self._emit('file_error', name=nzb_path.name, message=str(e))
                        self._classify(nzb_path, 'error')
                        completed += 1
                        self._emit('progress', completed=completed, total=total_files)
                        continue

                    targets = self.segments_to_check(segments)
                    state = self._new_state(segments, targets)
                    pending[nzb_path] = state

                    self.logger.info(
                        f"[{len(pending)}/{total_files}] Kontrol ediliyor: {nzb_path.name} "
                        f"({len(targets)}/{len(segments)} segment x {usable} provider)")
                    self._emit('file_start', name=nzb_path.name, segments=len(segments),
                               to_check=len(targets))

                    self._start_next_stage(executor, nzb_path, state, futures)
                    done_file(nzb_path, state)

                while futures:
                    done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                    for future in done:
                        nzb_path, index = futures.pop(future)
                        state = pending[nzb_path]
                        state['left'] -= 1

                        try:
                            checked, missing = future.result()
                        except Exception as e:
                            state['stage_failed'] = True
                            with self.stats_lock:
                                if index < len(self.provider_metrics):
                                    self.provider_metrics[index]['errors'] += 1
                            self.logger.error(
                                f"Kontrol hatası ({nzb_path.name}, "
                                f"{self.providers[index]['name']}): {e}")
                            self._emit('file_error', name=nzb_path.name, message=str(e))
                        else:
                            info = state['providers'][index]
                            info['checked'] += len(checked)
                            info['missing'] |= missing
                            with self.stats_lock:
                                if index < len(self.provider_metrics):
                                    self.provider_metrics[index]['missing'] += len(missing)
                            state['stage_checked'] |= checked
                            state['stage_missing'] |= missing
                            state['answered'] |= checked
                            with self.stats_lock:
                                self._scan_segments_done += len(checked)
                            now = time.time()
                            if (state['answered'] and
                                    (now - state['last_progress'] >= 0.15
                                     or len(state['answered']) >= len(state['ids']))):
                                state['last_progress'] = now
                                self._emit('file_progress', name=nzb_path.name,
                                           checked=len(state['answered']),
                                           to_check=len(state['ids']),
                                           segments=state['total'],
                                           scan_checked=self._scan_segments_done,
                                           elapsed=self._scan_elapsed())

                        if state['left'] == 0:
                            self._stage_finished(executor, nzb_path, state, futures)
                            done_file(nzb_path, state)

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.logger.error(f"İşlem hatası: {e}")
            self._emit('fatal', message=str(e))
        finally:
            self._close_connections()
            if self.cache is not None:
                self.cache.close()
            self._emit_scan_end(cancelled=self.cancel_event.is_set())

    def _preflight(self) -> List[bool]:
        """
        Her sağlayıcıya tek bağlantıyla erişilebiliyor mu?

        Kimlik/erişim hatası olan sağlayıcı taranmaz; aksi halde her segment
        dilimi yeniden bağlanmayı dener ve tarama yavaşlar.
        """
        def probe(index: int) -> bool:
            try:
                server = self.connect_to_server(index)
                try:
                    server.quit()
                except Exception:
                    pass
                return True
            except Exception as exc:
                self.logger.error(
                    f"Sağlayıcı kullanılamıyor ({self.providers[index]['name']}): {exc}")
                return False

        with ThreadPoolExecutor(max_workers=len(self.providers)) as executor:
            result = list(executor.map(probe, range(len(self.providers))))
        for i, ok in enumerate(result):
            if i < len(self.provider_metrics):
                self.provider_metrics[i]['active'] = ok
        return result

    def _scan_elapsed(self) -> float:
        if not self._scan_started:
            return 0.0
        return round(time.time() - self._scan_started, 2)

    def _scan_metrics(self) -> Dict:
        segments = sum(int(r.get('segments') or 0) for r in self.results)
        checked = sum(int(r.get('checked') or 0) for r in self.results)
        missing = sum(int(r.get('missing') or 0) for r in self.results)
        return {
            'elapsed': self._scan_elapsed(),
            'segments': segments,
            'checked_segments': checked,
            'missing_segments': missing,
            'provider_stats': [dict(p) for p in self.provider_metrics],
        }

    def _emit_scan_end(self, cancelled: bool = False, error: Optional[str] = None):
        payload = dict(self.stats)
        payload.update({
            'cancelled': cancelled,
            'move_failed': self.move_failed,
        })
        payload.update(self._scan_metrics())
        if error:
            payload['error'] = error
        if not cancelled and not error:
            save_scan_history({
                'finished_at': time.time(),
                'elapsed': payload.get('elapsed'),
                'stats': dict(self.stats),
                'providers': {p['name']: dict(p) for p in self.provider_metrics},
            })
        self._emit('scan_end', **payload)

    def export_report(self, destination) -> Dict:
        """
        Son taramanın sonuçlarını dışa aktarır.

        `rapor.csv` (tüm dosyalar) ve `eksik-segmentler/<dosya>.txt`
        (eksik message-id'ler, satır başına bir tane) yazılır.
        """
        folder = Path(destination).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        csv_path = folder / 'rapor.csv'
        missing_dir = folder / 'eksik-segmentler'
        missing_files = 0
        missing_count = 0

        with open(csv_path, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['dosya', 'durum', 'segment', 'kontrol', 'eksik',
                             'eksik_yuzde', 'saglayicilar', 'sure_sn', 'mod',
                             'tasinidi'])
            for item in self.results:
                providers = ' · '.join(
                    f"{p['name']} %{p['percent']:.1f}"
                    for p in item.get('providers') or [])
                writer.writerow([
                    item['name'], item['status'], item['segments'],
                    item['checked'], item['missing'], item['percent'],
                    providers, item['elapsed'],
                    'tam' if item.get('checked_all') else 'ornek',
                    'evet' if item.get('moved') else 'hayir',
                ])
                ids = item.get('missing_ids') or []
                if ids:
                    missing_files += 1
                    missing_count += len(ids)
                    missing_dir.mkdir(parents=True, exist_ok=True)
                    # alt klasör adları dosya adına sığsın: a/b.nzb -> a_b.nzb.txt
                    flat = item['name'].replace('/', '_').replace('\\', '_')
                    (missing_dir / f'{flat}.txt').write_text(
                        '\n'.join(ids) + '\n', encoding='utf-8')

        info = {'folder': str(folder), 'csv': str(csv_path),
                'files': len(self.results), 'missing_files': missing_files,
                'missing_ids': missing_count}
        info.update(self._scan_metrics())
        return info

    def print_summary(self):
        """İşlem özetini yazdırır"""
        metrics = self._scan_metrics()
        failed_moves = [r['name'] for r in self.results
                        if not r.get('moved', True) and r.get('status') != 'cancelled']
        missing = [r for r in self.results if r.get('status') == 'missing']

        print("\n" + "=" * 60)
        print("İŞLEM ÖZETİ")
        print("=" * 60)
        print(f"Toplam NZB       : {self.stats['total']}")
        print(f"Tam Dosyalar     : {self.stats['complete']}")
        print(f"Eksik Dosyalar   : {self.stats['missing']}")
        print(f"Hatalı Dosyalar  : {self.stats['error']}")
        print(f"Tarama süresi    : {metrics['elapsed']} sn")
        print(f"Segment          : {metrics['checked_segments']}/{metrics['segments']} kontrol, "
              f"{metrics['missing_segments']} eksik")
        if failed_moves:
            print(f"Taşınamayan      : {len(failed_moves)} (kaynak klasörde bırakıldı)")
            for name in failed_moves[:10]:
                print(f"  ! {name}")
        elif self.move_failed:
            print(f"Taşınamayan      : {self.move_failed}")
        if missing:
            print("\nEksik segment özeti:")
            for item in missing[:10]:
                print(f"  - {item['name']}: {item['missing']}/{item['segments']} eksik "
                      f"(%{item['percent']:.1f})")
            if len(missing) > 10:
                print(f"  ... ve {len(missing) - 10} dosya daha")

        print("\nSağlayıcı metrikleri:")
        for p in metrics.get('provider_stats') or []:
            durum = 'aktif' if p.get('active', True) else 'erişilemedi'
            print(f"  - {p['name']}: {p['stat']} STAT, {p['cache']} önbellek, "
                  f"{p['errors']} hata ({durum})")
        print(f"\nKlasörler:")
        print(f"  Tam      : {self.folders['complete']}")
        print(f"  Eksik    : {self.folders['missing']}")
        print(f"  Hata     : {self.folders['error']}")
        print("=" * 60 + "\n")
