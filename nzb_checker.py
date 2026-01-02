#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NZB Checker - NZB dosyalarını kontrol eder ve eksik parçalara göre sınıflandırır
"""

import os
import json
import sys
import logging
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple, Union
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

try:
    from nntplib import NNTP, NNTP_SSL
except ModuleNotFoundError:
    from nntp_client import NNTP, NNTP_SSL


class NZBChecker:
    """NZB dosyalarını kontrol eden ve sınıflandıran sınıf"""
    
    def __init__(self, config_path: str = "config.json"):
        """
        Args:
            config_path: Config dosyasının yolu
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
        self.max_workers = self.config['usenet_provider'].get('connections', 10)
    
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
        log_level = getattr(logging, self.config['settings']['log_level'], logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('nzb_checker.log', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
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
    
    def connect_to_server(self) -> Union[NNTP_SSL, NNTP]:
        """Usenet sunucusuna bağlanır"""
        provider = self.config['usenet_provider']
        
        try:
            if provider['use_ssl']:
                server = NNTP_SSL(
                    provider['host'],
                    port=provider['port'],
                    user=provider['username'],
                    password=provider['password']
                )
            else:
                server = NNTP(
                    provider['host'],
                    port=provider['port'],
                    user=provider['username'],
                    password=provider['password']
                )
            
            self.logger.info(f"Sunucuya bağlanıldı: {provider['host']}")
            return server
        except Exception as e:
            self.logger.error(f"Sunucuya bağlanırken hata: {e}")
            raise
    
    def parse_nzb(self, nzb_path: str) -> List[Dict]:
        """
        NZB dosyasını parse eder
        
        Args:
            nzb_path: NZB dosyasının yolu
            
        Returns:
            Segment bilgilerinin listesi
        """
        try:
            tree = ET.parse(nzb_path)
            root = tree.getroot()
            
            ns = {'nzb': 'http://www.newzbin.com/DTD/2003/nzb'}
            
            segments = []
            for file_elem in root.findall('nzb:file', ns):
                for segments_elem in file_elem.findall('nzb:segments', ns):
                    for segment in segments_elem.findall('nzb:segment', ns):
                        message_id = segment.text.strip()
                        segments.append({
                            'message_id': message_id,
                            'number': segment.get('number'),
                            'bytes': segment.get('bytes')
                        })
            
            return segments
        except ET.ParseError as e:
            self.logger.error(f"NZB parse hatası ({nzb_path}): {e}")
            return []
        except Exception as e:
            self.logger.error(f"NZB okuma hatası ({nzb_path}): {e}")
            return []
    
    def check_article_exists(self, server: Union[NNTP_SSL, NNTP], message_id: str) -> bool:
        """
        Bir article'ın sunucuda olup olmadığını kontrol eder
        
        Args:
            server: NNTP sunucu bağlantısı
            message_id: Kontrol edilecek message ID
            
        Returns:
            Article varsa True, yoksa False
        """
        try:
            response = server.stat(f'<{message_id}>')
            return True
        except Exception:
            return False
    
    def check_nzb_file(self, nzb_path: str, server: Union[NNTP_SSL, NNTP]) -> Tuple[bool, int, int]:
        """
        Bir NZB dosyasını kontrol eder
        
        Args:
            nzb_path: NZB dosyasının yolu
            server: NNTP sunucu bağlantısı
            
        Returns:
            (tamamlanmış mı, toplam segment, eksik segment)
        """
        segments = self.parse_nzb(nzb_path)
        
        if not segments:
            return False, 0, 0
        
        total_segments = len(segments)
        check_all = self.config['settings']['check_all_articles']
        sample_count = self.config['settings']['sample_check_count']
        
        if check_all:
            segments_to_check = segments
        else:
            step = max(1, total_segments // sample_count)
            segments_to_check = segments[::step][:sample_count]
        
        missing_count = 0
        checked_count = 0
        
        for segment in segments_to_check:
            checked_count += 1
            if not self.check_article_exists(server, segment['message_id']):
                missing_count += 1
        
        if check_all:
            estimated_missing = missing_count
        else:
            missing_ratio = missing_count / checked_count if checked_count > 0 else 0
            estimated_missing = int(total_segments * missing_ratio)
        
        is_complete = estimated_missing == 0
        
        return is_complete, total_segments, estimated_missing
    
    def move_nzb_file(self, nzb_path: str, destination_folder: Path):
        """NZB dosyasını hedef klasöre taşır"""
        try:
            dest_path = destination_folder / Path(nzb_path).name
            shutil.move(nzb_path, dest_path)
            self.logger.info(f"Taşındı: {Path(nzb_path).name} -> {destination_folder.name}")
        except Exception as e:
            self.logger.error(f"Dosya taşıma hatası ({nzb_path}): {e}")
    
    def process_single_nzb(self, nzb_path: Path, idx: int, total: int) -> Tuple[str, str, int, int]:
        """
        Tek bir NZB dosyasını işler (thread-safe)
        
        Args:
            nzb_path: NZB dosyasının yolu
            idx: Dosya indeksi
            total: Toplam dosya sayısı
            
        Returns:
            (durum, dosya_adı, toplam_segment, eksik_segment)
            durum: 'complete', 'missing', 'error'
        """
        try:
            server = self.connect_to_server()
            
            self.logger.info(f"[{idx}/{total}] Kontrol ediliyor: {nzb_path.name}")
            
            try:
                is_complete, total_segments, missing = self.check_nzb_file(str(nzb_path), server)
                
                if is_complete:
                    self.logger.info(f"✓ Tam: {nzb_path.name} ({total_segments} segment)")
                    self.move_nzb_file(str(nzb_path), self.folders['complete'])
                    
                    with self.stats_lock:
                        self.stats['complete'] += 1
                    
                    return 'complete', nzb_path.name, total_segments, missing
                else:
                    self.logger.warning(
                        f"✗ Eksik: {nzb_path.name} "
                        f"({missing}/{total_segments} segment eksik)"
                    )
                    self.move_nzb_file(str(nzb_path), self.folders['missing'])
                    
                    with self.stats_lock:
                        self.stats['missing'] += 1
                    
                    return 'missing', nzb_path.name, total_segments, missing
                    
            except Exception as e:
                self.logger.error(f"Kontrol hatası ({nzb_path.name}): {e}")
                self.move_nzb_file(str(nzb_path), self.folders['error'])
                
                with self.stats_lock:
                    self.stats['error'] += 1
                
                return 'error', nzb_path.name, 0, 0
            finally:
                server.quit()
                
        except Exception as e:
            self.logger.error(f"İşlem hatası ({nzb_path.name}): {e}")
            
            with self.stats_lock:
                self.stats['error'] += 1
            
            return 'error', nzb_path.name, 0, 0
    
    def process_nzb_files(self):
        """Tüm NZB dosyalarını paralel olarak işler"""
        nzb_folder = Path(self.config['paths']['nzb_folder'])
        
        if not nzb_folder.exists():
            self.logger.error(f"NZB klasörü bulunamadı: {nzb_folder}")
            return
        
        nzb_files = list(nzb_folder.glob('*.nzb'))
        
        if not nzb_files:
            self.logger.warning(f"NZB dosyası bulunamadı: {nzb_folder}")
            return
        
        total_files = len(nzb_files)
        self.logger.info(f"Toplam {total_files} NZB dosyası bulundu")
        self.logger.info(f"Paralel işlem sayısı: {self.max_workers}")
        self.stats['total'] = total_files
        
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_nzb = {
                    executor.submit(self.process_single_nzb, nzb_path, idx, total_files): nzb_path
                    for idx, nzb_path in enumerate(nzb_files, 1)
                }
                
                completed = 0
                for future in as_completed(future_to_nzb):
                    completed += 1
                    nzb_path = future_to_nzb[future]
                    try:
                        status, filename, total_seg, missing_seg = future.result()
                    except Exception as e:
                        self.logger.error(f"Thread hatası ({nzb_path.name}): {e}")
                    
                    if completed % 10 == 0:
                        self.logger.info(f"İlerleme: {completed}/{total_files} dosya işlendi")
            
        except Exception as e:
            self.logger.error(f"İşlem hatası: {e}")
            return
    
    def print_summary(self):
        """İşlem özetini yazdırır"""
        print("\n" + "="*60)
        print("İŞLEM ÖZETİ")
        print("="*60)
        print(f"Toplam NZB       : {self.stats['total']}")
        print(f"Tam Dosyalar     : {self.stats['complete']}")
        print(f"Eksik Dosyalar   : {self.stats['missing']}")
        print(f"Hatalı Dosyalar  : {self.stats['error']}")
        print("="*60)
        print(f"\nKlasörler:")
        print(f"  Tam      : {self.folders['complete']}")
        print(f"  Eksik    : {self.folders['missing']}")
        print(f"  Hata     : {self.folders['error']}")
        print("="*60 + "\n")


def main():
    """Ana fonksiyon"""
    print("="*60)
    print("NZB Checker - NZB Dosya Kontrolü ve Sınıflandırma")
    print("="*60 + "\n")
    
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
