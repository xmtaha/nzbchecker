#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Basit NNTP Client - Python 3.13+ (nntplib kaldirildi)

SSL baglantilari saglayicilar tarafindan bosta birakilinca sessizce dusurulur.
Bu yuzden stat() gecici bir baglanti hatasinda (soket koptu, EOF, 400-403 /
480-482 / 503 yaniti) bir kez yeniden baglanip komutu tekrarlar; isteyen
cagiran taraf bunu gormez.
"""

import socket
import ssl
from typing import Optional, Tuple

# Yeniden baglanip tekrar denemeye deger yanit kodlari:
# 400-403 sunucu kapaniyor / gecici red, 480-482 kimlik veya zaman asimi,
# 503 hizmet disi (genelde kapanma veya bakim)
TRANSIENT_CODES = (b'400', b'401', b'402', b'403', b'480', b'481', b'482', b'503')

# TCP keepalive (saniye): bosta kalan NAT/firewall baglantisi sessizce olmesin
KEEPIDLE = 60
KEEPINTVL = 15
KEEPCNT = 4


class NNTPError(Exception):
    """NNTP protokol hatasi"""


class NNTPConnectionError(NNTPError):
    """Baglanti/soket hatasi - article yoklugu ile karistirilmamali"""


class ArticleNotFound(NNTPError):
    """Article sunucuda yok (430)"""


class _Transient(NNTPConnectionError):
    """Gecici baglanti hatasi: stat() bir kez yeniden baglanip tekrar dener"""


def _text(line: bytes) -> str:
    """Sunucu yanitini okunabilir metne cevir"""
    return line.decode('utf-8', errors='ignore')


def _enable_keepalive(sock):
    """SO_KEEPALIVE + platform destekliyorsa TCP_KEEPIDLE/INTVL/CNT"""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        return
    for name, value in (('TCP_KEEPIDLE', KEEPIDLE),
                        ('TCP_KEEPINTVL', KEEPINTVL),
                        ('TCP_KEEPCNT', KEEPCNT)):
        option = getattr(socket, name, None)  # Windows/macOS'ta adlar farkli
        if option is None:
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            pass


class NNTPClient:
    """Basit NNTP client implementasyonu"""

    def __init__(self, host: str, port: int = 119, use_ssl: bool = False,
                 user: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 30, ssl_context: Optional[ssl.SSLContext] = None):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.user = user
        self.password = password
        self.timeout = timeout
        self.ssl_context = ssl_context
        self.socket = None
        self.file = None

        self._connect()

    def _connect(self):
        """Baglanti kur: once TCP (timeout ile), sonra TLS sarmalama"""
        try:
            sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise NNTPConnectionError(
                f"Baglanti kurulamadi ({self.host}:{self.port}): {e}") from e

        sock.settimeout(self.timeout)
        try:
            if self.use_ssl:
                context = self.ssl_context or ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=self.host)
                sock.settimeout(self.timeout)  # sarmalama sonrasi tekrar
            _enable_keepalive(sock)

            self.socket = sock
            self.file = sock.makefile('rb')

            greeting = self._getline()
            if not greeting.startswith((b'200', b'201')):
                raise NNTPConnectionError(
                    f"Baglanti reddedildi ({self.host}:{self.port}): {_text(greeting)}")

            if self.user and self.password:
                self._authenticate()
        except Exception:
            self._close_transport()
            raise

    def _authenticate(self):
        """Kimlik dogrulamasi: 381 -> PASS, sonuc 281 olmali (gevsek kabul yok)"""
        line = self._command(f'AUTHINFO USER {self.user}')
        if line.startswith(b'381'):
            line = self._command(f'AUTHINFO PASS {self.password}')
        if not line.startswith(b'281'):
            raise NNTPError(
                f"Kimlik dogrulama basarisiz ({self.host}): {_text(line)}")

    def _command(self, line: str) -> bytes:
        """Komut gonder ve tek satir yanit oku"""
        self._putline(line)
        return self._getline()

    def _putline(self, line: str):
        """Sunucuya satir gonder"""
        if self.socket is None:
            raise _Transient("Baglanti kapali")
        try:
            self.socket.sendall((line + '\r\n').encode('utf-8'))
        except OSError as e:
            raise _Transient(f"Gonderim hatasi: {e}") from e

    def _getline(self) -> bytes:
        """Sunucudan tam bir satir oku; yarim satir/EOF baglanti hatasidir"""
        if self.file is None:
            raise _Transient("Baglanti kapali")
        try:
            line = self.file.readline()
        except (OSError, ValueError) as e:  # SSLEOFError/SSLZeroReturnError OSError'dir
            raise _Transient(f"Okuma hatasi: {e}") from e
        if not line:
            raise _Transient("Baglanti kesildi (sunucu EOF gonderdi)")
        if not line.endswith(b'\r\n'):
            raise _Transient("Baglanti kesildi (satir yarim kaldi)")
        return line.rstrip(b'\r\n')

    def reconnect(self):
        """Baglantiyi kapatip yeniden ac (stat() gecici hatada otomatik yapar)"""
        self._close_transport()
        self._connect()

    def stat(self, message_id: str) -> Tuple[str, str]:
        """
        Article varligini kontrol et (STAT komutu)

        Gecici baglanti hatasinda (soket hatasi, EOF, 400-403/480-482/503)
        bir kez yeniden baglanip komutu tekrarlar; tekrar denemenin sonucu
        normal donus olarak verilir.

        Raises:
            ArticleNotFound: Article sunucuda yok (430/423)
            NNTPConnectionError: Yeniden baglandiktan sonra da baglanti koptu
            NNTPError: Baska bir NNTP hatasi (kimlik, 5xx ...)
        """
        if not message_id.startswith('<'):
            message_id = f'<{message_id}>'

        try:
            return self._stat_once(message_id)
        except _Transient as first:
            self._reconnect_for_retry(message_id, first)

        try:
            return self._stat_once(message_id)
        except _Transient as second:
            raise NNTPConnectionError(
                f"STAT {message_id} basarisiz: yeniden baglandiktan sonra da "
                f"baglanti koptu: {second}") from second

    def _stat_once(self, message_id: str) -> Tuple[str, str]:
        """Tek denemelik STAT - gecici hatalari _Transient olarak firlatir"""
        line = self._command(f'STAT {message_id}')
        code = line[:3]

        if code == b'223':
            return _text(line), message_id
        if code in (b'430', b'423'):
            raise ArticleNotFound(message_id)
        if code in TRANSIENT_CODES:
            raise _Transient(f"gecici sunucu yaniti: {_text(line)}")
        raise NNTPError(f"STAT hatasi: {_text(line)}")

    def _reconnect_for_retry(self, message_id: str, cause: Exception):
        """Ilk STAT denemesi gecici hatayla dustu: bir kez yeniden baglan"""
        try:
            self.reconnect()
        except NNTPConnectionError as e:
            raise NNTPConnectionError(
                f"STAT {message_id} basarisiz: baglanti koptu ({cause}), "
                f"yeniden baglanamadi: {e}") from e

    def _close_transport(self):
        """Soket/dosyayi kapat - hata firlatmaz, tekrar cagrilabilir"""
        file, sock = self.file, self.socket
        self.file = None
        self.socket = None
        if file is not None:
            try:
                file.close()
            except Exception:
                pass
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    def quit(self):
        """Baglantiyi kapat - idempotent, hata firlatmaz"""
        if self.socket is not None:
            try:
                self._putline('QUIT')
                self._getline()
            except Exception:
                pass
        self._close_transport()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit()


class NNTP(NNTPClient):
    """SSL olmadan baglanti"""

    def __init__(self, host: str, port: int = 119, user: Optional[str] = None,
                 password: Optional[str] = None, readermode: Optional[bool] = None,
                 timeout: int = 30):
        super().__init__(host, port, use_ssl=False, user=user,
                         password=password, timeout=timeout)


class NNTP_SSL(NNTPClient):
    """SSL ile baglanti (varsayilan port 563)"""

    def __init__(self, host: str, port: int = 563, user: Optional[str] = None,
                 password: Optional[str] = None,
                 ssl_context: Optional[ssl.SSLContext] = None,
                 readermode: Optional[bool] = None, timeout: int = 30):
        super().__init__(host, port, use_ssl=True, user=user, password=password,
                         timeout=timeout, ssl_context=ssl_context)
