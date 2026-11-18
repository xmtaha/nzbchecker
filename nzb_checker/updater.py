#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub release kontrolü.

Yalnızca bilgilendirme amaçlıdır: güncellemeyi indirmez, dosya değiştirmez.
Yeni sürüm bilgisi GUI'de gösterilir; AppImage'i kullanıcı tarayıcısından
indirip kendisi değiştirir.
"""

import json
import re
import urllib.error
import urllib.request

from . import __version__

API_URL = 'https://api.github.com/repos/xmtaha/nzbchecker/releases/latest'
RELEASES_URL = 'https://github.com/xmtaha/nzbchecker/releases/latest'
TIMEOUT = 6.0

_NUMBER = re.compile(r'\d+')


class UpdateError(Exception):
    """Release bilgisi alınamadı (ağ, HTTP hatası, bozuk yanıt)"""


def version_key(text: str):
    """
    Sürüm metnini karşılaştırılabilir anahtara çevirir.

    'v1.3.0' -> ((1, 3, 0), 1, '')   (yayın sürümü)
    '1.3.0-beta1' -> ((1, 3, 0), 0, 'beta1')  (ön sürüm daha eski sayılır)
    """
    value = str(text or '').strip().lower()
    if value.startswith('v'):
        value = value[1:]
    main, _, pre = value.partition('-')
    numbers = [int(match.group()) for match in _NUMBER.finditer(main)][:3]
    numbers += [0] * (3 - len(numbers))
    return (tuple(numbers), 0 if pre else 1, pre)


def is_newer(current: str, latest: str) -> bool:
    """latest, current'dan yeni mi?"""
    return version_key(latest) > version_key(current)


def pick_asset(assets, running_name: str = ''):
    """
    İndirilecek AppImage'i seçer.

    Çalışan dosya adı verilmişse aynı adı, yoksa 'bundled' durumuna göre
    en yakın AppImage'i tercih eder.
    """
    appimages = [a for a in assets or []
                 if str(a.get('name') or '').lower().endswith('.appimage')]
    if not appimages:
        return None

    if running_name:
        for asset in appimages:
            if asset.get('name') == running_name:
                return asset
        wants_bundled = 'bundled' in running_name.lower()
        for asset in appimages:
            has_bundled = 'bundled' in str(asset.get('name') or '').lower()
            if has_bundled == wants_bundled:
                return asset
    for asset in appimages:
        if 'bundled' not in str(asset.get('name') or '').lower():
            return asset
    return appimages[0]


def normalize_release(data: dict) -> dict:
    """GitHub API yanıtını uygulama içi sözlüğe çevirir"""
    tag = str(data.get('tag_name') or '').strip()
    if not tag:
        raise UpdateError('Release etiketi (tag_name) yok')

    assets = []
    for asset in data.get('assets') or []:
        assets.append({
            'name': str(asset.get('name') or ''),
            'url': str(asset.get('browser_download_url') or ''),
            'size': int(asset.get('size') or 0),
        })

    return {
        'tag': tag,
        'version': tag[1:] if tag[:1] in ('v', 'V') else tag,
        'name': str(data.get('name') or tag),
        'body': str(data.get('body') or ''),
        'html_url': str(data.get('html_url') or RELEASES_URL),
        'published_at': str(data.get('published_at') or ''),
        'assets': assets,
    }


def fetch_latest_release(api_url: str = API_URL, timeout: float = TIMEOUT) -> dict:
    """
    En son release bilgisini indirir.

    Raises:
        UpdateError: ağ/HTTP/JSON hatası veya beklenmeyen yanıt
    """
    request = urllib.request.Request(api_url, headers={
        'User-Agent': f'nzb-checker/{__version__}',
        'Accept': 'application/vnd.github+json',
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(str(exc)) from exc
    if not isinstance(data, dict):
        raise UpdateError('Beklenmeyen release yanıtı')
    return normalize_release(data)


def check_for_update(current: str = __version__, api_url: str = API_URL,
                     timeout: float = TIMEOUT):
    """
    Yeni sürüm varsa release bilgisini, yoksa None döndürür.

    Raises:
        UpdateError: kontrol yapılamadıysa
    """
    release = fetch_latest_release(api_url=api_url, timeout=timeout)
    if not is_newer(current, release['version']):
        return None
    release['current'] = current
    return release
