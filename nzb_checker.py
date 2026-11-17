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
from typing import Dict, List, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event, Lock, local

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
        self.max_workers = max(1, int(self.config['usenet_provider'].get('connections', 10)))
        settings = self.config.get('settings', {})
        self.check_all = bool(settings.get('check_all_articles', False))
        self.sample_count = max(1, int(settings.get('sample_check_count', 20)))
        self.sample_ratio = float(settings.get('sample_ratio', 0.02))
        self.timeout = settings.get('timeout', 30)

        # thread-local bağlantı havuzu: her worker thread bir bağlantı açar ve yeniden kullanır
        self._local = local()
        self._connections: List = []

        self.events = events or (lambda kind, **data: None)
        self.cancel_event = Event()

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

    def connect_to_server(self) -> Union[NNTP_SSL, NNTP]:
        """Usenet sunucusuna bağlanır"""
        provider = self.config['usenet_provider']

        try:
            cls = NNTP_SSL if provider['use_ssl'] else NNTP
            server = cls(
                provider['host'],
                port=provider['port'],
                user=provider['username'],
                password=provider['password'],
                timeout=self.timeout
            )
            self.logger.info(f"Sunucuya bağlanıldı: {provider['host']}")
            return server
        except Exception as e:
            self.logger.error(f"Sunucuya bağlanırken hata: {e}")
            raise

    def _connection(self) -> Union[NNTP_SSL, NNTP]:
        """Worker thread'in bağlantısı: ilk kullanımda açılır, sonra yeniden kullanılır"""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = self.connect_to_server()
            self._local.conn = conn
            with self.stats_lock:
                self._connections.append(conn)
        return conn

    def _drop_connection(self):
        """Bozulan bağlantıyı kapat (sonraki görevde yenisi açılır)"""
        conn = getattr(self._local, 'conn', None)
        self._local.conn = None
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

    def _check_chunk(self, nzb_path: Path, chunk: List[Dict]) -> Tuple[int, int]:
        """Bir segment dilimini kendi bağlantısıyla kontrol eder -> (eksik, kontrol edilen)"""
        checked = 0
        try:
            conn = self._connection()
            missing = 0
            for segment in chunk:
                if self.cancel_event.is_set():
                    break
                checked += 1
                if not self.check_article_exists(conn, segment['message_id']):
                    missing += 1
            return missing, checked
        except Exception:
            self._drop_connection()
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
                  missing: int = 0, checked: int = 0, started: Optional[float] = None):
        """Dosyayı ilgili klasöre taşır ve sayacı artırır"""
        if status == 'complete':
            self.logger.info(f"✓ Tam: {nzb_path.name} ({total_segments} segment)")
        elif status == 'missing':
            self.logger.warning(
                f"✗ Eksik: {nzb_path.name} ({missing}/{total_segments} segment eksik)")
        else:
            self.logger.error(f"✗ Hatalı: {nzb_path.name}")

        self.move_nzb_file(nzb_path, self.folders[status])
        with self.stats_lock:
            self.stats[status] += 1
        self._emit('file_done', name=nzb_path.name, status=status,
                   segments=total_segments, checked=checked, missing=missing,
                   elapsed=round(time.time() - started, 2) if started else 0.0)

    def _finish_file(self, nzb_path: Path, state: Dict):
        """Tüm dilimleri biten dosyayı sınıflandırır"""
        total = state['total']
        checked = state['checked']
        missing = state['missing']

        if state.get('failed'):
            return

        if self.cancel_event.is_set():
            self._emit('file_done', name=nzb_path.name, status='cancelled',
                       segments=total, checked=checked, missing=missing,
                       elapsed=round(time.time() - state['started'], 2))
            return

        if self.check_all or checked >= total:
            estimated_missing = missing          # kesin sayım
        else:
            estimated_missing = int(total * missing / checked) if checked else 0

        if estimated_missing == 0:
            self._classify(nzb_path, 'complete', total, 0, checked, state['started'])
        else:
            self._classify(nzb_path, 'missing', total, estimated_missing, checked, state['started'])

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
        self.logger.info(f"Paralel bağlantı sayısı: {self.max_workers}")
        self._emit('scan_start', total=nzb_files and total_files)

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
                    chunk_size = max(1, math.ceil(len(targets) / self.max_workers))
                    chunks = [targets[i:i + chunk_size] for i in range(0, len(targets), chunk_size)]

                    self.logger.info(
                        f"[{len(pending) + 1}/{total_files}] Kontrol ediliyor: {nzb_path.name} "
                        f"({len(targets)}/{len(segments)} segment, {len(chunks)} dilim)")

                    pending[nzb_path] = {'total': len(segments), 'left': len(chunks),
                                         'checked': 0, 'missing': 0, 'failed': False,
                                         'started': time.time()}
                    self._emit('file_start', name=nzb_path.name, segments=len(segments),
                               to_check=len(targets), chunks=len(chunks))
                    for chunk in chunks:
                        futures[executor.submit(self._check_chunk, nzb_path, chunk)] = nzb_path

                completed = 0
                for future in as_completed(futures):
                    nzb_path = futures[future]
                    state = pending[nzb_path]

                    try:
                        missing, checked = future.result()
                    except Exception as e:
                        if not state['failed']:
                            state['failed'] = True
                            self.logger.error(f"Kontrol hatası ({nzb_path.name}): {e}")
                            self._emit('file_error', name=nzb_path.name, message=str(e))
                            self._classify(nzb_path, 'error', started=state['started'])
                    else:
                        state['missing'] += missing
                        state['checked'] += checked

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
