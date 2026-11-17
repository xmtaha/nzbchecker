#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Basit NNTP Client - Python 3.13+ (nntplib kaldirildi)
"""

import socket
import ssl
from typing import Optional, Tuple


class NNTPError(Exception):
    """NNTP protokol hatasi"""


class NNTPConnectionError(NNTPError):
    """Baglanti/soket hatasi - article yoklugu ile karistirilmamali"""


class ArticleNotFound(NNTPError):
    """Article sunucuda yok (430)"""


class NNTPClient:
    """Basit NNTP client implementasyonu"""

    def __init__(self, host: str, port: int = 119, use_ssl: bool = False,
                 user: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 30):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.user = user
        self.password = password
        self.timeout = timeout
        self.socket = None
        self.file = None

        self._connect()

    def _connect(self):
        """Sunucuya baglan"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)

        try:
            if self.use_ssl:
                self.socket = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=self.host)
            else:
                self.socket = sock
            self.socket.connect((self.host, self.port))
        except OSError as e:
            self.socket = None
            sock.close()
            raise NNTPConnectionError(
                f"Baglanti kurulamadi ({self.host}:{self.port}): {e}") from e

        self.file = self.socket.makefile('rb')

        response = self._getline()
        if not response.startswith((b'200', b'201')):
            self.quit()
            raise NNTPConnectionError(
                f"Baglanti reddedildi: {response.decode('utf-8', errors='ignore')}")

        if self.user and self.password:
            self._authenticate()

    def _authenticate(self):
        """Kimlik dogrulamasi yap"""
        self._putline(f'AUTHINFO USER {self.user}'.encode('utf-8'))
        response = self._getline()

        if response.startswith(b'381'):
            self._putline(f'AUTHINFO PASS {self.password}'.encode('utf-8'))
            response = self._getline()

        if not response.startswith(b'281'):
            raise NNTPError(
                f"Kimlik dogrulama hatasi: {response.decode('utf-8', errors='ignore')}")

    def _putline(self, line: bytes):
        """Sunucuya satir gonder"""
        try:
            self.socket.sendall(line + b'\r\n')
        except OSError as e:
            raise NNTPConnectionError(f"Gonderim hatasi: {e}") from e

    def _getline(self) -> bytes:
        """Sunucudan satir oku"""
        try:
            line = self.file.readline()
        except OSError as e:
            raise NNTPConnectionError(f"Okuma hatasi: {e}") from e
        if not line:
            raise NNTPConnectionError("Baglanti kesildi")
        return line.rstrip(b'\r\n')

    def stat(self, message_id: str) -> Tuple[str, str]:
        """
        Article varligini kontrol et (STAT komutu)

        Raises:
            ArticleNotFound: Article sunucuda yok
            NNTPConnectionError/NNTPError: Kontrol yapilamadi
        """
        if not message_id.startswith('<'):
            message_id = f'<{message_id}>'

        self._putline(f'STAT {message_id}'.encode('utf-8'))
        response = self._getline()

        if response.startswith(b'223'):
            return response.decode('utf-8', errors='ignore'), message_id

        if response.startswith((b'430', b'423')):
            raise ArticleNotFound(message_id)

        raise NNTPError(f"STAT hatasi: {response.decode('utf-8', errors='ignore')}")

    def quit(self):
        """Baglantiyi kapat"""
        try:
            if self.socket:
                self._putline(b'QUIT')
                self._getline()
        except Exception:
            pass
        finally:
            if self.file:
                self.file.close()
                self.file = None
            if self.socket:
                self.socket.close()
                self.socket = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit()


class NNTP(NNTPClient):
    """SSL olmadan baglanti"""

    def __init__(self, host: str, port: int = 119, user: str = None,
                 password: str = None, readermode: bool = None, timeout: int = 30):
        super().__init__(host, port, use_ssl=False, user=user,
                         password=password, timeout=timeout)


class NNTP_SSL(NNTPClient):
    """SSL ile baglanti"""

    def __init__(self, host: str, port: int = 563, user: str = None,
                 password: str = None, ssl_context: ssl.SSLContext = None,
                 readermode: bool = None, timeout: int = 30):
        super().__init__(host, port, use_ssl=True, user=user,
                         password=password, timeout=timeout)
