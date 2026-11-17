#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NZB Checker duman testi: sahte NNTP sunucusuna karsi uctan uca kontrol.

    python3 test_nzb_checker.py
"""

import json
import logging
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nzb_checker import NZBChecker  # noqa: E402

EXIST = {f'ok-complete-{i}@test' for i in range(20)} | {f'ok-upper-{i}@test' for i in range(6)}
EXIST |= {'ok-m1@test', 'ok-m2@test', 'ok-m4@test', 'drop-1@test', 'ok-d2@test'}
BIG = {f'ok-big-{i}@test' for i in range(300) if i % 6 != 0}  # 50 tanesi yok
EXIST |= BIG

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
                    self.send('223 0 <%s>' % mid if mid in EXIST else '430 no such article')
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


def write_config(tmp, nzb_dir, port, **settings):
    config = {
        'usenet_provider': {'host': '127.0.0.1', 'port': port, 'username': 'u',
                            'password': 'p', 'use_ssl': False, 'connections': 8},
        'paths': {'nzb_folder': str(nzb_dir), 'complete_folder': 'complete',
                  'missing_folder': 'missing', 'error_folder': 'error'},
        'settings': {'check_all_articles': False, 'sample_check_count': 20,
                     'sample_ratio': 0.02, 'timeout': 10, 'log_level': 'INFO'},
    }
    config['settings'].update(settings)
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
    done = dict(olaylar)['file_done']
    assert done['status'] == 'complete' and done['missing'] == 0 and done['checked'] == 20, done
    assert dict(olaylar)['scan_end']['complete'] == 1, olaylar[-1]
    assert not dict(olaylar)['scan_end']['cancelled'], olaylar[-1]


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


def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    capture = Capture()
    logging.getLogger().addHandler(capture)

    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)  # log dosyasi cwd'ye yazilir
        test_siniflandirma(tmp, port, capture)
        test_paralel_tam_tarama(tmp, port, capture)
        test_tam_dosya(tmp, port, capture)
        test_olaylar(tmp, port, capture)
        test_iptal(tmp, port, capture)

    server.shutdown()
    print('OK - tum senaryolar gecti (max eszamanli baglanti: %d)' % SEEN['max_conns'])


if __name__ == '__main__':
    main()
