# NZB Checker

NZB dosyalarını Usenet sunucusunda kontrol eden ve eksik parçalara göre otomatik olarak sınıflandıran Python scripti.

## Özellikler

-  NZB dosyalarını otomatik tarama
-  Usenet sunucusunda article kontrolü
-  **Çoklu thread ile paralel işlem (10 NZB aynı anda)**
-  Eksik parçaları tespit etme
-  Otomatik klasörleme (tam/eksik/hatalı)
-  Detaylı log kaydı
-  Config dosyası ile kolay yapılandırma
-  Örnek kontrol veya tam kontrol seçeneği
-  Python 3.13+ uyumlu

## Kurulum

1. Python 3.8 veya üstü sürüm gereklidir:
```bash
python --version
```

2. Bağımlılıkları yükleyin (sadece standard library kullanıldığı için gerek yok):
```bash
pip install -r requirements.txt
```

3. `config.json` dosyasını düzenleyin ve kendi bilgilerinizi girin.

## Yapılandırma

`config.json` dosyasını aşağıdaki şekilde düzenleyin:

```json
{
    "usenet_provider": {
        "host": "news.example.com",      // Usenet sunucu adresi
        "port": 563,                      // SSL port (veya 119 SSL olmadan)
        "username": "kullanici_adi",      // Usenet hesap kullanıcı adı
        "password": "sifre",              // Usenet hesap şifresi
        "use_ssl": true,                  // SSL kullanımı (önerilen)
        "connections": 10                 // Paralel bağlantı sayısı (aynı anda kaç NZB kontrol edilecek)
    },
    "paths": {
        "nzb_folder": "C:\\nzb_files",    // NZB dosyalarının bulunduğu klasör
        "complete_folder": "complete",     // Tam dosyalar için alt klasör (nzb_folder içinde)
        "missing_folder": "missing",       // Eksik dosyalar için alt klasör (nzb_folder içinde)
        "error_folder": "error"            // Hatalı dosyalar için alt klasör (nzb_folder içinde)
    },
    "settings": {
        "check_all_articles": false,      // true: tüm segmentleri kontrol et
        "sample_check_count": 5,          // Örnek kontrol sayısı
        "timeout": 30,                    // Bağlantı timeout (saniye)
        "log_level": "INFO"               // Log seviyesi: DEBUG, INFO, WARNING, ERROR
    }
}
```

## Kullanım

### Basit kullanım:
```bash
python nzb_checker.py
```

### Özel config dosyası ile:
```bash
python nzb_checker.py my_config.json
```

## Nasıl Çalışır?

1. **NZB Tarama**: Belirtilen klasördeki tüm `.nzb` dosyalarını bulur
2. **Paralel İşlem**: `connections` ayarına göre (varsayılan 10) aynı anda birden fazla NZB kontrol eder
3. **Parse**: Her NZB dosyasını parse eder ve segment bilgilerini çıkarır
4. **Kontrol**: 
   - `check_all_articles=false`: Örnek segmentleri kontrol eder (hızlı)
   - `check_all_articles=true`: Tüm segmentleri kontrol eder (yavaş ama kesin)
5. **Sınıflandırma**:
   - **complete/**: Eksik segment olmayan dosyalar
   - **missing/**: Eksik segmentli dosyalar
   - **error/**: Parse edilemeyen veya hatalı dosyalar

## Örnek Çıktı

```
============================================================
NZB Checker - NZB Dosya Kontrolü ve Sınıflandırma
============================================================

2025-11-27 10:30:15 - INFO - Klasör hazır: C:\output\complete
2025-11-27 10:30:15 - INFO - Klasör hazır: C:\output\missing
2025-11-27 10:30:15 - INFO - Klasör hazır: C:\output\error
2025-11-27 10:30:16 - INFO - Sunucuya bağlanıldı: news.example.com
2025-11-27 10:30:16 - INFO - Toplam 15 NZB dosyası bulundu
2025-11-27 10:30:16 - INFO - [1/15] Kontrol ediliyor: file1.nzb
2025-11-27 10:30:18 - INFO - ✓ Tam: file1.nzb (250 segment)
2025-11-27 10:30:18 - INFO - [2/15] Kontrol ediliyor: file2.nzb
2025-11-27 10:30:20 - WARNING - ✗ Eksik: file2.nzb (45/300 segment eksik)

============================================================
İŞLEM ÖZETİ
============================================================
Toplam NZB       : 15
Tam Dosyalar     : 12
Eksik Dosyalar   : 2
Hatalı Dosyalar  : 1
============================================================

Klasörler:
  Tam      : C:\output\complete
  Eksik    : C:\output\missing
  Hata     : C:\output\error
============================================================
```

## İpuçları

- **Hızlı Kontrol**: `check_all_articles: false` ile örnek kontrol yapın (önerilen)
- **Kesin Kontrol**: `check_all_articles: true` ile tüm segmentleri kontrol edin
- **Büyük Arşivler**: Büyük arşivler için önce örnek kontrolle başlayın
- **Paralel İşlem**: `connections` değerini artırarak (örn: 20) daha hızlı tarama yapabilirsiniz
- **Log Dosyası**: Tüm işlemler `nzb_checker.log` dosyasına kaydedilir
- **Performans**: Usenet provider'ınız çok bağlantıya izin veriyorsa `connections` değerini artırın

## Lisans

MIT License

## Uyarı

Bu araç kişisel kullanım içindir. Usenet kullanım koşullarına ve yerel yasalara uygun kullanın.
