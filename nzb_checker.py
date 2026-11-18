#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NZB Checker - NZB dosyalarını kontrol eder ve eksik parçalara göre sınıflandırır
"""

import os
import json
import sys
import math
import time
import logging
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock, Semaphore, local

try:
    from nntplib import NNTP, NNTP_SSL
except ModuleNotFoundError:  # Python 3.13+ nntplib icermez
    from nntp_client import NNTP, NNTP_SSL
from nntp_client import ArticleNotFound


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
        self.providers = self.load_providers()
        self.max_workers = sum(p['connections'] for p in self.providers)
        settings = self.config.get('settings', {})
        self.check_all = bool(settings.get('check_all_articles', False))
        self.sample_count = max(1, int(settings.get('sample_check_count', 20)))
        self.sample_ratio = float(settings.get('sample_ratio', 0.02))
        self.timeout = settings.get('timeout', 30)

        # thread-local bağlantı havuzu: her worker thread provider başına bir bağlantı açar,
        # aynı görevde yeniden kullanır
        self._local = local()
        self._connections: List = []
        # provider başına eşzamanlı bağlantı sınırı (thread'ler ortak havuzdan görev alır)
        self._slots = [Semaphore(p['connections']) for p in self.providers]

        self.events = events or (lambda kind, **data: None)
        self.cancel_event = Event()

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
        """Config dosyasını yükler"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Hata: Config dosyası bulunamadı: {config_path}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"Hata: Config dosyası geçersiz JSON: {e}")
            sys.exit(1)

    def setup_logging(self):
        """Log ayarlarını yapar"""
        log_level = getattr(logging, self.config.get('settings', {}).get('log_level', 'INFO'), logging.INFO)
        root = logging.getLogger()
        if not root.handlers:  # GUI tekrar çalıştırınca handler'ları çoğaltma
            logging.basicConfig(
                level=log_level,
                format='%(asctime)s - %(levelname)s - %(message)s',
                handlers=[
                    logging.FileHandler('nzb_checker.log', encoding='utf-8'),
                    logging.StreamHandler()
                ]
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
            cls = NNTP_SSL if provider['use_ssl'] else NNTP
            server = cls(
                provider['host'],
                port=provider['port'],
                user=provider['username'],
                password=provider['password'],
                timeout=self.timeout
            )
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

    def check_article_exists(self, server: Union[NNTP_SSL, NNTP], message_id: str) -> bool:
        """
        Bir article'ın sunucuda olup olmadığını kontrol eder

        Returns:
            Article varsa True, yoksa False

        Raises:
            NNTPConnectionError/NNTPError: kontrol yapılamadıysa (article yokluğu değil)
        """
        try:
            server.stat(f'<{message_id}>')
            return True
        except ArticleNotFound:
            return False
        except Exception as e:
            # nntplib yolu 430/423 için NNTPError fırlatır
            if any(code in str(e) for code in ('430', '423')):
                return False
            raise

    def _check_chunk(self, index: int, chunk: List[Dict]) -> Tuple[int, int, Set[str]]:
        """
        Bir segment dilimini bir provider üzerinde kontrol eder.

        Returns:
            (provider index, kontrol edilen, eksik message_id kümesi)
        """
        checked = 0
        missing: Set[str] = set()
        with self._slots[index]:
            try:
                conn = self._connection(index)
                for segment in chunk:
                    if self.cancel_event.is_set():
                        break
                    checked += 1
                    if not self.check_article_exists(conn, segment['message_id']):
                        missing.add(segment['message_id'])
                return index, checked, missing
            except Exception:
                self._drop_connection(index)
                raise

    # ------------------------------------------------------------------ dosya işleme

    def move_nzb_file(self, nzb_path: Path, destination_folder: Path):
        """NZB dosyasını hedef klasöre taşır"""
        try:
            dest_path = destination_folder / nzb_path.name
            shutil.move(str(nzb_path), str(dest_path))
            self.logger.info(f"Taşındı: {nzb_path.name} -> {destination_folder.name}")
        except Exception as e:
            self.logger.error(f"Dosya taşıma hatası ({nzb_path}): {e}")

    def _classify(self, nzb_path: Path, status: str, total_segments: int = 0,
                  missing: int = 0, checked: int = 0, started: Optional[float] = None,
                  percent: float = 0.0, per_provider: Optional[List[Dict]] = None):
        """Dosyayı ilgili klasöre taşır ve sayacı artırır"""
        detail = ''
        if per_provider and len(per_provider) > 1:
            detail = ' [' + ', '.join(
                f"{p['name']}: %{p['percent']:.1f}" for p in per_provider) + ']'

        if status == 'complete':
            self.logger.info(f"✓ Tam: {nzb_path.name} ({total_segments} segment){detail}")
        elif status == 'missing':
            self.logger.warning(
                f"✗ Eksik: {nzb_path.name} ({missing}/{total_segments} segment, "
                f"%{percent:.1f} eksik){detail}")
        else:
            self.logger.error(f"✗ Hatalı: {nzb_path.name}")

        self.move_nzb_file(nzb_path, self.folders[status])
        with self.stats_lock:
            self.stats[status] += 1
        self._emit('file_done', name=nzb_path.name, status=status,
                   segments=total_segments, checked=checked, missing=missing,
                   percent=round(percent, 1), providers=per_provider or [],
                   elapsed=round(time.time() - started, 2) if started else 0.0)

    def _finish_file(self, nzb_path: Path, state: Dict):
        """
        Tüm dilimleri biten dosyayı sınıflandırır.

        Segment, kontrol eden provider'ların HEPSİNDE yoksa eksik sayılır
        (başka bir provider'da varsa dosya indirilebilir).
        """
        total = state['total']
        targets = state['targets']
        providers = state['providers']
        started = state['started']

        def summary():
            return [{'name': self.providers[i]['name'],
                     'missing': len(p['missing']),
                     'percent': round(100.0 * len(p['missing']) / p['checked'], 1) if p['checked'] else 0.0}
                    for i, p in providers.items()]

        if self.cancel_event.is_set():
            self._emit('file_done', name=nzb_path.name, status='cancelled',
                       segments=total, checked=0, missing=0, percent=0.0,
                       providers=summary(), elapsed=round(time.time() - started, 2))
            return

        if state.get('failed'):
            return

        # tüm hedef segmentleri kontrol etmiş provider'lar güvenilir kaynak sayılır
        done = [p for p in providers.values() if p['checked'] >= targets]
        if not done:
            self.logger.error(f"✗ Hatalı: {nzb_path.name} (hiçbir provider taraması tamamlanmadı)")
            self._classify(nzb_path, 'error', total, started=started, per_provider=summary())
            return

        checked = max(p['checked'] for p in done)
        common = set.intersection(*[p['missing'] for p in done])
        found_missing = len(common)

        if self.check_all or (checked >= total and len(done) == len(providers)):
            estimated_missing = found_missing              # kesin sayım
        else:
            estimated_missing = int(total * found_missing / checked) if checked else 0

        percent = 100.0 * estimated_missing / total if total else 0.0
        status = 'complete' if estimated_missing == 0 else 'missing'
        self._classify(nzb_path, status, total, estimated_missing, checked, started,
                       percent, summary())

    def process_nzb_files(self):
        """
        Tüm NZB dosyalarını paralel işler.

        Bir dosyanın segmentleri bağlantı sayısına bölünür: tek büyük NZB bile
        tüm bağlantıları kullanır. Worker thread'ler bağlantılarını yeniden kullanır,
        böylece dosya başına yeniden el sıkışma olmaz.
        """
        nzb_folder = Path(self.config['paths']['nzb_folder'])

        if not nzb_folder.exists():
            self.logger.error(f"NZB klasörü bulunamadı: {nzb_folder}")
            self._emit('scan_end', **self.stats, cancelled=False)
            return

        nzb_files = sorted(p for p in nzb_folder.iterdir()
                           if p.is_file() and p.suffix.lower() == '.nzb')

        if not nzb_files:
            self.logger.warning(f"NZB dosyası bulunamadı: {nzb_folder}")
            self._emit('scan_end', **self.stats, cancelled=False)
            return

        total_files = len(nzb_files)
        self.stats['total'] = total_files
        self.logger.info(f"Toplam {total_files} NZB dosyası bulundu")
        self.logger.info(f"{len(self.providers)} provider, toplam paralel bağlantı: {self.max_workers}")
        for provider in self.providers:
            self.logger.info(f"  - {provider['name']}: {provider['host']}:{provider['port']} "
                             f"({provider['connections']} bağlantı, "
                             f"{'SSL' if provider['use_ssl'] else 'düz'})")
        self._emit('scan_start', total=total_files)

        pending: Dict[Path, Dict] = {}
        futures = {}

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                for nzb_path in nzb_files:
                    if self.cancel_event.is_set():
                        break
                    try:
                        segments = self.parse_nzb(str(nzb_path))
                    except Exception as e:
                        self.logger.error(f"NZB okuma hatası ({nzb_path.name}): {e}")
                        self._emit('file_error', name=nzb_path.name, message=str(e))
                        self._classify(nzb_path, 'error')
                        continue

                    targets = self.segments_to_check(segments)

                    state = {'total': len(segments), 'targets': len(targets), 'left': 0,
                             'failed': False, 'started': time.time(), 'providers': {}}
                    chunk_total = 0
                    for index, provider in enumerate(self.providers):
                        size = max(1, math.ceil(len(targets) / provider['connections']))
                        chunks = [targets[i:i + size] for i in range(0, len(targets), size)]
                        state['providers'][index] = {'checked': 0, 'missing': set()}
                        chunk_total += len(chunks)
                        for chunk in chunks:
                            futures[executor.submit(self._check_chunk, index, chunk)] = nzb_path
                    state['left'] = chunk_total

                    self.logger.info(
                        f"[{len(pending) + 1}/{total_files}] Kontrol ediliyor: {nzb_path.name} "
                        f"({len(targets)}/{len(segments)} segment x {len(self.providers)} provider)")

                    pending[nzb_path] = state
                    self._emit('file_start', name=nzb_path.name, segments=len(segments),
                               to_check=len(targets), chunks=chunk_total)

                completed = 0
                for future in as_completed(futures):
                    nzb_path = futures[future]
                    state = pending[nzb_path]

                    try:
                        index, checked, missing_ids = future.result()
                    except Exception as e:
                        if not state['failed']:
                            state['failed'] = True
                            self.logger.error(f"Kontrol hatası ({nzb_path.name}): {e}")
                            self._emit('file_error', name=nzb_path.name, message=str(e))
                            self._classify(nzb_path, 'error', started=state['started'])
                    else:
                        item = state['providers'][index]
                        item['checked'] += checked
                        item['missing'] |= missing_ids

                    state['left'] -= 1
                    if state['left'] == 0:
                        completed += 1
                        self._finish_file(nzb_path, state)
                        self._emit('progress', completed=completed, total=total_files)
                        if completed % 10 == 0:
                            self.logger.info(f"İlerleme: {completed}/{total_files} dosya işlendi")

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.logger.error(f"İşlem hatası: {e}")
            self._emit('fatal', message=str(e))
        finally:
            self._close_connections()
            self._emit('scan_end', **self.stats, cancelled=self.cancel_event.is_set())

    def print_summary(self):
        """İşlem özetini yazdırır"""
        print("\n" + "=" * 60)
        print("İŞLEM ÖZETİ")
        print("=" * 60)
        print(f"Toplam NZB       : {self.stats['total']}")
        print(f"Tam Dosyalar     : {self.stats['complete']}")
        print(f"Eksik Dosyalar   : {self.stats['missing']}")
        print(f"Hatalı Dosyalar  : {self.stats['error']}")
        print("=" * 60)
        print("\nProvider'lar:")
        for provider in self.providers:
            print(f"  - {provider['name']}: {provider['host']}:{provider['port']}")
        print(f"\nKlasörler:")
        print(f"  Tam      : {self.folders['complete']}")
        print(f"  Eksik    : {self.folders['missing']}")
        print(f"  Hata     : {self.folders['error']}")
        print("=" * 60 + "\n")


def main():
    """Ana fonksiyon"""
    print("=" * 60)
    print("NZB Checker - NZB Dosya Kontrolü ve Sınıflandırma")
    print("=" * 60 + "\n")

    config_file = "config.json"

    if len(sys.argv) > 1:
        config_file = sys.argv[1]

    try:
        checker = NZBChecker(config_file)
        checker.process_nzb_files()
        checker.print_summary()
    except KeyboardInterrupt:
        print("\n\nİşlem kullanıcı tarafından durduruldu.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal hata: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
