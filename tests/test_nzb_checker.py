#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NZB Checker duman testi: sahte NNTP sunucusuna karsi uctan uca kontrol.

    python3 test_nzb_checker.py
"""

import json
import logging
import os
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from nzb_checker.engine import NZBChecker  # noqa: E402
from nzb_checker.engine.cache import ResultCache  # noqa: E402

EXIST = {f'ok-complete-{i}@test' for i in range(20)} | {f'ok-upper-{i}@test' for i in range(6)}
EXIST |= {'ok-m1@test', 'ok-m2@test', 'ok-m4@test', 'drop-1@test', 'ok-d2@test'}
BIG = {f'ok-big-{i}@test' for i in range(300) if i % 6 != 0}  # 50 tanesi yok
EXIST |= BIG
# yukseltme testi: 100 segment, 4'u yok; yalnizca index 0 ornege denk gelir
ESC_IDS = [f'ok-esc-{i}@test' for i in range(100)]
EXIST |= {mid for i, mid in enumerate(ESC_IDS) if i not in (0, 30, 60, 90)}

# port -> sunucudaki makale kumesi (coklu provider testi farkli portlar kullanir)
EXIST_BY_PORT = {}

SEEN = {'stat': 0, 'conns': 0, 'max_conns': 0}
SEEN_LOCK = threading.Lock()


class Handler(socketserver.StreamRequestHandler):
    def send(self, text):
        self.wfile.write(text.encode() + b'\r\n')

    def handle(self):
        with SEEN_LOCK:
            SEEN['conns'] += 1
            SEEN['max_conns'] = max(SEEN['max_conns'], SEEN['conns'])
        try:
            self.send('200 fake ready')
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                cmd = line.decode(errors='ignore').strip()
                upper = cmd.upper()
                if upper.startswith('AUTHINFO USER'):
                    self.send('381 need password')
                elif upper.startswith('AUTHINFO PASS'):
                    self.send('281 ok')
                elif upper.startswith('STAT'):
                    with SEEN_LOCK:
                        SEEN['stat'] += 1
                    mid = cmd.split(None, 1)[1].strip('<>')
                    if mid.startswith('drop'):
                        return  # baglantiyi kes
                    known = EXIST_BY_PORT.get(self.server.server_address[1], EXIST)
                    self.send('223 0 <%s>' % mid if mid in known else '430 no such article')
                elif upper == 'QUIT':
                    self.send('205 bye')
                    return
                else:
                    self.send('500 unknown')
        finally:
            with SEEN_LOCK:
                SEEN['conns'] -= 1


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def nzb(ids):
    segments = '\n'.join(
        f'      <segment bytes="1000" number="{i}">{mid}</segment>'
        for i, mid in enumerate(ids, 1))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n'
            '  <file poster="t" date="1700000000" subject="test">\n'
            '    <segments>\n' + segments + '\n    </segments>\n  </file>\n</nzb>\n')


def write_config(tmp, nzb_dir, port, providers=None, **settings):
    if providers is None:
        providers = [{'name': 'P1', 'host': '127.0.0.1', 'port': port, 'username': 'u',
                      'password': 'p', 'use_ssl': False, 'connections': 8}]
    config = {
        'providers': providers,
        'paths': {'nzb_folder': str(nzb_dir), 'complete_folder': 'complete',
                  'missing_folder': 'missing', 'error_folder': 'error'},
        'settings': {'check_all_articles': False, 'sample_check_count': 20,
                     'sample_ratio': 0.02, 'timeout': 10, 'log_level': 'INFO',
                     'cache': False},
    }
    config['settings'].update(settings)
    # saglayici sirasi gecmise bagli: testler arasi sizmasi engelle
    history = Path(os.environ['XDG_CACHE_HOME']) / 'nzb_checker' / 'last_scan.json'
    history.unlink(missing_ok=True)
    path = Path(tmp) / f'config_{len(list(Path(tmp).glob("config_*")))}.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    return path


def placed_in(nzb_dir):
    return {d: sorted(os.listdir(nzb_dir / d)) for d in ('complete', 'missing', 'error')}


def test_siniflandirma(tmp, port, capture):
    """Ornekleme modu: tam / eksik / bozuk / baglanti kopmasi / .NZB uzantisi"""
    nzb_dir = Path(tmp) / 'nzb'
    nzb_dir.mkdir()
    (nzb_dir / 'complete.nzb').write_text(nzb([f'ok-complete-{i}@test' for i in range(20)]), encoding='utf-8')
    (nzb_dir / 'UPPER.NZB').write_text(nzb([f'ok-upper-{i}@test' for i in range(6)]), encoding='utf-8')
    (nzb_dir / 'missing.nzb').write_text(nzb(['ok-m1@test', 'ok-m2@test', 'gone@test', 'ok-m4@test']), encoding='utf-8')
    (nzb_dir / 'drops.nzb').write_text(nzb(['drop-1@test', 'ok-d2@test']), encoding='utf-8')
    (nzb_dir / 'broken.nzb').write_text('<nzb><file><segments>', encoding='utf-8')

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port)))
    checker.process_nzb_files()

    placed = placed_in(nzb_dir)
    assert placed['complete'] == ['UPPER.NZB', 'complete.nzb'], placed['complete']
    assert placed['missing'] == ['missing.nzb'], placed['missing']
    assert placed['error'] == ['broken.nzb', 'drops.nzb'], placed['error']
    assert checker.stats == {'total': 5, 'complete': 2, 'missing': 1, 'error': 2}, checker.stats
    assert not any(p.suffix.lower() == '.nzb' for p in nzb_dir.iterdir()), 'isinlanmayan dosya kaldi'


def test_paralel_tam_tarama(tmp, port, capture):
    """check_all_articles=true: tek buyuk dosyanin segmentleri baglantilara bolunur"""
    nzb_dir = Path(tmp) / 'big'
    nzb_dir.mkdir()
    ids = sorted(f'ok-big-{i}@test' for i in range(300))  # 50 tanesi sunucuda yok
    (nzb_dir / 'big.nzb').write_text(nzb(ids), encoding='utf-8')

    del capture.messages[:]
    SEEN['stat'] = 0
    SEEN['max_conns'] = 0

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, check_all_articles=True)))
    checker.process_nzb_files()

    placed = placed_in(nzb_dir)
    assert placed['missing'] == ['big.nzb'], placed
    assert checker.stats == {'total': 1, 'complete': 0, 'missing': 1, 'error': 0}, checker.stats
    assert SEEN['stat'] == 300, f'beklenen 300 STAT, gelen {SEEN["stat"]}'
    assert SEEN['max_conns'] >= 2, f'tek dosya tek baglantida tarandi (max {SEEN["max_conns"]})'
    assert any('50/300' in m for m in capture.messages), capture.messages[-5:]


def test_ornek_rozeti(tmp, port, capture):
    """Ornekte eksik yoksa tam tarama yapilmaz, satir 'ornek' olarak isaretlenir"""
    nzb_dir = Path(tmp) / 'sample'
    nzb_dir.mkdir()
    ids = [f'ok-esc-{i}@test' for i in range(100) if i not in (0, 30, 60, 90)]
    (nzb_dir / 'sample.nzb').write_text(nzb(ids), encoding='utf-8')

    SEEN['stat'] = 0
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port)))
    checker.process_nzb_files()

    assert placed_in(nzb_dir)['complete'] == ['sample.nzb'], placed_in(nzb_dir)
    assert checker.results[0]['checked_all'] is False, checker.results[0]
    assert SEEN['stat'] == 20, f'ornek 20 STAT olmali, gelen {SEEN["stat"]}'


def test_alt_klasor(tmp, port, capture):
    """Alt klasorler taranir; complete/missing/error klasorleri tekrar taranmaz"""
    nzb_dir = Path(tmp) / 'nested'
    (nzb_dir / 'alt' / 'derin').mkdir(parents=True)
    (nzb_dir / 'alt' / 'derin' / 'ic.nzb').write_text(nzb(['ok-m1@test']), encoding='utf-8')

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port)))
    checker.process_nzb_files()

    assert checker.results[0]['name'] == 'alt/derin/ic.nzb', checker.results
    assert placed_in(nzb_dir)['complete'] == ['ic.nzb'], placed_in(nzb_dir)

    # ikinci tarama: tasinmis dosya cikti klasorunden geri alinmamali
    again = NZBChecker(str(write_config(tmp, nzb_dir, port)))
    again.process_nzb_files()
    assert again.results == [] and again.stats['total'] == 0, again.results


def test_saglayici_sirasi(tmp, port2, port, capture):
    """Gecmis isabet oranina gore yuksek olan saglayici basa alinir"""
    nzb_dir = Path(tmp) / 'order'
    nzb_dir.mkdir()
    providers = [
        {'name': 'P2', 'host': '127.0.0.1', 'port': port2, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 2},
        {'name': 'P1', 'host': '127.0.0.1', 'port': port, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 2},
    ]
    config = write_config(tmp, nzb_dir, port, providers=providers, cache=False)
    history = Path(os.environ['XDG_CACHE_HOME']) / 'nzb_checker' / 'last_scan.json'
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(json.dumps({'providers': {
        'P1': {'stat': 100, 'missing': 0},
        'P2': {'stat': 100, 'missing': 40},
    }}), encoding='utf-8')

    checker = NZBChecker(str(config))
    assert [p['name'] for p in checker.providers] == ['P1', 'P2'], checker.providers
    history.unlink()


def test_onbellek_ttl(tmp, port, capture):
    """Var sonucu kisa, yok sonucu uzun TTL ile saklanir"""
    cache = ResultCache(Path(tmp) / 'ttl.db', ttl_days=14)
    key = 'test-key'
    cache.put_many(key, {'var@test': True, 'yok@test': False})
    assert cache.get_many(key, ['var@test', 'yok@test']) == {
        'var@test': True, 'yok@test': False}

    # "var" kaydinin tazelik penceresini gecmis say, "yok" hala taze kalmali
    cache.present_ttl_seconds = -1
    assert cache.get_many(key, ['var@test', 'yok@test']) == {'yok@test': False}
    cache.close()


def test_tam_dosya(tmp, port, capture):
    """check_all_articles=true + hepsi mevcut -> complete/"""
    nzb_dir = Path(tmp) / 'full'
    nzb_dir.mkdir()
    (nzb_dir / 'full.nzb').write_text(nzb([f'ok-complete-{i}@test' for i in range(20)]), encoding='utf-8')

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, check_all_articles=True)))
    checker.process_nzb_files()

    assert placed_in(nzb_dir)['complete'] == ['full.nzb'], placed_in(nzb_dir)
    assert checker.stats == {'total': 1, 'complete': 1, 'missing': 0, 'error': 0}, checker.stats


def test_olaylar(tmp, port, capture):
    """events API: GUI'nin gordugu olay dizisi ve alanlari"""
    nzb_dir = Path(tmp) / 'events'
    nzb_dir.mkdir()
    (nzb_dir / 'e1.nzb').write_text(nzb([f'ok-complete-{i}@test' for i in range(20)]), encoding='utf-8')

    olaylar = []
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, check_all_articles=True)),
                         events=lambda kind, **data: olaylar.append((kind, data)))
    checker.process_nzb_files()

    turler = [k for k, _ in olaylar]
    assert turler[0] == 'scan_start' and turler[-1] == 'scan_end', turler
    assert turler.count('file_start') == 1 and turler.count('file_done') == 1, turler
    assert turler.count('progress') == 1, turler
    assert 'file_progress' in turler, turler
    progress = [d for k, d in olaylar if k == 'file_progress'][-1]
    assert progress['checked'] == 20 and progress['to_check'] == 20, progress
    done = dict(olaylar)['file_done']
    assert done['status'] == 'complete' and done['missing'] == 0 and done['checked'] == 20, done
    assert done['moved'] is True, done
    assert dict(olaylar)['scan_end']['complete'] == 1, olaylar[-1]
    assert not dict(olaylar)['scan_end']['cancelled'], olaylar[-1]
    # hatalı dosya sayacı 'error'; hata mesajı yoksa alan da yok (GUI sahte hata açmasın)
    assert 'error_message' not in dict(olaylar)['scan_end'], olaylar[-1]


def test_erisilemeyen_saglayici(tmp, capture):
    """Hiçbir sağlayıcıya bağlanılamazsa scan_end hata mesajı taşır ama sayacı bozmaz"""
    nzb_dir = Path(tmp) / 'dead'
    nzb_dir.mkdir()
    (nzb_dir / 'x.nzb').write_text(nzb(['ok-m1@test']), encoding='utf-8')

    dead = socket.socket()
    dead.bind(('127.0.0.1', 0))
    dead_port = dead.getsockname()[1]
    dead.close()
    providers = [{'name': 'Olu', 'host': '127.0.0.1', 'port': dead_port, 'username': 'u',
                  'password': 'p', 'use_ssl': False, 'connections': 2}]

    olaylar = []
    checker = NZBChecker(str(write_config(tmp, nzb_dir, dead_port, providers=providers)),
                         events=lambda kind, **data: olaylar.append((kind, data)))
    checker.process_nzb_files()

    son = dict(olaylar)['scan_end']
    assert son['error_message'] and son['error'] == 0, son
    assert placed_in(nzb_dir)['error'] == [], placed_in(nzb_dir)


def test_iptal(tmp, port, capture):
    """Dinleyici ilk dosyada cancel() cagirir: dosyalar yerinde kalir, cancelled olayi gelir"""
    nzb_dir = Path(tmp) / 'cancel'
    nzb_dir.mkdir()
    for i in range(5):
        (nzb_dir / f'c{i}.nzb').write_text(nzb([f'ok-complete-{i}@test'] * 20), encoding='utf-8')

    olaylar = []
    checker = None

    def dinle(kind, **data):
        olaylar.append((kind, data))
        if kind == 'file_start':
            checker.cancel()

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, check_all_articles=True)), events=dinle)
    checker.process_nzb_files()

    assert any(k == 'file_done' and d['status'] == 'cancelled' for k, d in olaylar), olaylar
    assert any(k == 'scan_end' and d['cancelled'] for k, d in olaylar), olaylar[-1]
    kalan = [p.name for p in nzb_dir.iterdir() if p.suffix.lower() == '.nzb']
    assert len(kalan) == 5, f'iptal edilen dosyalar tasinmamali: {kalan}'


def test_tasima_cakismasi(tmp, port, capture):
    """Hedef klasorde ayni ad varsa dosya numaralandirilir, uzerine yazilmaz"""
    nzb_dir = Path(tmp) / 'collision'
    nzb_dir.mkdir()
    (nzb_dir / 'complete').mkdir()
    (nzb_dir / 'complete' / 'same.nzb').write_text('eski', encoding='utf-8')
    (nzb_dir / 'same.nzb').write_text(
        nzb([f'ok-complete-{i}@test' for i in range(20)]), encoding='utf-8')

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, check_all_articles=True)))
    checker.process_nzb_files()

    assert sorted(os.listdir(nzb_dir / 'complete')) == ['same (1).nzb', 'same.nzb'], \
        os.listdir(nzb_dir / 'complete')
    assert (nzb_dir / 'complete' / 'same.nzb').read_text(encoding='utf-8') == 'eski'
    assert checker.move_failed == 0


def test_saglayici_hatasi(tmp, port, capture):
    """Bir saglayici tamamen erisilemezse digerinin sonucu kullanilir (dosya hatali olmaz)"""
    nzb_dir = Path(tmp) / 'fallback'
    nzb_dir.mkdir()
    (nzb_dir / 'fallback.nzb').write_text(
        nzb([f'ok-complete-{i}@test' for i in range(10)]), encoding='utf-8')

    import socket
    dead = socket.socket()
    dead.bind(('127.0.0.1', 0))
    dead_port = dead.getsockname()[1]
    dead.close()

    providers = [
        {'name': 'Olu', 'host': '127.0.0.1', 'port': dead_port, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 2},
        {'name': 'P1', 'host': '127.0.0.1', 'port': port, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 4},
    ]
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, providers=providers,
                                          check_all_articles=True)))
    checker.process_nzb_files()

    placed = placed_in(nzb_dir)
    assert placed['complete'] == ['fallback.nzb'], placed
    assert checker.stats == {'total': 1, 'complete': 1, 'missing': 0, 'error': 0}, checker.stats
    # sağlayıcılar geçmiş isabet oranına göre sıralanabilir: ada göre karşılaştır
    assert dict(zip((p['name'] for p in checker.providers),
                    checker._provider_ok)) == {'Olu': False, 'P1': True}, \
        list(zip((p['name'] for p in checker.providers), checker._provider_ok))


def test_kademeli_provider(tmp, port2, port, capture):
    """P2'ye yalnizca P1'in bulamadigi segmentler sorulur (STAT sayisi duser)"""
    nzb_dir = Path(tmp) / 'staged'
    nzb_dir.mkdir()
    ids = [f'stage-a{i}@test' for i in range(10)]
    (nzb_dir / 'staged.nzb').write_text(nzb(ids), encoding='utf-8')

    EXIST_BY_PORT[port] = set(ids) - {'stage-a3@test', 'stage-a7@test'}
    EXIST_BY_PORT[port2] = set(ids) - {'stage-a7@test'}
    providers = [
        {'name': 'P1', 'host': '127.0.0.1', 'port': port, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 4},
        {'name': 'P2', 'host': '127.0.0.1', 'port': port2, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 4},
    ]

    SEEN['stat'] = 0
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, providers=providers,
                                          check_all_articles=True)))
    checker.process_nzb_files()

    placed = placed_in(nzb_dir)
    assert placed['missing'] == ['staged.nzb'], placed
    # P1: 10 STAT, P2: yalnizca P1'in eksik dedigi 2 segment -> toplam 12 (eski davranis 20)
    assert SEEN['stat'] == 12, f"beklenen 12 STAT, gelen {SEEN['stat']}"

    EXIST_BY_PORT.pop(port, None)
    EXIST_BY_PORT.pop(port2, None)


def test_onbellek(tmp, port, capture):
    """Ayni NZB ikinci kez tarandiginda STAT gonderilmez (onbellek)"""
    nzb_dir = Path(tmp) / 'cache'
    nzb_dir.mkdir()
    cache_path = Path(tmp) / 'results.db'
    content = nzb([f'ok-complete-{i}@test' for i in range(20)])
    settings = {'check_all_articles': True, 'cache': True, 'cache_path': str(cache_path)}

    (nzb_dir / 'cached.nzb').write_text(content, encoding='utf-8')
    SEEN['stat'] = 0
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, **settings)))
    checker.process_nzb_files()
    assert checker.stats == {'total': 1, 'complete': 1, 'missing': 0, 'error': 0}, checker.stats
    assert SEEN['stat'] == 20, SEEN['stat']

    (nzb_dir / 'cached.nzb').write_text(content, encoding='utf-8')
    SEEN['stat'] = 0
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, **settings)))
    checker.process_nzb_files()
    assert checker.stats == {'total': 1, 'complete': 1, 'missing': 0, 'error': 0}, checker.stats
    assert SEEN['stat'] == 0, f'onbellekten okunmaliydi, {SEEN["stat"]} STAT gonderildi'



def test_apprun_paketi(tmp, capture):
    """AppRun, paket kokundeki modulu bulur (PYTHONPATH olmadan calismazdi)"""
    import shutil
    import subprocess

    root = Path(__file__).resolve().parent.parent
    appdir = Path(tmp) / 'AppDir'
    (appdir / 'usr' / 'src').mkdir(parents=True)
    shutil.copytree(root / 'nzb_checker', appdir / 'usr' / 'src' / 'nzb_checker',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copy(root / 'packaging' / 'AppRun', appdir / 'AppRun')
    (appdir / 'AppRun').chmod(0o755)

    # notr calisma dizini: yerel nzb_checker paketi yanlislikla bulunmasin
    result = subprocess.run([str(appdir / 'AppRun'), '--version'],
                            cwd=tmp, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert 'nzb-checker' in result.stdout, result.stdout
    print('  AppRun paketi calisiyor')


def test_guncelleme(tmp, capture):
    """Release kontrolu: surum karsilastirma, asset secimi, HTTP hatasi"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from nzb_checker import updater

    class Handler(BaseHTTPRequestHandler):
        release = {}

        def do_GET(self):
            payload = json.dumps(self.release).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    Handler.release = {
        'tag_name': 'v9.9.9',
        'name': 'Test surumu',
        'body': 'notlar',
        'html_url': 'https://example.com/release',
        'published_at': '2026-01-02T00:00:00Z',
        'assets': [
            {'name': 'NZB-Checker-x86_64.AppImage',
             'browser_download_url': 'https://example.com/a.AppImage', 'size': 1048576},
            {'name': 'NZB-Checker-bundled-x86_64.AppImage',
             'browser_download_url': 'https://example.com/b.AppImage', 'size': 10485760},
        ],
    }
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_address[1]}/latest'
    try:
        assert updater.is_newer('1.3.0', 'v1.4.0')
        assert not updater.is_newer('1.4.0', 'v1.4.0')
        assert updater.is_newer('1.4.0-beta1', '1.4.0')
        assert not updater.is_newer('1.4.0', '1.4.0-beta1')

        info = updater.check_for_update('1.4.0', api_url=url, timeout=5)
        assert info and info['version'] == '9.9.9', info
        assert info['current'] == '1.4.0', info
        assert updater.pick_asset(info['assets'])['name'] == 'NZB-Checker-x86_64.AppImage'
        bundled = updater.pick_asset(info['assets'], 'NZB-Checker-bundled-x86_64.AppImage')
        assert bundled['name'] == 'NZB-Checker-bundled-x86_64.AppImage', bundled

        Handler.release = dict(Handler.release, tag_name='v1.4.0')
        assert updater.check_for_update('1.4.0', api_url=url, timeout=5) is None

        dead = socket.socket()
        dead.bind(('127.0.0.1', 0))
        dead_port = dead.getsockname()[1]
        dead.close()
        try:
            updater.fetch_latest_release(f'http://127.0.0.1:{dead_port}/latest', timeout=3)
        except updater.UpdateError:
            pass
        else:
            raise AssertionError('baglanti hatasi UpdateError uretmeliydi')
    finally:
        server.shutdown()



def test_config_hatasi(tmp, capture):
    """Eksik/bozuk config veya saglayicisiz ayar ConfigError firlatir (sys.exit yok)"""
    from nzb_checker.engine import ConfigError

    def expect_config_error(config_path):
        try:
            NZBChecker(str(config_path))
        except ConfigError:
            return
        raise AssertionError(f'ConfigError bekleniyordu: {config_path}')

    expect_config_error(Path(tmp) / 'yok.json')
    bad = Path(tmp) / 'bozuk.json'
    bad.write_text('{', encoding='utf-8')
    expect_config_error(bad)

    empty = Path(tmp) / 'saglayicisiz.json'
    empty.write_text(json.dumps({
        'providers': [],
        'paths': {'nzb_folder': str(Path(tmp) / 'bos'), 'complete_folder': 'complete',
                  'missing_folder': 'missing', 'error_folder': 'error'},
    }), encoding='utf-8')
    expect_config_error(empty)


def test_rapor(tmp, port, capture):
    """export_report: CSV ve eksik-segment listeleri yazilir"""
    nzb_dir = Path(tmp) / 'report'
    nzb_dir.mkdir()
    (nzb_dir / 'tam.nzb').write_text(
        nzb([f'ok-complete-{i}@test' for i in range(20)]), encoding='utf-8')
    (nzb_dir / 'eksik.nzb').write_text(
        nzb(['ok-m1@test', 'gone-x@test', 'ok-m2@test']), encoding='utf-8')

    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, check_all_articles=True)))
    checker.process_nzb_files()

    out = Path(tmp) / 'rapor-cikti'
    info = checker.export_report(out)
    assert info['files'] == 2 and info['missing_files'] == 1, info
    assert info['missing_ids'] == 1, info

    csv_text = (out / 'rapor.csv').read_text(encoding='utf-8-sig').replace('\r', '')
    assert 'eksik.nzb,missing,3,3,1' in csv_text, csv_text
    assert 'tam.nzb,complete,20,20,0' in csv_text, csv_text

    ids = (out / 'eksik-segmentler' / 'eksik.nzb.txt').read_text(
        encoding='utf-8').split()
    assert ids == ['gone-x@test'], ids


def test_coklu_provider(tmp, port2, port, capture):
    """Iki provider: segment ancak HER IKISINDE yoksa eksik sayilir"""
    nzb_dir = Path(tmp) / 'multi'
    nzb_dir.mkdir()
    ids = [f'multi-a{i}@test' for i in range(4)] + ['multi-gone@test']
    (nzb_dir / 'multi.nzb').write_text(nzb(ids), encoding='utf-8')

    # ikinci saglayici yalnizca 'multi-a0' makalesini bilmiyor
    EXIST_BY_PORT[port2] = set(EXIST) | {f'multi-a{i}@test' for i in range(1, 4)}
    providers = [
        {'name': 'P1', 'host': '127.0.0.1', 'port': port, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 4},
        {'name': 'P2', 'host': '127.0.0.1', 'port': port2, 'username': 'u',
         'password': 'p', 'use_ssl': False, 'connections': 4},
    ]

    del capture.messages[:]
    checker = NZBChecker(str(write_config(tmp, nzb_dir, port, providers=providers,
                                          check_all_articles=True)))
    checker.process_nzb_files()

    placed = placed_in(nzb_dir)
    assert placed['missing'] == ['multi.nzb'], placed
    assert [p['name'] for p in checker.providers] == ['P1', 'P2'], checker.providers
    assert checker.max_workers == 8, checker.max_workers
    # P1'e 5, P2'ye 4 makale soruldu (P2 sadece 4'unu tanir, biri eksik)
    assert len(EXIST_BY_PORT[port2]) > 0
    assert any('P1' in m and 'P2' in m for m in capture.messages), capture.messages[-5:]

def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    server2 = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    port2 = server2.server_address[1]

    capture = Capture()
    logging.getLogger().addHandler(capture)

    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        # log/onbellek/gecmis gercek ~/.cache'e yazilmasin
        os.environ['XDG_CACHE_HOME'] = str(Path(tmp) / 'xdg-cache')
        test_siniflandirma(tmp, port, capture)
        test_paralel_tam_tarama(tmp, port, capture)
        test_ornek_rozeti(tmp, port, capture)
        test_tam_dosya(tmp, port, capture)
        test_alt_klasor(tmp, port, capture)
        test_onbellek_ttl(tmp, port, capture)
        test_olaylar(tmp, port, capture)
        test_erisilemeyen_saglayici(tmp, capture)
        test_iptal(tmp, port, capture)
        test_tasima_cakismasi(tmp, port, capture)
        test_saglayici_hatasi(tmp, port, capture)
        test_onbellek(tmp, port, capture)
        test_coklu_provider(tmp, port2, port, capture)
        test_saglayici_sirasi(tmp, port2, port, capture)
        test_kademeli_provider(tmp, port2, port, capture)
        test_apprun_paketi(tmp, capture)
        test_guncelleme(tmp, capture)
        test_config_hatasi(tmp, capture)
        test_rapor(tmp, port, capture)

    server.shutdown()
    server2.shutdown()
    print('OK - tum senaryolar gecti (max eszamanli baglanti: %d)' % SEEN['max_conns'])


if __name__ == '__main__':
    main()
