# NZB Checker

NZB dosyalarını Usenet sunucusunda kontrol eden ve eksik parçalara göre otomatik olarak sınıflandıran Python scripti.

## Özellikler

-  NZB dosyalarını otomatik tarama
-  Usenet sunucusunda article kontrolü
-  **Çoklu thread ile paralel işlem (10 NZB aynı anda)**
-  **Kademeli sağlayıcı taraması**: her segment her sağlayıcıya değil, yalnızca öncekilerin
   bulamadığı segmentler sonraki sağlayıcıya sorulur
-  **Sonuç önbelleği (SQLite)**: tekrar taramalarda aynı segment için STAT gönderilmez
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

3. GUI'yi açıp ayarları arayüzden girin (**önerilen**), ya da `config.example.json` dosyasını `config.json` olarak kopyalayıp elle düzenleyin.

### AppImage (paketlenmiş sürüm)

İndir: https://github.com/xmtaha/nzbchecker/releases/latest (dosya: `NZB-Checker-x86_64.AppImage`).
Tek dosyadır, kurulum gerektirmez:

```bash
chmod +x NZB-Checker-x86_64.AppImage
./NZB-Checker-x86_64.AppImage
```

Sistemdeki `python3` + `tkinter` kullanılır (yoksa uyarı gösterir:
`sudo apt install python3-tk`). Python paket içine gömülmez, bu yüzden dosya ~1 MB'dir.

Yeniden üretmek için: `./packaging/build_appimage.sh` (appimagetool'u gerekirse indirir).

Sürükle-bırak için `tkdnd/` klasöründeki Tcl paketi kullanılır (derlemede `nzb_checker/gui/tkdnd` içine kopyalanır) (tkdnd 2.9.5, Tcl 9 uyumlu).
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
./packaging/build_appimage.sh NZB-Checker-bundled-x86_64.AppImage /yol/python-agaci
```

`python-agaci/` düzeni: `python3`, `libpython3.x.so`, `libtcl9*.so`, `lib/tcl9.0`, `lib/tk9.0`.
`AppRun-bundled` bu düzeni `LD_LIBRARY_PATH`, `TCL_LIBRARY`, `TK_LIBRARY` ile kullanır.

## Proje yapısı

```
nzb_checker/            # uygulama paketi
├── __main__.py         #   giriş: python3 -m nzb_checker [gui|cli] [config]
├── engine/             #   ağ + kontrol motoru (arayüzden bağımsız)
│   ├── nntp_client.py  #     NNTP/SSL istemcisi (yeniden bağlanma, keepalive)
│   ├── cache.py        #     sağlayıcı yanıtı önbelleği (SQLite)
│   ├── checker.py      #     NZBChecker: paralel tarama ve sınıflandırma
│   └── cli.py          #     komut satırı akışı
└── gui/                #   tkinter arayüzü
    ├── theme.py        #     renkler, fontlar, ayar alanı tanımları
    ├── config.py       #     ayar dosyası okuma/yazma, varsayılanlar
    ├── widgets.py      #     sağlayıcı metni/dönüşümü, log köprüsü
    ├── dialogs.py      #     sağlayıcı ekleme/düzenleme penceresi
    └── app.py          #     ana pencere (liste, ayarlar, tarama)
nzb_gui.py              # GUI kısayolu
nzb_checker.py          # CLI kısayolu
tkdnd/                  # sürükle-bırak Tcl paketi (vendor)
packaging/              # AppRun, AppRun-bundled, build_appimage.sh, .desktop
tests/                  # sahte NNTP sunucusuyla uçtan uca test
```

## Yapılandırma

Ayar dosyasının yeri:

| Kullanım | Dosya |
|---|---|
| Masaüstü arayüz (ve AppImage) | `~/.config/nzb_checker/config.json` (ilk açılışta otomatik oluşur) |
| Komut satırı | `config.json` (çalıştırma dizininde; `config.example.json` örnek) |

`NZB_CHECKER_CONFIG` ortam değişkeni ile arayüzün kullanacağı dosya değiştirilebilir.

`config.example.json` dosyasını `config.json` olarak kopyalayıp aşağıdaki şekilde düzenleyin:

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
        "cache": true,                    // Sonuçları SQLite önbelleğinde tut
        "cache_ttl_days": 14,             // Önbellek kaydı ömrü (gün)
        "log_level": "INFO"               // Log seviyesi: DEBUG, INFO, WARNING, ERROR
    }
}
```

Önbellek dosyası varsayılan olarak `~/.cache/nzb_checker/cache.db`
(veya `$XDG_CACHE_HOME/nzb_checker/cache.db`) konumundadır. `NZB_CHECKER_CACHE` ortam
değişkeni ya da `settings.cache_path` ile değiştirilebilir; `cache: false` ile kapatılır.

## Kullanım

### Masaüstü arayüz (önerilen):
```bash
./nzb_gui.py               # ya da python3 -m nzb_checker
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
- **Sağlayıcı bazlı sonuç**: tablodaki **Sağlayıcılar** kolonu her dosya için hangi sağlayıcının tam
  hangisinin eksik olduğunu gösterir (`Frugal ✓ · Eweka %3.2`). Yüzdeler o sağlayıcıya **sorulan**
  segmentlere göredir (kademeli tarama: sonraki sağlayıcıya yalnızca öncekilerin bulamadıkları
  sorulur). Dosya toplamda "Tam" olsa bile bir sağlayıcıda eksik segment varsa satır
  **Bazı sağlayıcılarda eksik** filtresiyle bulunabilir
  ve ayrıntı log'a yazılır (`Frugal: tam · Eweka: %3.2 eksik (12 segment)`)
- **Canlı ilerleme**: büyük bir NZB taranırken satırdaki kontrol sayısı ve durum çubuğu
  segment ilerlemesini gösterir
- Ölçek testi: 2.000 dosya x 20 segment (40.000 STAT), 50 bağlantı -> ~3 sn (yerel sunucu)

### Komut satırı:
```bash
python3 -m nzb_checker cli               # config.json ile
python3 -m nzb_checker cli my_config.json # özel config ile
```

### Test:
```bash
python3 tests/test_nzb_checker.py
```

## Nasıl Çalışır?

1. **NZB Tarama**: Belirtilen klasördeki tüm `.nzb` dosyalarını bulur
2. **Paralel ve kademeli işlem**: `connections` kadar bağlantı açılır. Hem birden fazla NZB
   paralel işlenir hem de **tek bir NZB'nin segmentleri bu bağlantılara bölünür** — 20.000
   segmentlik dosya bile tüm bağlantıları kullanır. Sağlayıcılar sırayla taranır: P1'in
   bulamadığı segmentler P2'ye, P2'nin bulamadıkları P3'e sorulur. Böylece sağlam dosyalarda
   STAT sayısı sağlayıcı sayısıyla çarpılmaz. Bağlantılar worker thread'lerce yeniden kullanılır
   (dosya başına yeniden el sıkışma yok). Önbellekte taze kaydı olan segment hiç sorulmaz.
   Bir sağlayıcı tamamen çökerse tarama diğerleriyle sürer; ancak **hiçbir** sağlayıcı
   taranamadıysa dosya `error/`'a gider
3. **Parse**: Her NZB dosyasını parse eder ve segment bilgilerini çıkarır
4. **Kontrol**: 
   - `check_all_articles=false`: Örnek segmentleri kontrol eder (hızlı).
     Kontrol edilen segment sayısı = `max(sample_check_count, toplam × sample_ratio)`,
     dosyaya eşit yayılır (ilk ve son segment dahil)
   - `check_all_articles=true`: Tüm segmentleri kontrol eder (yavaş ama kesin)
5. **Sınıflandırma**:
   - **complete/**: Eksik segment olmayan dosyalar
   - **missing/**: Eksik segmentli dosyalar
   - **error/**: Parse edilemeyen veya hiçbir sağlayıcıda taranamayan dosyalar
   - Hedef klasörde aynı ad varsa dosya `ad (1).nzb` olarak taşınır; taşıma başarısız olursa
     dosya yerinde bırakılır ve özette "Taşınamayan" olarak raporlanır

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
- **Önbellek**: Aynı arşivi tekrar tararken `cache: true` (varsayılan) ile STAT sayısı
  neredeyse sıfıra iner. Güncel olmayan sonuçlar için `cache_ttl_days` değerini düşürün
- **Log Dosyası**: Tüm işlemler `nzb_checker.log` dosyasına kaydedilir
- **Performans**: Usenet provider'ınız çok bağlantıya izin veriyorsa `connections` değerini artırın

## Lisans

MIT License

## Uyarı

Bu araç kişisel kullanım içindir. Usenet kullanım koşullarına ve yerel yasalara uygun kullanın.
