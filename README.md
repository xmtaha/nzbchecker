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
-  **Masaüstü arayüz (GUI)**: tüm ayarlar arayüzden, binlerce dosya tek seferde
-  Python 3.9+ uyumlu (yalnızca standart kütüphane)

## Kurulum

1. Python 3.9 veya üstü sürüm gereklidir:
```bash
python3 --version
```

2. Bağımlılık yok - sadece standart kütüphane kullanılır. GUI için tkinter gerekir
   (Debian/Ubuntu'da eksikse: `sudo apt install python3-tk`).

3. GUI'yi açıp ayarları arayüzden girin (**önerilen**), ya da `config.json` dosyasını elle düzenleyin.

### AppImage (paketlenmiş sürüm)

İndir: https://github.com/xmtaha/nzbchecker/releases/latest (dosya: `NZB-Checker-x86_64.AppImage`).
Tek dosyadır, kurulum gerektirmez:

```bash
chmod +x NZB-Checker-x86_64.AppImage
./NZB-Checker-x86_64.AppImage
```

Sistemdeki `python3` + `tkinter` kullanılır (yoksa uyarı gösterir:
`sudo apt install python3-tk`). Python paket içine gömülmez, bu yüzden dosya ~1 MB'dir.

Yeniden üretmek için: `./build_appimage.sh` (appimagetool'u gerekirse indirir).

Sürükle-bırak için `tkdnd/` klasöründeki Tcl paketi kullanılır (tkdnd 2.9.5, Tcl 9 uyumlu).
Paket repoda hazır durur, ayrıca kurulum gerekmez; ayrıntı için `tkdnd/NOTICE.txt`.

### Gömülü Python'lu sürüm (bundled)

İki AppImage yayınlanır:

| Dosya | Boyut | Python |
|---|---|---|
| `NZB-Checker-x86_64.AppImage` | ~1 MB | sistemdeki `python3` + `tkinter` kullanılır |
| `NZB-Checker-bundled-x86_64.AppImage` | ~11 MB | Python 3.15 ve Tcl/Tk 9.0 **paket içinde** (sistemde python olmasa da çalışır) |

Bundled sürümü üretmek için önce Python/Tcl-Tk ağacını hazırlayın (dağıtımın kendi
`python3` + `libpython` + `libtcl9/libtcl9tk9` + `tcl9.0/tk9.0` script dizinleri), sonra:

```bash
./build_appimage.sh NZB-Checker-bundled-x86_64.AppImage /yol/python-agaci
```

`python-agaci/` düzeni: `python3`, `libpython3.x.so`, `libtcl9*.so`, `lib/tcl9.0`, `lib/tk9.0`.
`AppRun-bundled` bu düzeni `LD_LIBRARY_PATH`, `TCL_LIBRARY`, `TK_LIBRARY` ile kullanır.

## Yapılandırma

Ayar dosyasının yeri:

| Kullanım | Dosya |
|---|---|
| Masaüstü arayüz (ve AppImage) | `~/.config/nzb_checker/config.json` (ilk açılışta otomatik oluşur) |
| Komut satırı | `config.json` (betiğin yanında) |

`NZB_CHECKER_CONFIG` ortam değişkeni ile arayüzün kullanacağı dosya değiştirilebilir.

`config.json` dosyasını aşağıdaki şekilde düzenleyin:

```json
{
    "providers": [                        // istediğiniz kadar sağlayıcı ekleyin
        {
            "name": "Sağlayıcı 1",        // görünen ad
            "host": "news.example.com",   // Usenet sunucu adresi
            "port": 563,                  // SSL port (veya 119 SSL olmadan)
            "username": "kullanici_adi",  // Usenet hesap kullanıcı adı
            "password": "sifre",          // Usenet hesap şifresi
            "use_ssl": true,              // SSL kullanımı (önerilen)
            "connections": 30             // Eşzamanlı bağlantı sayısı (provider limitine göre; tipik 50-60)
        }
    ],
    "paths": {
        "nzb_folder": "",                 // NZB klasörü (arayüzde sürükle-bırak ile de seçilir)
        "complete_folder": "complete",     // Tam dosyalar için alt klasör (nzb_folder içinde)
        "missing_folder": "missing",       // Eksik dosyalar için alt klasör (nzb_folder içinde)
        "error_folder": "error"            // Hatalı dosyalar için alt klasör (nzb_folder içinde)
    },
    "settings": {
        "check_all_articles": false,      // true: tüm segmentleri kontrol et
        "sample_check_count": 20,         // En az kaç segment kontrol edilsin
        "sample_ratio": 0.02,             // Ayrıca dosyanın bu kadarı kontrol edilir (0.02 = %2)
        "timeout": 30,                    // Bağlantı timeout (saniye)
        "log_level": "INFO"               // Log seviyesi: DEBUG, INFO, WARNING, ERROR
    }
}
```

## Kullanım

### Masaüstü arayüz (önerilen):
```bash
python3 nzb_gui.py         # ya da ./NZB-Checker-x86_64.AppImage
```

Tüm ayarlar arayüzden yapılır (sağlayıcılar, bağlantı sayısı, klasörler, örnekleme,
timeout, log seviyesi) ve **Ayarları kaydet** ile `~/.config/nzb_checker/config.json` dosyasına yazılır.

- **Sürükle-bırak**: NZB klasörünü pencereye sürükleyip bırakın; klasör hemen ayarlanır ve taranır
  (tkdnd Tcl paketi gerekir - repodaki `tkdnd/` klasöründe gelir, AppImage içinde de vardır).
  Paket yoksa arayüz çalışmaya devam eder, **Klasör seç** düğmesi kullanılır
- **Çoklu sağlayıcı**: Ayarlar > Usenet sağlayıcıları listesinde **+ Ekle** ile istediğiniz kadar
  sağlayıcı tanımlayın (ekleme/düzenleme ayrı pencerede açılır; satıra çift tıklayarak düzenlenir).
  **Bağlantıyı test et** düğmesi hem pencere içinde hem listede çalışır. Her NZB **tüm sağlayıcılarda** kontrol edilir; bir segment ancak
  **hiçbir sağlayıcıda bulunamazsa** eksik sayılır (biri bulursa dosya tamdır). Bağlantı sayıları
  toplanır: 2 sağlayıcı x 30 = 60 paralel bağlantı
- **Klasörü tara**: klasördeki tüm `.nzb` dosyalarını listeler (binlerce dosya sorunsuz;
  liste partiler halinde doldurulur)
- **Başlat / Durdur**: tarama arka planda çalışır, arayüz donmaz. Durdur, o an işlenen
  dosyaları `cancelled` olarak işaretler ve **dosyaları taşımaz** - sonraki çalıştırmada
  tekrar denenir
- Canlı durum tablosu (tam/eksik/hatalı, segment/kontrol/eksik sayıları, **Eksik %** tahmini, süre),
  "Sadece eksik/hatalı" filtresi ve arama kutusu
- Ölçek testi: 2.000 dosya x 20 segment (40.000 STAT), 50 bağlantı -> ~3 sn (yerel sunucu)

### Komut satırı:
```bash
python3 nzb_checker.py                # config.json ile
python3 nzb_checker.py my_config.json # özel config ile
```

### Test:
```bash
python3 test_nzb_checker.py
```

## Nasıl Çalışır?

1. **NZB Tarama**: Belirtilen klasördeki tüm `.nzb` dosyalarını bulur
2. **Paralel İşlem**: `connections` kadar bağlantı açılır. Hem birden fazla NZB paralel işlenir
   hem de **tek bir NZB'nin segmentleri bu bağlantılara bölünür** — 20.000 segmentlik dosya
   bile tüm bağlantıları kullanır. Bağlantılar worker thread'lerce yeniden kullanılır
   (dosya başına yeniden el sıkışma yok)
3. **Parse**: Her NZB dosyasını parse eder ve segment bilgilerini çıkarır
4. **Kontrol**: 
   - `check_all_articles=false`: Örnek segmentleri kontrol eder (hızlı).
     Kontrol edilen segment sayısı = `max(sample_check_count, toplam × sample_ratio)`,
     dosyaya eşit yayılır (ilk ve son segment dahil)
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
- **Paralel İşlem**: `connections` değerini provider limitine kadar artırın (tipik 50-60; fazlası `502 too many connections` verir)
- **Tam Tarama Hızı**: `check_all_articles: true` ile 20.000 segment, 50 bağlantıda ~400 STAT/bağlantı → 30 ms RTT ile ~12 s
- **Log Dosyası**: Tüm işlemler `nzb_checker.log` dosyasına kaydedilir
- **Performans**: Usenet provider'ınız çok bağlantıya izin veriyorsa `connections` değerini artırın

## Lisans

MIT License

## Uyarı

Bu araç kişisel kullanım içindir. Usenet kullanım koşullarına ve yerel yasalara uygun kullanın.
