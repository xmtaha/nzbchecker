# AGENTS.md — Ajanlar için geliştirici rehberi

Bu dosya, depo üzerinde çalışacak yapay zekâ ajanları (ve yeni geliştiriciler) için
teknik referanstır. Kullanıcıya dönük kullanım/özellik anlatımı `README.md` içindedir;
burada **mimari, sözleşmeler, komutlar ve tuzaklar** anlatılır.

> Kural: Davranış değiştiren her commit, ilgili bölümü burada günceller. README
> kullanıcı içindir, AGENTS.md ajan/katkıcı içindir; ikisi çelişirse kod esastır.

## 1. Proje özeti

NZB dosyalarını Usenet sağlayıcılarında `STAT` ile kontrol edip tam / eksik / hatalı
olarak sınıflandıran ve dosyaları alt klasörlere taşıyan bir Python uygulaması.
İki yüz vardır:

- **Motor** (`nzb_checker/engine/`): arayüzden bağımsız; ağ, önbellek, tarama, rapor.
- **GUI** (`nzb_checker/gui/`): tkinter masaüstü arayüzü; motoru thread içinde çalıştırır.

Temel kısıtlar:

- **Yalnızca Python standart kütüphanesi** kullanılır. Yeni çalışma zamanı bağımlılığı
  eklemeyin (paketleme/AppImage bundan etkilenir).
- **Python 3.9+** uyumludur. `match`, `X | Y` tip birleşimi, `list[str]` gibi 3.10+
  sözdizimini kullanmayın; `typing.List/Dict/Optional` biçimini koruyun.
- Kullanıcıya görünen tüm metinler (GUI, log, CSV başlıkları) **Türkçe**'dir.
  Kod içi yorumlar da Türkçe yazılmıştır; tutarlılığı koruyun.
- Uygulama **salt okunur** bir AppImage içinden çalışabilir. Çalışma dizinine asla
  yazmayın; kalıcı dosyalar `XDG_CACHE_HOME` / `~/.config` altına gider.

## 2. Hızlı komutlar

```bash
# GUI (varsayılan)
python3 -m nzb_checker
./nzb_gui.py

# CLI
python3 -m nzb_checker cli config.json
python3 nzb_checker.py config.json

# Uçtan uca testler (pytest yok; saf assert'ler, sahte NNTP sunucusu)
python3 tests/test_nzb_checker.py

# AppImage üretimi (sistem python3 + tkinter)
./packaging/build_appimage.sh

# Gömülü Python'lu AppImage (python ağacı hazır olmalı)
./packaging/build_appimage.sh NZB-Checker-bundled-x86_64.AppImage /yol/python-agaci
```

Testler `XDG_CACHE_HOME`'u geçici dizine çevirir ve `os.chdir(tmp)` yapar; gerçek
`~/.cache` kirlenmez. Testi çalıştırmadan önce depo kökünde `config.json` gerekmez.

## 3. Depo haritası

| Yol | Sorumluluk |
|---|---|
| `nzb_checker/__init__.py` | `__version__`: çalışma zamanındaki sürüm kaynağı (`updater.py` bunu kullanır); `pyproject.toml` ile senkron tutulur |
| `nzb_checker/__main__.py` | `python -m nzb_checker [gui\|cli] [config]`; eski `config.json` argümanını GUI'ye yönlendirir |
| `nzb_checker/engine/checker.py` | `NZBChecker`: config, log, klasörler, provider sırası, ön uçuş, kademeli tarama, sınıflandırma, taşıma, rapor |
| `nzb_checker/engine/nntp_client.py` | `NNTPClient` + `NNTP`/`NNTP_SSL`; bağlantı, AUTHINFO, `stat`, `stat_many` (pipelining), geçici hatada 1 kez yeniden bağlanma |
| `nzb_checker/engine/cache.py` | `ResultCache` (SQLite), `provider_key`, `last_scan.json` geçmişi, XDG durum dizini |
| `nzb_checker/engine/cli.py` | CLI akışı: `NZBChecker` oluştur → tara → özet |
| `nzb_checker/updater.py` | GitHub release kontrolü (indirme yok), `version_key`, `is_newer`, `pick_asset` |
| `nzb_checker/gui/app.py` | `App` ana pencere: tablo, filtre/sıralama, ayarlar, worker thread, olay kuyruğu, rapor dışa aktarma |
| `nzb_checker/gui/config.py` | `CONFIG_PATH`, `DEFAULTS`, `ensure_config`, `read_config` (eksikleri tamamlar), `write_config` (0o600) |
| `nzb_checker/gui/theme.py` | Renkler, fontlar, `PROVIDER_FIELDS` / `SPEC` alan tanımları, `PARTIAL_THRESHOLD` |
| `nzb_checker/gui/widgets.py` | `provider_text` (✓ / %x.y), `provider_from_vars`, `QueueLogHandler` |
| `nzb_checker/gui/dialogs.py` | Provider penceresi, klasör/ileti/güncelleme diyalogları |
| `nzb_gui.py`, `nzb_checker.py` | İnce başlatıcılar (paket içi girişe delege eder) |
| `tests/test_nzb_checker.py` | Sahte NNTP sunucusu + tüm senaryolar (tek dosya) |
| `packaging/` | `AppRun`, `AppRun-bundled`, `build_appimage.sh`, `.desktop` |
| `tkdnd/` | Sürükle-bırak için vendor Tcl paketi (2.9.5); derlemede `nzb_checker/gui/tkdnd` içine kopyalanır |
| `config.example.json` | CLI örnek yapılandırması (gerçek `config.json` asla commit edilmez) |

## 4. Çalışma zamanı akışı (motor)

`NZBChecker.process_nzb_files()` sırası:

1. `iter_nzb_files(nzb_folder, self.folders.values())` — `.nzb`/`.NZB` uzantılı dosyaları
   alt klasörlerle listeler; **çıktı klasörlerinin (complete/missing/error) altındaki
   dosyaları atlar** (yoksa taşınmış dosyalar sonsuz yeniden taranır).
2. `_preflight()` — her provider'a tek bağlantıyla paralel erişim denemesi. Erişilemeyen
   provider o taramada tamamen atlanır. **Hiçbiri erişilemiyorsa** dosya taşınmaz,
   `scan_end` `error_message` ile yayınlanır (sayacı taşıyan `error` alanı değişmez).
3. `segments_to_check()` — örneklem seçimi:
   `sample = min(total, max(sample_check_count, int(total * sample_ratio)))`.
   `check_all_articles` veya `sample >= total` ise tüm segmentler; değilse ilk+son dahil
   eşit yayılmış örnek. **Örnek modunda eksik görülse bile otomatik tam taramaya
   geçilmez** (bilinçli tasarım).
4. Kademeli tarama: provider sırası geçmiş isabet oranına göre (`last_scan.json`,
   `(stat-missing)/stat`, bilinmeyen 0.5). P1 örneklerin tamamına, P2 yalnızca P1'in
   bulamadıklarına, P3 kalanlara sorulur. Her provider dilimi
   `ceil(aday / connections)` boyutunda parçalara bölünür; parçalar ortak
   `ThreadPoolExecutor`'a verilir.
5. `_finish_file()` — bir segment **herhangi bir provider'da bulunduysa** mevcut sayılır.
   Hiçbir provider aşamasını tamamlayamadıysa dosya `error` olur (ağ/kimlik sorunu).
   Eksik tahmini: `answered < total` ise `total * found_missing / answered` ile ölçeklenir.
6. `_classify()` — loglar, dosyayı hedef klasöre taşır (ad çakışırsa `ad (1).nzb`),
   `self.results` satırını ve sayaçları günceller, `file_done` olayı yayınlar.
   Taşıma başarısızsa dosya **yerinde bırakılır** ve `moved=False` raporlanır.
7. `_emit_scan_end()` — özet + metrikler; iptal veya hata yoksa `last_scan.json` yazılır.

İptal (`cancel()`): `cancel_event` set edilir. Uçuştaki dosyalar `cancelled` olarak
yayınlanır ve **taşınmaz**; kalan dosyalar sonraki çalıştırmada tekrar denenir.

## 5. Eşzamanlılık ve bağlantı sözleşmesi

- `max_workers = sum(provider.connections)`; erişilemeyen provider'lar bu toplamdan düşülür.
- Bağlantılar **thread-local**: her worker thread, her provider için ilk kullanımda bir
  bağlantı açar ve sonraki görevlerde yeniden kullanır. Dosya başına yeniden el sıkışma yok.
- Provider başına eşzamanlı bağlantı sınırı `Semaphore(connections)` ile korunur.
- Bozuk bağlantı `_drop_connection()` ile kapatılır; sonraki görevde yenisi açılır.
- `PIPELINE_WINDOW = 32`: tek seferde en fazla 32 `STAT` gönderilir, yanıtlar sırayla
  okunur. Pencere içinde geçici hata olursa `stat_many` **bir kez** yeniden bağlanıp
  tekrar dener; ikinci hatada `NNTPConnectionError`.
- `TRANSIENT_CODES = (400,401,402,403,480,481,482,503)`. `430/423` = makale yok (hata
  değil, `False`). Tekil `stat()` `ArticleNotFound` fırlatır; `_check_chunk` toplu
  `stat_many` kullanır.
- Motor worker thread'lerinden **tkinter'a dokunulmaz**; GUI yalnızca olay kuyruğuna yazar.

## 6. Önbellek sözleşmesi

`ResultCache` (`engine/cache.py`), SQLite tablosu `results(provider, message_id,
present, checked_at)`, WAL, thread-safe.

- Anahtar: `provider_key(provider)` = sha1(`host:port:username`) ilk 16 hex.
  **Şifre anahtara girmez** (şifre değişince önbellek geçersiz olmasın).
- "Var" kayıtları **2 gün** (`PRESENT_TTL_DAYS`), "yok" kayıtları `cache_ttl_days`
  (varsayılan 14 gün) geçerlidir. Amaç: DMCA/retention silmelerini yakalamak.
- Konum: `settings.cache_path` > `NZB_CHECKER_CACHE` > `$XDG_CACHE_HOME/nzb_checker/cache.db`.
  `settings.cache=false` ile tamamen kapatılır.
- SQLite açılamazsa önbellek **sessizce** devre dışı kalır; tarama sürer.
- `_check_chunk` önce `get_many` ile taze kayıtları kullanır; yalnızca kalanlar için STAT
  gönderir; sonuçları `put_many` ile yazar. Dilim hatasında o ana kadar yanıtlanan
  id'ler yine yazılır (geçerli sonuç ziyan olmasın); eksik kalanlar sonraki sağlayıcıya sorulur.

## 7. Olay protokolü (motor → GUI)

`NZBChecker(config_path, events=callback)` — callback `events(kind, **data)`.
Dinleyici hatası iş akışını bozmaz (loglanır). Alan adları GUI tarafından tüketilir;
**yeniden adlandırmadan önce `app.py:_handle` ve testleri güncelleyin.**

| kind | Alanlar | Tüketici davranışı |
|---|---|---|
| `scan_start` | `total`, `started` | Progress bar, sayaçlar sıfırlanır |
| `file_start` | `name`, `segments`, `to_check` | Satır `running`, ETA paydası artar |
| `file_progress` | `name`, `checked`, `to_check`, `segments`, `scan_checked`, `elapsed` | Satır ilerlemesi + hız/ETA |
| `file_done` | `name`, `status`, `segments`, `checked`, `missing`, `percent`, `providers`, `moved`, `elapsed`, `checked_all` (iptalde yok) | Satır güncellenir, sayaç/log |
| `file_error` | `name`, `message` | Log'a hata |
| `progress` | `completed`, `total` | Genel ilerleme çubuğu |
| `scan_end` | `total`, `complete`, `missing`, `error` (hatalı dosya sayısı), `cancelled`, `move_failed`, `elapsed`, `segments`, `checked_segments`, `missing_segments`, `provider_stats`; bağlantı hatası varsa ayrıca `error_message` | Özet, durum, düğmeler |
| `fatal` | `message` | Hata diyaloğu |

`status` değerleri: `queued`, `running`, `complete`, `missing`, `error`, `cancelled`.
GUI filtre "sadece eksik/hatalı" görünümünde `VISIBLE_WHEN_FILTERED` kullanır.

GUI tarafı: worker thread `self.q` kuyruğuna yazar; `App._drain` her 120 ms'de en fazla
3000 olayı boşaltıp `_handle`'a verir. Ağ/istek asla ana thread'de yapılmaz
(provider testi ve güncelleme kontrolü de ayrı daemon thread'lerdedir).

## 8. Yapılandırma

GUI: `NZB_CHECKER_CONFIG` > `~/.config/nzb_checker/config.json` (ilk açılışta
`ensure_config()` varsayılanlarla oluşturur, `chmod 0600`).
CLI: argüman veya çalışma dizininde `config.json`.

`read_config()` eksik `paths`/`settings` anahtarlarını `DEFAULTS`'tan tamamlar, eski
`usenet_provider` biçimini `providers` listesine çevirir. `providers` listesi
varsayılanla **birleştirilmez** (kullanıcının listesi esastır).

Önemli anahtarlar: `providers[].{name,host,port,username,password,use_ssl,connections}`,
`paths.{nzb_folder,complete_folder,missing_folder,error_folder}`,
`settings.{check_all_articles,sample_check_count,sample_ratio,timeout,cache,
cache_ttl_days,cache_path,check_updates,log_level}`. Yeni ayar eklerken:
`gui/config.py:DEFAULTS` + `gui/theme.py:SPEC` (GUI'de görünecekse) +
`README.md` tablosu + gerekirse `config.example.json`.

Durum dosyaları (`default_state_dir()`):
`cache.db`, `nzb_checker.log`, `last_scan.json`. Log/önbellek yazılamazsa uygulama
konsola düşer, çökmez.

## 9. Rapor dışa aktarma

`NZBChecker.export_report(destination)`:

- `rapor.csv` — UTF-8 BOM (`utf-8-sig`), başlıklar:
  `dosya, durum, segment, kontrol, eksik, eksik_yuzde, saglayicilar, sure_sn, mod, tasinidi`
  (`mod`: `tam`/`ornek`; `tasinidi`: `evet`/`hayir`).
- `eksik-segmentler/<ad>.txt` — satır başına bir message-id; alt klasör ayraçları `_`
  ile düzleştirilir (`a/b.nzb` → `a_b.nzb.txt`).
- GUI'de yalnızca `self.checker.results` doluysa çalışır.

## 10. Testler

Tek dosya, saf `assert`, çerçeve yok: `python3 tests/test_nzb_checker.py`.

- `Handler` sahte NNTP sunucusudur: `AUTHINFO`, `STAT`, `QUIT`; `drop*` ile başlayan
  id'lerde bağlantıyı koparır (yeniden bağlanma testi). Makale kümeleri port bazlıdır
  (`EXIST_BY_PORT`), böylece çoklu provider senaryoları farklı port kullanır.
- `write_config()` her test için geçici config yazar, `XDG_CACHE_HOME/last_scan.json`'u
  siler (provider sırası testler arası sızmasın).
- `Capture` logging handler'ı ile log iddiaları yapılır.
- **Yeni test eklerken `main()` içindeki çağrı listesine de ekleyin**; testler otomatik
  keşfedilmez. Testler sıralı ve durum paylaşır (portlar, `SEEN`); bağımsız çalıştırma
  desteklenmez.

Kapsanan başlıca senaryolar: sınıflandırma, paralel tam tarama, örnek rozeti, alt klasör,
provider sırası, önbellek + TTL, olaylar, iptal, taşıma çakışması, provider hatası,
kademeli provider, çoklu provider, AppRun paketi, güncelleme, config hatası, rapor.

## 11. Paketleme / sürüm

- `packaging/build_appimage.sh` AppDir'i `/tmp` içinde kurar; `nzb_checker` + `tkdnd`
  kopyalanır, `__pycache__` ayıklanır. Çıktı depo kökünde `*.AppImage` olur (git'e girmez).
- İki değişken: `AppRun` (sistem `python3` + tkinter arar; yoksa zenity ile uyarır) ve
  `AppRun-bundled` (`usr/python` ağacını `LD_LIBRARY_PATH`/`TCL_LIBRARY`/`TK_LIBRARY` ile kullanır).
- Sürüm artırırken **iki yeri** güncelleyin: `nzb_checker/__init__.py:__version__` ve
  `pyproject.toml:[project].version`. `updater.py` karşılaştırmayı `__version__` üzerinden yapar.
- Güncelleme kontrolü yalnızca bilgilendirir: indirme/değiştirme yok; `APPIMAGE` ortam
  değişkeni çalışan dosyayı bulmak için kullanılır.

## 12. Sık yapılan hatalar / değişmezler

- **Çıktı klasörlerini tarama dışında tutmayı bozma** (`iter_nzb_files`) — sonsuz döngü.
- **Örnek modunu otomatik tam taramaya çevirme** — README ve testler bunu yasaklar.
- **`file_done` alan adlarını** değiştirmeden GUI/test güncellemesi yapma.
- **`found` semantiği**: bir segmentin bulunmuş sayılması için *yanıtlanmış* olması gerekir;
  yalnızca "soruldu" olması yetmez (`answered` ayrı tutulur).
- **İptalde dosya taşıma** — `cancelled` dosyalar asla taşınmaz.
- **Ana thread'de ağ işlemi** — GUI donar; daemon thread + kuyruk kullan.
- **Çalışma dizinine yazma** — AppImage salt okunur; XDG yollarını kullan.
- **Şifreyi loglama/commit etme** — `provider_key` şifreyi dışlar; config `0600`'dır.
- **Yeni bağımlılık** — standart kütüphane dışına çıkma; tkinter yalnızca GUI'de.
- **Python 3.10+ sözdizimi** — 3.9 uyumunu bozar.
- `PARTIAL_THRESHOLD = 0.05` altındaki eksik yüzdesi "tam" gösterilir (yuvarlama gürültüsü).
- `nzb_checker/gui/tkdnd` kaynakta yoktur; yalnızca derleme zamanında oluşur.

## 13. Git hijyeni

Depoya yalnızca kaynak, test, doküman ve gerekli vendor dosyaları girer.
`.gitignore` şunları dışlar (ayrıntı için dosyaya bakın): `__pycache__/`, `*.py[cod]`,
`*.egg-info/`, `build/`, `dist/`, `.venv/`, `venv/`, test/kalite önbellekleri,
`config.json` (gizli bilgi), `*.log`, `last_scan.json`, `.env` dosyaları, `.tmp/`,
`.codegraph/`, editör/OS dosyaları ve `*.AppImage` çıktıları.

- `config.json` **asla** commit edilmez; örnek dosya `config.example.json`'dur.
- `tkdnd/` vendor paketi (`.so`, `.tcl`, `NOTICE.txt`) ve `nzb-checker.png` gereklidir,
  ignore etmeyin.
- AppImage'lar release asset'idir; repoya eklemeyin.
- `.gitignore` zaten izlenen dosyaları etkilemez; yanlışlıkla eklenen dosyayı
  `git rm --cached` ile çıkarın.