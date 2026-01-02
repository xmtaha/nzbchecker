#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Basit NNTP Client - Python 3.13+
"""

import socket
import ssl
from typing import Tuple, Optional


class NNTPClient:
    """Basit NNTP client implementasyonu"""
    
    def __init__(self, host: str, port: int = 119, use_ssl: bool = False,
                 user: Optional[str] = None, password: Optional[str] = None,
                 timeout: int = 30):
        """
        Args:
            host: NNTP sunucu adresi
            port: Port numarası
            use_ssl: SSL kullanımı
            user: Kullanıcı adı
            password: Şifre
            timeout: Bağlantı timeout
        """
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
        """Sunucuya bağlan"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        
        if self.use_ssl:
            context = ssl.create_default_context()
            self.socket = context.wrap_socket(sock, server_hostname=self.host)
        else:
            self.socket = sock
        
        self.socket.connect((self.host, self.port))
        self.file = self.socket.makefile('rb')
        
        response = self._getline()
        if not response.startswith(b'200') and not response.startswith(b'201'):
            raise Exception(f"Bağlantı hatası: {response.decode('utf-8', errors='ignore')}")
        
        if self.user and self.password:
            self._authenticate()
    
    def _authenticate(self):
        """Kimlik doğrulaması yap"""
        self._putline(f'AUTHINFO USER {self.user}'.encode('utf-8'))
        response = self._getline()
        
        if response.startswith(b'381'):
            self._putline(f'AUTHINFO PASS {self.password}'.encode('utf-8'))
            response = self._getline()
            
            if not response.startswith(b'281'):
                raise Exception(f"Kimlik doğrulama hatası: {response.decode('utf-8', errors='ignore')}")
    
    def _putline(self, line: bytes):
        """Sunucuya satır gönder"""
        self.socket.sendall(line + b'\r\n')
    
    def _getline(self) -> bytes:
        """Sunucudan satır oku"""
        line = self.file.readline()
        if not line:
            raise Exception("Bağlantı kesildi")
        return line.rstrip(b'\r\n')
    
    def stat(self, message_id: str) -> Tuple[str, str]:
        """
        Article varlığını kontrol et (STAT komutu)
        
        Args:
            message_id: Message ID (< > olmadan)
            
        Returns:
            (response, message_id)
            
        Raises:
            Exception: Article bulunamadı
        """
        if not message_id.startswith('<'):
            message_id = f'<{message_id}>'
        
        self._putline(f'STAT {message_id}'.encode('utf-8'))
        response = self._getline()
        
        if response.startswith(b'223'):
            return response.decode('utf-8', errors='ignore'), message_id
        
        raise Exception(f"Article bulunamadı: {message_id}")
    
    def quit(self):
        """Bağlantıyı kapat"""
        try:
            self._putline(b'QUIT')
            self._getline()
        except:
            pass
        finally:
            if self.file:
                self.file.close()
            if self.socket:
                self.socket.close()
    
    def __enter__(self):
        """Context manager enter"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.quit()


class NNTP(NNTPClient):
    
    
    def __init__(self, host: str, port: int = 119, user: str = None, 
                 password: str = None, readermode: bool = None):
        super().__init__(host, port, use_ssl=False, user=user, password=password)


class NNTP_SSL(NNTPClient):
    

    def __init__(self, host: str, port: int = 563, user: str = None, 
                 password: str = None, ssl_context: ssl.SSLContext = None):
        super().__init__(host, port, use_ssl=True, user=user, password=password)
