"""
AgroNext - ESP32 Wi-Fi Canlı Veri Köprüsü (kopru.py)
=====================================================
Bu script, sahadaki ESP32 ile laptoptaki GRU modelini BİRBİRİNE BAĞLAR.

NE YAPAR:
  1. ESP32'nin SoftAP'ına bağlı laptopta çalışır.
  2. http://192.168.4.1/oku adresini OLCUM_ARALIGI saniyede bir poll eder.
  3. Gelen ham ölçümden 10 ÖZELLİKLİ pencere kurar (model ne ile eğitildiyse
     o sırayla - hour_sin/cos ve vpd burada TÜRETİLİR, ESP32'den gelmez).
  4. Pencere dolunca GRU modeliyle inference yapar (sulama olasılığı).
  5. Sonucu durum.json'a yazar  -> api.py /durum bunu okuyup dashboard'a verir.
  6. Her ölçümü gercek_sera_verileri.csv'ye loglar (gerçek veri seti büyüsün).

DAYANIKLILIK (demo asla çökmesin):
  - ESP32'ye ulaşılamazsa simule_sera_verileri.csv'den besler ("simule" mod).
  - Bağlantı geri gelince otomatik "canli" moda döner.

ÇALIŞTIRMA:
    .venv_new/bin/python kopru.py
    (api.py'yi de ayrı terminalde çalıştır: .venv_new/bin/python api.py)

NOT: api.py çalışmasa bile kopru.py kendi başına model yükler ve durum.json
     yazar. api.py sadece bu dosyayı okuyup dashboard'a servis eder.
"""

import os
import csv
import json
import math
import time
import tempfile
from collections import deque
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

import asistan   # öneri + anomali üretimi (api.py ile ortak modül)

try:
    import requests
except ImportError:
    requests = None  # ESP32 yoksa zaten simule moda düşeceğiz

# ============================================================
# AYARLAR  <-- demo/gerçek arası buradan değiştirilir
# ============================================================
# ESP32 adresi env ile değiştirilebilir (test: sahte_esp32.py kullanır):
#   AGRONEXT_ESP32=http://127.0.0.1:8032 .venv_new/bin/python kopru.py
ESP32_TABAN   = os.environ.get("AGRONEXT_ESP32", "http://192.168.4.1").rstrip("/")
ESP32_URL     = ESP32_TABAN + "/oku"       # sensör verisi buradan okunur
ESP32_AI_URL  = ESP32_TABAN + "/ai"        # AI sonucu buraya POST edilir
OLCUM_ARALIGI = 3                          # saniye (demo: 3, gerçek: 300)
ZAMAN_ASIMI   = 2.0                        # ESP32 poll timeout (saniye)
BEYIN_PORT    = 5001                       # api.py portu (sohbet için sayfaya bildirilir)

MODEL_DOSYA   = "agronext_gru_model.pt"
DURUM_DOSYA   = "durum.json"                 # api.py bunu okur
LOG_CSV       = "gercek_sera_verileri.csv"   # gerçek/canlı veri logu
SIMULE_CSV    = "simule_sera_verileri.csv"   # fallback kaynağı

# CSV log kolonları (simule_sera_verileri.csv ile AYNI şema)
CSV_KOLONLAR = ["timestamp", "temp", "humidity", "pressure", "voc",
                "soil_raw", "soil_pct", "co2", "lux", "ph", "pump"]


# ============================================================
# Model tanımı (api.py / gru_model.py ile AYNI mimari)
# ============================================================
class GRUNet(nn.Module):
    def __init__(self, n_features, hidden, layers):
        super().__init__()
        self.gru = nn.GRU(n_features, hidden, layers,
                          batch_first=True, dropout=0.2 if layers > 1 else 0)
        self.fc = nn.Sequential(nn.Linear(hidden, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x):
        out, _ = self.gru(x)
        return self.fc(out[:, -1, :]).squeeze(-1)


# ---- Model yükleme (SICAK YÜKLEME destekli) ----
# Model global değişkenlerde tutulur; her döngüde dosyanın mtime'ı kontrol
# edilir. yeniden_egit.py yeni checkpoint yazınca kopru.py RESTART GEREKMEDEN
# yeni modele geçer — "kendini eğitme" döngüsünün son halkası budur.
model = None
OZELLIKLER = []
PENCERE = 12
THRESHOLD = 0.4
MEAN = SCALE = None
ISTATISTIKLER = {}
MODEL_SURUM = "?"
_model_mtime = 0.0


def modeli_yukle():
    """Checkpoint'i oku ve global model durumunu güncelle."""
    global model, OZELLIKLER, PENCERE, THRESHOLD, MEAN, SCALE
    global ISTATISTIKLER, MODEL_SURUM, _model_mtime

    ckpt = torch.load(MODEL_DOSYA, map_location="cpu", weights_only=False)
    OZELLIKLER = ckpt["ozellikler"]      # checkpoint'teki GERÇEK sıra (vpd dahil)
    PENCERE    = ckpt["pencere"]
    THRESHOLD  = float(ckpt.get("threshold", 0.4))
    MEAN       = np.array(ckpt["scaler_mean"],  dtype=np.float32)
    SCALE      = np.array(ckpt["scaler_scale"], dtype=np.float32)
    ISTATISTIKLER = ckpt.get("istatistikler", {})   # eski checkpoint'te yok → boş
    MODEL_SURUM   = ckpt.get("model_surum", "GRU-eski")

    model = GRUNet(len(OZELLIKLER), ckpt["hidden"], ckpt["layers"])
    model.load_state_dict(ckpt["model"])
    model.eval()
    _model_mtime = os.path.getmtime(MODEL_DOSYA)
    print(f"Model hazir. Surum={MODEL_SURUM}, pencere={PENCERE}, esik={THRESHOLD}")


def model_degistiyse_yenile():
    """yeniden_egit.py yeni model yazdıysa onu yükle (sıcak yükleme)."""
    try:
        if os.path.getmtime(MODEL_DOSYA) > _model_mtime:
            print("[model] Yeni checkpoint bulundu, sicak yukleniyor...")
            modeli_yukle()
            return True
    except OSError:
        pass  # dosya o an yazılıyor olabilir; sonraki döngüde tekrar denenir
    return False


print("Model yukleniyor...")
modeli_yukle()


# ============================================================
# Özellik türetme
# ============================================================
def vpd_hesapla(temp, hum):
    """Buhar basıncı açığı (VPD). Yüksek VPD = bitki daha çok su kaybeder."""
    es = 0.6108 * math.exp(17.27 * temp / (temp + 237.3))  # doygun buhar basıncı
    ea = es * (hum / 100.0)                                  # gerçek buhar basıncı
    return max(0.0, es - ea)


def ozellik_sozlugu(olcum, zaman):
    """Ham ölçümden + zamandan TÜM olası özellikleri hesapla.

    olcum: ESP32'den veya simule CSV'den gelen ham değerler (dict).
    zaman: datetime - hour_sin/cos bundan türetilir.

    Döndürür: özellik adı -> değer. Pencere bu sözlükten OZELLIKLER
    sırasına göre seçilerek kurulur (model hangi 10'u istiyorsa o gider).
    """
    saat = zaman.hour + zaman.minute / 60.0
    temp = float(olcum.get("temp", 24.0))
    hum  = float(olcum.get("humidity", 60.0))

    # pH yoksa / 0 / NaN ise 6.5 varsayılan (sensör opsiyonel)
    ph_ham = olcum.get("ph", None)
    try:
        ph_ham = float(ph_ham)
    except (TypeError, ValueError):
        ph_ham = None
    ph_imputed = ph_ham if (ph_ham is not None and ph_ham > 0) else 6.5

    return {
        "temp":     temp,
        "humidity": hum,
        "soil_pct": float(olcum.get("soil_pct", 50.0)),
        "co2":      float(olcum.get("co2", 420.0)),
        "pressure": float(olcum.get("pressure", 1013.0)),
        "voc":      float(olcum.get("voc", 25000.0)),
        "lux":      float(olcum.get("lux", 0.0)),
        "hour_sin": math.sin(2 * math.pi * saat / 24.0),   # TÜRETİLİR
        "hour_cos": math.cos(2 * math.pi * saat / 24.0),   # TÜRETİLİR
        "vpd":      vpd_hesapla(temp, hum),                # TÜRETİLİR
        "ph_imputed": ph_imputed,                          # log + olası 10. özellik
        "ph":       ph_imputed,
    }


def pencere_satiri(ozellikler):
    """Özellik sözlüğünden modelin istediği sırada satır vektörü kur.

    OZELLIKLER checkpoint'ten gelir. Böylece model 'vpd' isterse vpd,
    'ph_imputed' isterse onu alır - iki şema da kırılmaz.
    """
    return [float(ozellikler[ad]) for ad in OZELLIKLER]


# ============================================================
# Inference
# ============================================================
def tahmin_yap(pencere_list):
    """deque'deki PENCERE satırından sulama olasılığını hesapla."""
    pencere = np.array(pencere_list, dtype=np.float32)
    # Pencere henüz dolmadıysa ilk satırı tekrarlayarak doldur (demo başı)
    if len(pencere) < PENCERE:
        eksik = PENCERE - len(pencere)
        pencere = np.vstack([np.repeat(pencere[:1], eksik, axis=0), pencere])
    pencere = pencere[-PENCERE:]

    norm = (pencere - MEAN) / SCALE
    x = torch.tensor(norm, dtype=torch.float32).unsqueeze(0)
    with torch.no_grad():
        olasilik = float(torch.sigmoid(model(x)).item())

    if olasilik > THRESHOLD:
        karar = "SULAMA GEREKLI"
        mesaj = (f"Toprak nemi dususte. 30 dk icinde sulama gerekecek "
                 f"(olasilik %{olasilik*100:.0f}, esik={THRESHOLD}).")
    else:
        karar = "Sulama gerekmiyor"
        mesaj = (f"Sera kosullari dengede. 30 dk icin sulama gerekmiyor "
                 f"(olasilik %{olasilik*100:.0f}, esik={THRESHOLD}).")
    return olasilik, karar, mesaj


# ============================================================
# Durum paylaşımı + CSV log
# ============================================================
def durum_yaz(son_olcum, olasilik, karar, mesaj, mod, oneriler, anomaliler):
    """Son durumu durum.json'a ATOMİK yaz (api.py /durum bunu okur)."""
    durum = {
        "son_olcum": son_olcum,
        "olasilik":  round(olasilik, 4),
        "karar":     karar,
        "mesaj":     mesaj,
        "mod":       mod,                 # "canli" veya "simule"
        "oneriler":  oneriler,            # asistan.py'nin ürettiği öneri listesi
        "anomaliler": anomaliler,         # eğitim verisiyle kıyas uyarıları
        "model_surum": MODEL_SURUM,
        "beyin_port": BEYIN_PORT,         # ESP32 sayfası sohbet için kullanır
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # Önce geçici dosyaya yaz, sonra rename -> api.py yarım dosya okumaz.
    fd, gecici = tempfile.mkstemp(dir=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(durum, f, ensure_ascii=False)
    os.replace(gecici, DURUM_DOSYA)
    return durum


def esp32_ai_gonder(durum):
    """AI sonucunu ESP32'ye POST et — ESP32 web sitesi bunu gösterir.

    ESP32 RAM'i sınırlı olduğu için yalnız sayfanın ihtiyacı olan KOMPAKT
    bir özet gönderilir (tam durum değil). ESP32, POST'u yapan laptopun
    IP'sini kendisi görür (remoteIP) ve sayfaya 'beyin_ip' olarak verir —
    böylece sayfadaki sohbet kutusu laptoptaki api.py'ye doğrudan bağlanır.
    """
    if requests is None:
        return False
    ozet = {
        "olasilik":    durum["olasilik"],
        "karar":       durum["karar"],
        "mesaj":       durum["mesaj"],
        "oneriler":    [{"baslik": o["baslik"], "detay": o["detay"],
                         "seviye": o["seviye"]} for o in durum["oneriler"][:4]],
        "anomaliler":  durum["anomaliler"][:3],
        "model_surum": durum["model_surum"],
        "beyin_port":  durum["beyin_port"],
        "zaman":       durum["timestamp"],
    }
    try:
        r = requests.post(ESP32_AI_URL, json=ozet, timeout=ZAMAN_ASIMI)
        return r.status_code == 200
    except Exception:
        return False  # ESP32'ye ulaşılamadı; sayfa son bilineni gösterir


def csv_logla(olcum, zaman):
    """Her ölçümü gercek_sera_verileri.csv'ye ekle (model yeniden eğitimi için)."""
    yeni = not os.path.exists(LOG_CSV)
    satir = {
        "timestamp": zaman.strftime("%Y-%m-%d %H:%M:%S"),
        "temp":      olcum.get("temp", ""),
        "humidity":  olcum.get("humidity", ""),
        "pressure":  olcum.get("pressure", ""),
        "voc":       olcum.get("voc", ""),
        "soil_raw":  olcum.get("soil_raw", ""),
        "soil_pct":  olcum.get("soil_pct", ""),
        "co2":       olcum.get("co2", ""),
        "lux":       olcum.get("lux", ""),
        "ph":        olcum.get("ph", ""),
        "pump":      olcum.get("pump", 0),
    }
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_KOLONLAR)
        if yeni:
            w.writeheader()
        w.writerow(satir)


# ============================================================
# Veri kaynakları: ESP32 (canlı) + simule CSV (fallback)
# ============================================================
def esp32_oku():
    """ESP32'den güncel ölçümü çek. Ulaşılamazsa None döndür."""
    if requests is None:
        return None
    try:
        r = requests.get(ESP32_URL, timeout=ZAMAN_ASIMI)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def simule_kaynak():
    """simule_sera_verileri.csv'yi sonsuz döngüde okuyan üreteç (fallback)."""
    while True:
        if not os.path.exists(SIMULE_CSV):
            # Simule CSV de yoksa minimum sahte veri üret (yine de çökme).
            yield {"temp": 24.0, "humidity": 60.0, "pressure": 1013.0,
                   "voc": 25000, "soil_raw": 2000, "soil_pct": 45.0,
                   "co2": 420, "lux": 0, "ph": 6.5, "pump": 0}, None
            continue
        with open(SIMULE_CSV, newline="", encoding="utf-8") as f:
            for satir in csv.DictReader(f):
                olcum = {}
                for k, v in satir.items():
                    if k == "timestamp":
                        olcum["timestamp"] = v
                        continue
                    try:
                        olcum[k] = float(v)
                    except (TypeError, ValueError):
                        olcum[k] = v
                # CSV timestamp'ini hour türetimi için sakla
                ts = None
                try:
                    ts = datetime.fromisoformat(satir["timestamp"])
                except Exception:
                    ts = None
                yield olcum, ts


# ============================================================
# Ana döngü
# ============================================================
def main():
    print("\nKopru basladi. ESP32 araniyor:", ESP32_URL)
    print("Cikis: Ctrl+C\n")

    pencere = deque(maxlen=PENCERE)
    sim_gen = simule_kaynak()
    onceki_mod = None

    while True:
        # Yeniden eğitim olduysa yeni modeli al (pencere boyutu değişebilir)
        if model_degistiyse_yenile():
            pencere = deque(pencere, maxlen=PENCERE)

        ham = esp32_oku()

        if ham is not None:
            mod = "canli"
            zaman = datetime.now()                # canlı: şimdiki zaman
            olcum = ham
        else:
            mod = "simule"
            olcum, sim_ts = next(sim_gen)          # fallback: simule CSV satırı
            zaman = sim_ts or datetime.now()       # varsa CSV zamanı, yoksa şimdi

        if mod != onceki_mod:
            isaret = "● CANLI" if mod == "canli" else "○ SIMULE"
            print(f"[mod] {isaret}")
            onceki_mod = mod

        # Özellikleri türet, pencereyi büyüt
        ozk = ozellik_sozlugu(olcum, zaman)
        pencere.append(pencere_satiri(ozk))

        # Inference
        olasilik, karar, mesaj = tahmin_yap(list(pencere))

        # Dashboard'a gönderilecek "son ölçüm" (ham + türetilen ph_imputed)
        son_olcum = {
            "temp":     round(float(olcum.get("temp", 0)), 1),
            "humidity": round(float(olcum.get("humidity", 0)), 1),
            "pressure": round(float(olcum.get("pressure", 0)), 1),
            "voc":      int(float(olcum.get("voc", 0))),
            "soil_pct": round(float(olcum.get("soil_pct", 0)), 1),
            "co2":      int(float(olcum.get("co2", 0))),
            "lux":      int(float(olcum.get("lux", 0))),
            "ph":       round(ozk["ph_imputed"], 2),
            "pump":     int(float(olcum.get("pump", 0))),
        }

        # Eğitim verisiyle KIYAS: canlı değerler modelin bildiği aralıkta mı?
        anomaliler = asistan.anomali_bul(ozk, ISTATISTIKLER)

        # Agronomik öneriler (GRU tahmini + kural tabanlı uzman sistem)
        oneriler = asistan.oneri_uret(olcum, olasilik, karar,
                                      ozk["vpd"], anomaliler, THRESHOLD)

        # Durumu paylaş + logla + ESP32 web sitesine gönder
        durum = durum_yaz(son_olcum, olasilik, karar, mesaj, mod,
                          oneriler, anomaliler)
        csv_logla(olcum, zaman)
        ai_gitti = esp32_ai_gonder(durum) if mod == "canli" else False

        print(f"[{mod}] soil={son_olcum['soil_pct']:5.1f}% "
              f"olasilik=%{olasilik*100:5.1f} -> {karar}"
              f"{'  [ESP32 sayfasina gonderildi]' if ai_gitti else ''}"
              f"{'  [!' + str(len(anomaliler)) + ' anomali]' if anomaliler else ''}")

        time.sleep(OLCUM_ARALIGI)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nKopru durduruldu.")
