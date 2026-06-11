# CLAUDE.md — AgroNext Proje Hafızası

> Bu dosya Claude Code'un projeyi anlaması içindir. Her oturumda otomatik okunur.
> Açıklamalar Türkçe, komutlar ve kod İngilizcedir.

---

## 1. PROJE ÖZETİ

**AgroNext**, orta ölçekli sera işletmeleri için yapay zekâ destekli, düşük maliyetli
bir akıllı sera otomasyon sistemidir. Sensörlerden gelen veriyi GRU tabanlı bir derin
öğrenme modeliyle işleyerek sulama ihtiyacını **önceden** tahmin eder (eşik tabanlı
tepkisel sistemlerden farkı budur).

- **Yarışma:** TEKNOFEST Tarım Teknolojileri Yarışması — Lise Seviyesi
- **Başvuru ID:** 4924555 · **Takım ID:** 913861
- **Aşama:** MVP geliştirme. Sensörler sipariş aşamasında (henüz elde yok).
- **Hedef:** ~2 ay içinde çalışan fiziksel prototip + jüri sunumu.
- **Değer önerisi:** Netafim/Priva gibi sistemlerin ~onda biri fiyatına, Türkiye
  iklimine özel eğitilmiş AI.

---

## 2. TAKIM VE ROLLER

| Kişi | Rol | Sorumluluk |
|------|-----|------------|
| Tuna Özkan Yapıcı | Kaptan / CEO | Koordinasyon, rapor, sunum, iş geliştirme |
| Yusuf Mete | CTO | Yazılım + AI (Python, GRU model, dashboard) |
| Ali Kaan | Donanım Lideri | ESP32, gömülü yazılım, sensör kodu |
| Batuhan Melik Gültekin | Donanım | Montaj, kalibrasyon, test |
| Nil | Tasarım | Dashboard UI, sunum görselleri, poster |
| Doç. Dr. Nurgül Kıtır Şen | Akademik Danışman | Gebze Teknik Ü., veri ortaklığı |

**Deneyim notu:** Yusuf Python biliyor ama ML deneyimi yok. Ali Kaan donanıma yeni
başlıyor. Kod açıklamaları sade ve öğretici olmalı.

---

## 3. SİSTEM MİMARİSİ (4 Katman)

```
[1] SAHA DONANIMI          ESP32 (WiFi Station — "BK_Mdm" ağına bağlanır, DHCP IP alır)
                           + sensörler (BME680, toprak nemi, MQ-135, pH) + TFT + röle
                           + WEB SİTESİ sunar: GET / (sensörler + AI kartı + sohbet)
        │ Wi-Fi: GET /oku → JSON   (plan B: USB serial)
        │        POST /ai ← kopru.py AI sonucunu GERİ GÖNDERİR (site bunu gösterir)
        ▼
[2] VERİ TOPLAMA / KÖPRÜ   kopru.py (Wi-Fi poll → 10-özellik pencere → log + durum.json
                           → öneri+anomali üret → ESP32'ye POST /ai)
                           veri_topla.py (USB serial → CSV, yedek yol)
        │ pencere (12×10)
        ▼
[3] YAPAY ZEKÂ             gru_model.py (eğitim) + tahmin.py + api.py (servis)
                           + asistan.py (sohbet/öneri/anomali) + yeniden_egit.py (kendini eğitme)
        │ HTTP 0.0.0.0:5001  (/tahmin /durum /saglik /chat /geri_bildirim /yeniden_egit)
        ▼
[4] KULLANICI ARAYÜZÜ      ESP32 web sitesi (http://192.168.4.1 — ağdaki HERKES görür,
                           sohbet kutusu doğrudan laptop api.py'ye bağlanır)
                           + agronext_dashboard.html (laptop paneli, canlı/simule mod)
```

> **Not:** Raspberry Pi planı iptal. Gerçek donanım **WiFi Station** modunda çalışır (`BK_Mdm`).
> Laptop poll eder; ESP32 laptopun IP'sini POST /ai isteğinden kendisi öğrenir ve
> web sitesindeki sohbet kutusuna bildirir (sayfa → http://<beyin_ip>:5001/chat).
> **Kendini eğitme döngüsü:** kopru.py canlı veriyi CSV'ye biriktirir →
> yeniden_egit.py (elle, /yeniden_egit endpoint'i veya sohbette "yeniden eğit") modeli
> yeniden eğitir → api.py ve kopru.py yeni checkpoint'i RESTART'SIZ sıcak yükler.

---

## 4. DOSYA YAPISI

| Dosya | Görev | Sorumlu |
|-------|-------|---------|
| `agronext_esp32_firmware.ino` | ESP32 firmware (SoftAP sürümü, demo alternatifi): **SoftAP** kurar (`AgroNext-Demo`), `GET /oku`, `POST /ai`, `GET /ai_durum` | Ali Kaan |
| **`bme680/bme680.ino`** ← **GERÇEK DONANIM FİRMWARE** (Arduino Cloud'da) | **v3.0** — WiFi Station (`BK_Mdm`), TFT ST7735 4 sayfa (Hava/Toprak/Sistem/**AI**), buton nav, `GET /oku` (AgroNext şeması), `GET /data` (geriye uyumluluk), `POST /ai`, `GET /ai_durum`, röle GPIO13 (AI kararına göre). `webpage.h` ile ayrı HTML. Adres: `~/.../bme680/bme680.ino` | Ali Kaan |
| `esp32_sayfa.html` | ESP32 web sitesinin KAYNAĞI (sahte_esp32.py sunar; **bme680/webpage.h** ile senkron tutulmalı). Sensör kartları + AI kartı + öneriler + anomali kıyası + sohbet + 👍/👎 | Nil + Yusuf |
| `sahte_esp32.py` | **ESP32 simülatörü** (port 8032): firmware ile birebir aynı endpoint'ler, gerçekçi kuruma/sulama döngüsü. Donanımsız uçtan uca test + jüri provası | Yusuf |
| `asistan.py` | AI asistan modülü: kural tabanlı Türkçe sohbet (canlı veriye dayalı), agronomik öneri üretimi, eğitim verisi istatistikleriyle anomali kıyası. kopru.py + api.py ortak kullanır | Yusuf |
| `yeniden_egit.py` | **Kendini eğitme:** birikmiş gerçek veriyi (gerekirse simüle tabanla birleştirip) gru_model.egit_ve_kaydet'e verir, eski modeli `yedekler/`e alır, `egitim_kaydi.json` yazar. `--force` test için | Yusuf |
| `veri_topla.py` | ESP32'den USB serial okuyup CSV'ye yazar (plan B) | Yusuf |
| `simule_veri.py` | Sahte ama gerçekçi sera verisi üretir (sensör yokken test için) | Yusuf |
| `gru_model.py` | GRU modelini eğitir, değerlendirir, grafik üretir, modeli kaydeder. `egit_ve_kaydet()` fonksiyonu yeniden_egit.py'den de çağrılır; checkpoint'e eğitim istatistikleri + sürüm yazar | Yusuf |
| `tahmin.py` | Kaydedilmiş modeli yükleyip tek/canlı tahmin yapar | Yusuf |
| `kopru.py` | **Wi-Fi köprüsü:** ESP32 `/oku`'yu poll eder → 10-özellikli pencere → inference → öneri+anomali → `durum.json` + CSV log + **ESP32'ye POST /ai**. ESP32 yoksa simüle fallback; yeni checkpoint'i sıcak yükler. Adres: `AGRONEXT_ESP32` env | Yusuf |
| `api.py` | Flask **AI beyni** (0.0.0.0:5001): `/tahmin`, `/durum`, `/saglik`, `/chat` (sohbet), `/geri_bildirim` (👍/👎), `/yeniden_egit` (arka planda eğitim). Checkpoint değişince sıcak yükler. `AGRONEXT_MIN_SATIR` env: eğitim için min gerçek veri | Yusuf |
| `agronext_dashboard.html` | Canlı sera paneli (laptop): 8 sensör kartı, `/durum`'dan beslenir (fallback `/tahmin`), mod badge, demo butonu | Nil + Yusuf |
| `index.html` | `localhost:8000` → dashboard'a yönlendirme (yerel sunucu kolaylığı) | — |
| `agronext_gru_model.pt` | Eğitilmiş model + scaler + eşik + **eğitim istatistikleri (anomali kıyası için)** + sürüm (gru_model.py yazar; tahmin.py, api.py & kopru.py okur) | — |
| `simule_sera_verileri.csv` | Örnek/test verisi (simule_veri.py üretir; kopru.py fallback + yeniden_egit.py taban kaynağı) | — |
| `gercek_sera_verileri.csv` | **Canlı/gerçek log** (kopru.py her ölçümü 10 kolonla yazar — yeniden eğitim bundan beslenir) | — |
| `durum.json` | kopru.py'nin yazdığı son durum (api.py `/durum` okur) — geçici, .gitignore'a uygun | — |
| `egitim_kaydi.json`, `sohbet_gecmisi.csv`, `geri_bildirim.csv` | Kendini eğitme sonucu / sohbet logu / ziyaretçi değerlendirmeleri (api.py & yeniden_egit.py yazar) — geçici | — |
| `yedekler/` | Her yeniden eğitimden önceki model checkpoint'leri (geri dönüş yolu) | — |

### 4.B `agronext-ai/` — Spec'e dayalı TensorFlow/Keras pipeline (AYRI)

`AGRONEXT_GRU_BUILD_1.md` spesifikasyonuna göre kurulmuş, yukarıdaki PyTorch
prototipinden **bağımsız** ikinci bir pipeline. Fark: **regresyon** (gelecek 24
saatlik nem eğrisini tahmin eder, ikili sulama olayı değil), **TensorFlow/Keras**,
modüler `src/` yapısı, sentetik veri üreteci + persistence baseline + FAO-56
sulama mantığı + su tasarrufu simülasyonu.

- **Ortam:** `agronext-ai/.venv` (Python 3.12 — TF, sistemdeki Python 3.14'ü desteklemiyor).
- **Çalıştır:** `cd agronext-ai && .venv/bin/python src/{data_generation,train,evaluate,irrigation_logic}.py`
- **Parametreler:** `agronext-ai/config.yaml` (TODO'lar danışman teyidi bekliyor).
- **Sentetik sonuç:** GRU test MAE ~2.3 vs persistence ~8.5 (%73 iyileşme); su tasarrufu sim. ~%32.
- Detay: `agronext-ai/README.md`.

---

## 5. KURULUM VE KOMUTLAR

### Ortam (ÖNEMLİ — PyTorch için ayrı venv)
Sistemdeki `python` = 3.14, PyTorch'u desteklemiyor. PyTorch prototipi kendi
`.venv`'inden (Python 3.12) çalışır. (TensorFlow `agronext-ai/.venv`'i AYRIDIR.)
```bash
/Users/alikaankaya/.local/bin/python3.12 -m venv .venv     # tek seferlik
.venv/bin/pip install torch numpy pandas scikit-learn matplotlib flask flask-cors pyserial requests
```

### Çalıştırma sırası (sensörler YOKKEN — simüle veriyle)
```bash
.venv/bin/python simule_veri.py    # 1. örnek veri üret → simule_sera_verileri.csv
.venv/bin/python gru_model.py      # 2. modeli eğit → agronext_gru_model.pt + grafikler
.venv/bin/python tahmin.py         # 3. tek tahmin testi
.venv/bin/python api.py            # 4. API başlat (http://localhost:5001 — macOS'ta 5000 AirPlay'de)
# 5. agronext_dashboard.html'i tarayıcıda aç → "● GRU bağlı" görünmeli
#    (yerel sunucu gerekirse: .venv/bin/python -m http.server 8000)
```

### Donanımsız TAM demo (sahte ESP32 — site + sohbet + kendini eğitme)
```bash
# 3 ayrı terminal (sıra önemli değil; her parça diğerini otomatik bulur):
.venv_new/bin/python sahte_esp32.py                              # A: ESP32 simülatörü (port 8032)
AGRONEXT_ESP32=http://127.0.0.1:8032 .venv_new/bin/python kopru.py   # B: köprü → sahte ESP32
AGRONEXT_MIN_SATIR=60 .venv_new/bin/python api.py                # C: AI beyni (5001; min veri eşiği demo için düşük)
# Tarayıcı: http://127.0.0.1:8032 → AgroNext sitesi (sensörler + AI + sohbet)
# Sohbete "yeniden eğit" yaz → ~60 ölçüm biriktiyse model kendini eğitir,
# api.py + kopru.py yeni modeli RESTART'SIZ alır (sayfadaki sürüm değişir).
```

### Wi-Fi canlı demo (ESP32 SoftAP — jüri için)
```bash
# 1. Arduino IDE'den agronext_esp32_firmware.ino'yu ESP32'ye yükle (DEMO_MOD=true)
# 2. Laptopu ESP32'nin "AgroNext-Demo" Wi-Fi ağına bağla (şifre: agronext2025)
.venv_new/bin/python api.py        # terminal A: AI beyni (/tahmin /durum /chat ...)
.venv_new/bin/python kopru.py      # terminal B: ESP32 /oku poll → tahmin → ESP32'ye POST /ai
# 3. Ağdaki HERHANGİ bir cihazdan (telefon dahil) http://192.168.4.1 aç:
#    AgroNext web sitesi — canlı sensörler, AI kartı, öneriler, sohbet, 👍/👎.
#    Laptop dashboard'u (agronext_dashboard.html) da ayrıca çalışır.
#    ESP32 yoksa kopru.py simüle CSV'den besler → "○ SİMÜLE MOD"
#    Donanımsız demo: dashboard'da sağ alttaki "🎬 Demo Senaryosu" butonu
```

### Çalıştırma (sensörler GELİNCE — gerçek veriyle, USB plan B)
```bash
# Arduino IDE'den agronext_esp32_firmware.ino'yu ESP32'ye yükle
.venv_new/bin/python veri_topla.py                  # PORT değişkenini ayarla (COM3 / /dev/tty...)
.venv_new/bin/python gru_model.py sera_verileri.csv # gerçek veriyle yeniden eğit
.venv_new/bin/python tahmin.py sera_verileri.csv    # gerçek veriyle tahmin
.venv_new/bin/python api.py                         # dashboard'a servis et
# Not: kopru.py canlı çalışırken gercek_sera_verileri.csv'yi otomatik biriktirir →
#      yeterince veri olunca gru_model.py'ye bu dosyayı vererek yeniden eğit.
```

---

## 6. KRİTİK TEKNİK KARARLAR (DEĞİŞTİRİRKEN DİKKAT)

### JSON Veri Şeması — EN KRİTİK BAĞIMLILIK
ESP32 firmware ve Python tarafı AYNI formatı kullanmalı. Değişirse her şey kırılır.
**Ali Kaan firmware'e uygulamalı:**
```json
{"temp": 25.5, "humidity": 65.0, "pressure": 1013.2, "voc": 25000, "soil_raw": 1800, "soil_pct": 62.5, "co2": 420, "lux": 15000, "ph": 6.4, "pump": 0}
```
`ph` opsiyoneldir; sensör yoksa `null` veya `0` gönderilebilir — Python tarafı NaN olarak işler.

### Köprü Türetim Mantığı (kopru.py) — ESP32 JSON ≠ Model Girdisi
ESP32 **7 ham sensör** alanı gönderir; model **10 özellik** ister. Aradaki 3
özellik ESP32'den GELMEZ, `kopru.py` türetir (gru_model.py / tahmin.py ile AYNI formül):
- `hour_sin = sin(2π·saat/24)`, `hour_cos = cos(2π·saat/24)` — anlık zamandan
  (canlı: `datetime.now()`; simüle fallback: CSV satırının timestamp'i).
- `vpd` (buhar basıncı açığı): `es=0.6108·exp(17.27·T/(T+237.3))`, `ea=es·(nem/100)`, `vpd=max(0, es−ea)`.
- `ph_imputed = ph` (0/yok/NaN ise **6.5**) — modelin 10. özelliği `vpd` olduğu için
  ph_imputed şu an modele GİTMEZ, sadece `gercek_sera_verileri.csv`'ye loglanır.
- Pencere, checkpoint'teki `OZELLIKLER` **sırasına göre** kurulur → model `vpd` isterse vpd,
  `ph_imputed` isterse onu alır (iki şema da kırılmaz).

### API Endpoint'leri (api.py — 0.0.0.0:5001, "AI beyni")
- `POST /tahmin` → `{pencere:[[10 özellik]×12]}` ver, `{olasilik, karar, mesaj}` al. (Dashboard fallback)
- `GET /durum` → kopru.py'nin son durumu: `{son_olcum, olasilik, karar, mesaj, mod, oneriler, anomaliler, model_surum, timestamp}`.
  - `mod`: `"canli"` (ESP32 bağlı) / `"simule"` (fallback) / `"yok"` (kopru.py kapalı → dashboard kendi simülasyonuna düşer).
- `GET /saglik` → `{durum, mod, model_version, egitim_zamani, pencere, ozellik_sayisi, sensors, egitim_durumu, gercek_veri}`.
- `POST /chat` → `{soru}` ver, `{cevap, niyet}` al. Kural tabanlı asistan (asistan.py), cevaplar canlı durumdan üretilir; `sohbet_gecmisi.csv`'ye loglanır. "yeniden eğit" niyeti eğitimi tetikler.
- `POST /geri_bildirim` → `{tip: "dogru"|"yanlis", yorum?}` — ziyaretçi değerlendirmesi `geri_bildirim.csv`'ye. `GET` → sayılar.
- `POST /yeniden_egit` → arka planda yeniden_egit.py başlatır (`{"force":true}` veri eşiğini atlar). `GET` → eğitim durumu.

### ESP32 HTTP Endpoint'leri (192.168.4.1 — firmware)
- `GET /` → AgroNext web sitesi (PROGMEM'de gömülü; kaynağı `esp32_sayfa.html`).
- `GET /oku` → sensör JSON'u (yukarıdaki şema). kopru.py poll eder.
- `POST /ai` → kopru.py AI özetini buraya gönderir (≤2 KB). ESP32 gönderenin IP'sini `beyin_ip` olarak saklar.
- `GET /ai_durum` → `{ai: <son AI özeti|null>, beyin_ip, yas_sn}`. Web sitesi 3 sn'de bir okur; sohbet kutusu `http://<beyin_ip>:5001/chat`'e bağlanır.
- **Senkron kuralı:** `esp32_sayfa.html` değişirse firmware'deki `SAYFA_HTML` PROGMEM kopyası da güncellenmeli (ve tersi).

### ESP32 Pin Tanımları
- **BME680** (sıcaklık, nem, basınç, VOC): I2C — SDA=`GPIO21`, SCL=`GPIO22`
- Kapasitif toprak nemi: `GPIO36` (analog, input-only)
- MQ-135 (CO₂): `GPIO34` (analog, input-only)
- **BH1750** (ışık yoğunluğu, lux): I2C — SDA=`GPIO21`, SCL=`GPIO22` (adres 0x23)
- pH sensörü: `GPIO35` (analog, input-only) — **OPSİYONEL**, kalibrasyon zor
- Röle (sulama): `GPIO23` — **AKTİF LOW** (LOW=açık, HIGH=kapalı)

### Toprak Nemi Kalibrasyonu (Batuhan güncelleyecek)
- `SOIL_DRY = 3200` (kuru toprak ham değeri)
- `SOIL_WET = 1200` (ıslak toprak ham değeri)
- Gerçek sensörle bu iki değer ölçülüp firmware'de güncellenmeli.

### GRU Model Parametreleri (gru_model.py / tahmin.py / api.py'de AYNI olmalı)
- `PENCERE = 12` (son 12 ölçüm = 1 saat girdi)
- `UFUK = 6` (30 dk sonrası tahmin)
- `OZELLIKLER = ["temp","humidity","soil_pct","co2","pressure","voc","lux","hour_sin","hour_cos","vpd"]`
  (10 özellik; `hour_sin/cos` ve `vpd` CSV'den değil, kod içinde türetilir)
- `HIDDEN = 32`, `LAYERS = 2`
- `ESIK = 0.40` (Recall > Precision: kaçırılan sulama kötüdür)
- Hedef: "gelecek 30 dk içinde sulama olayı (pump=1) olacak mı?" (binary)
- Model parametreleri, scaler, threshold ve sensör listesi `agronext_gru_model.pt`'ye kaydedilir.
- `api.py` threshold'u checkpoint'ten okur (hardcode değil).

---

## 7. MEVCUT DURUM

**Çalışan / tamamlanan:**
- Tüm kod iskeleti yazıldı ve simüle veriyle uçtan uca çalışıyor.
- GRU modeli simüle veride ~%94-99 doğruluk, F1 ~%85-92 (her eğitimde değişir).
- API köprüsü test edildi: kuru toprak → ~%100, nemli toprak → ~%0 sulama olasılığı.
- Dashboard API'ye bağlı, canlı çalışıyor, fallback mekanizması var.
- **ESP32 web sitesi** (firmware'de gömülü) + **AI sohbet asistanı** + **öneri/anomali motoru**
  + **kendini eğitme döngüsü** (sohbetten tetiklenebilir, sıcak model yükleme) —
  sahte_esp32.py ile uçtan uca test edildi (2026-06-10). Gerçek ESP32'de doğrulanacak.
- Web sitesi mevcut (agronext.net).

**Eksik / yapılmamış:**
- Fiziksel donanım (sensörler sipariş aşamasında).
- Gerçek veri toplama (sensör gelince).
- Mobil uygulama (sadece web dashboard var).
- Demo videosu, sunum slaytları, poster.

---

## 8. YAPILACAKLAR (Sıradaki Adımlar)

1. **Sensörleri sipariş et** (alınacaklar listesi hazır, ~2000 TL zorunlu kalem).
2. **Ali Kaan:** Arduino IDE kur → firmware'i ESP32'ye yükle → her sensörü test et.
3. **Batuhan:** Donanımı monte et → multimetreyle test → toprak sensörünü kalibre et.
4. **Yusuf:** `veri_topla.py` ile gerçek veri topla → modeli gerçek veriyle yeniden eğit.
5. **Nil:** Dashboard tasarımını geliştir → sunum görsellerini hazırla.
6. **Tuna:** Demo videosu + sunum + rapor.
7. **Model iyileştirme:** Gerçek veri biriktikçe doğruluğu artır, görselleri yenile.

---

## 9. ÖNEMLİ UYARILAR

- **Simüle veri uyarısı:** Mevcut model SİMÜLE veriyle eğitildi. Sonuçlar gerçek
  değil — sensörler gelince gerçek veriyle yeniden eğitilmeli. Sunumda bu dürüstçe
  belirtilmeli ("sahada gerçek veriyle kalibre ediliyor").
- **Güneş paneli:** Opsiyonel, sona bırakıldı. Sunumda "tasarımda mevcut" denebilir.
- **pH sensörü:** Alınmadı (pahalı + kalibrasyonu zor). "Pilot aşamada eklenecek".
- **Pos_weight:** GRU'da dengesiz veri için ham oran (neg/pos, sqrt'siz) kullanılır — daha agresif ama recall öncelikli (kaçırılan sulama = zarar).
- **DHT22 KULLANILMIYOR:** Önceki notlarda DHT22 geçiyordu. Gerçek donanım **BME680** (I2C) kullanıyor — çok daha stabil + basınç + VOC bonus. Tüm DHT22 referanslarını BME680 olarak oku.

---

## 10. KOD STİLİ TERCİHLERİ

- Açıklamalar ve değişken isimleri Türkçe olabilir (ekip Türkçe çalışıyor).
- Kod sade ve öğretici olsun — ekipte ML/donanım yeni öğreniliyor.
- Yorum satırlarıyla "neden" açıklansın, sadece "ne" değil.
- Yeni dosya eklerken bu CLAUDE.md'deki dosya tablosunu güncelle.
