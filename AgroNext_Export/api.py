"""
AgroNext - GRU API + Yapay Zekâ Beyni (api.py)
===============================================
Eğitilmiş GRU modelini ve sohbet asistanını web servisi olarak sunar.
Laptop "AI beyni"dir; ESP32'nin web sitesindeki sohbet kutusu ve dashboard
buraya bağlanır.

ENDPOINT'LER (http://<laptop-ip>:5001):
  POST /tahmin        pencere ver → sulama olasılığı al (dashboard fallback)
  GET  /durum         kopru.py'nin son durumu (dashboard + ESP32 sayfası)
  GET  /saglik        model + sistem sağlık bilgisi
  POST /chat          {"soru": "..."} → {"cevap": "...", "niyet": "..."}
  POST /geri_bildirim ziyaretçi 👍/👎 değerlendirmesi → CSV'ye loglanır
  GET  /geri_bildirim değerlendirme sayıları
  POST /yeniden_egit  birikmiş gerçek veriyle modeli arka planda eğit
  GET  /yeniden_egit  eğitim durumu (bos / egitiliyor / son sonuç)

SICAK YÜKLEME: yeniden_egit.py yeni checkpoint yazınca bu servis RESTART
GEREKMEDEN yeni modeli kullanır (her istekte dosya mtime kontrolü).

KURULUM:  pip install flask flask-cors torch numpy
ÇALIŞTIRMA:
    .venv_new/bin/python api.py
    -> http://0.0.0.0:5001 (macOS'ta 5000'i AirPlay tutar)
"""

import os
import sys
import csv
import json
import subprocess
import threading
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
import torch
import torch.nn as nn
import numpy as np

import asistan

MODEL_DOSYA  = "agronext_gru_model.pt"
DURUM_DOSYA  = "durum.json"            # kopru.py'nin yazdığı son durum
SOHBET_CSV   = "sohbet_gecmisi.csv"    # ziyaretçi-AI konuşma logu
BILDIRIM_CSV = "geri_bildirim.csv"     # ziyaretçi 👍/👎 değerlendirmeleri
EGITIM_KAYIT = "egitim_kaydi.json"     # yeniden_egit.py'nin yazdığı sonuç

# Yeniden eğitim için gereken minimum GERÇEK ölçüm sayısı.
# Demo'da hızlı göstermek için: AGRONEXT_MIN_SATIR=60 ile başlatın.
MIN_SATIR = int(os.environ.get("AGRONEXT_MIN_SATIR", "360"))


class GRUNet(nn.Module):
    def __init__(self, n_features, hidden, layers):
        super().__init__()
        self.gru = nn.GRU(n_features, hidden, layers,
                          batch_first=True, dropout=0.2 if layers > 1 else 0)
        self.fc = nn.Sequential(nn.Linear(hidden, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x):
        out, _ = self.gru(x)
        return self.fc(out[:, -1, :]).squeeze(-1)


# ============================================================
# Model yönetimi — SICAK YÜKLEME destekli
# ============================================================
class ModelYonetici:
    """Checkpoint'i yükler; dosya değişince otomatik yeniler.

    Neden sınıf? Eğitim sonrası modelin restart'sız devreye girmesi için
    tüm model durumu tek yerde tutulur ve atomik değiştirilir.
    """

    def __init__(self, dosya):
        self.dosya = dosya
        self._kilit = threading.Lock()
        self._mtime = 0.0
        self.yukle()

    def yukle(self):
        ckpt = torch.load(self.dosya, map_location="cpu", weights_only=False)
        model = GRUNet(len(ckpt["ozellikler"]), ckpt["hidden"], ckpt["layers"])
        model.load_state_dict(ckpt["model"])
        model.eval()
        with self._kilit:
            self.model      = model
            self.ozellikler = ckpt["ozellikler"]
            self.pencere    = ckpt["pencere"]
            self.ufuk       = ckpt.get("ufuk", 6)
            self.threshold  = float(ckpt.get("threshold", 0.40))
            self.mean       = np.array(ckpt["scaler_mean"],  dtype=np.float32)
            self.scale      = np.array(ckpt["scaler_scale"], dtype=np.float32)
            self.sensorler  = (ckpt.get("sensor_list") or "").split("+")
            self.istatistikler = ckpt.get("istatistikler", {})
            self.surum      = ckpt.get("model_surum", f"GRU-v2-{self.pencere}x{len(self.ozellikler)}")
            self.egitim_zamani = ckpt.get("egitim_zamani", "?")
            self.veri_satir = ckpt.get("veri_satir", "?")
            self._mtime     = os.path.getmtime(self.dosya)
        print(f"Model hazir. Surum={self.surum}, pencere={self.pencere}, "
              f"esik={self.threshold}, egitim={self.egitim_zamani}")

    def gerekirse_yenile(self):
        """Checkpoint dosyası değiştiyse yeni modeli yükle (her istekte ucuz kontrol)."""
        try:
            if os.path.getmtime(self.dosya) > self._mtime:
                print("[model] Yeni checkpoint bulundu, sicak yukleniyor...")
                self.yukle()
        except OSError:
            pass  # dosya tam o an yazılıyor olabilir; sonraki istekte denenir

    def meta(self):
        """asistan.py'nin sohbet cevapları için model meta bilgisi."""
        return {
            "pencere": self.pencere, "ufuk": self.ufuk, "esik": self.threshold,
            "ozellikler": self.ozellikler, "istatistikler": self.istatistikler,
            "model_surum": self.surum, "egitim_zamani": self.egitim_zamani,
            "veri_satir": self.veri_satir,
        }


print("Model yukleniyor...")
yonetici = ModelYonetici(MODEL_DOSYA)

app = Flask(__name__)
CORS(app)  # ESP32 sayfası ve dashboard farklı kaynaktan erişir


# ============================================================
# Yardımcılar
# ============================================================
def _durum_oku():
    """kopru.py'nin yazdığı durum.json'u oku. Yoksa/bozuksa None döndür."""
    if not os.path.exists(DURUM_DOSYA):
        return None
    try:
        with open(DURUM_DOSYA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _csv_ekle(dosya, kolonlar, satir):
    """Bir satırı CSV'ye ekle; dosya yoksa başlıkla oluştur."""
    yeni = not os.path.exists(dosya)
    with open(dosya, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=kolonlar)
        if yeni:
            w.writeheader()
        w.writerow(satir)


# ============================================================
# Yeniden eğitim — arka planda, tek seferde bir tane
# ============================================================
egitim_durumu = {"durum": "bos", "baslangic": None, "son_sonuc": None, "hata": None}
_egitim_kilidi = threading.Lock()


def _egitim_izle(surec):
    """Eğitim subprocess'ini bekle, bitince sonucu kaydet (ayrı thread)."""
    cikti, _ = surec.communicate()
    with _egitim_kilidi:
        if surec.returncode == 0:
            sonuc = None
            try:
                with open(EGITIM_KAYIT, encoding="utf-8") as f:
                    sonuc = json.load(f)
            except Exception:
                pass
            egitim_durumu.update(durum="bos", son_sonuc=sonuc, hata=None)
            print("[egitim] Tamamlandi. Model bir sonraki istekte sicak yuklenecek.")
        else:
            egitim_durumu.update(durum="bos", hata=cikti.strip()[-300:])
            print(f"[egitim] HATA (kod {surec.returncode}): {cikti.strip()[-300:]}")


def egitim_baslat(force=False):
    """yeniden_egit.py'yi arka planda başlat.

    Dönüş: (basladi_mi, kullaniciya_mesaj)
    Flask thread'ini bloklamamak için subprocess kullanılır; ayrıca
    matplotlib/torch eğitimi ayrı süreçte daha güvenlidir.
    """
    with _egitim_kilidi:
        if egitim_durumu["durum"] == "egitiliyor":
            return False, "Eğitim zaten sürüyor — biraz bekleyin."

        n = asistan.gercek_veri_sayisi()
        if n < MIN_SATIR and not force:
            return False, (f"Henüz yeterli gerçek veri yok: {n}/{MIN_SATIR} ölçüm. "
                           f"Sistem çalıştıkça veri birikiyor, daha sonra tekrar deneyin.")

        komut = [sys.executable, "yeniden_egit.py"] + (["--force"] if force else [])
        surec = subprocess.Popen(komut, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        egitim_durumu.update(durum="egitiliyor",
                             baslangic=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             hata=None)
        threading.Thread(target=_egitim_izle, args=(surec,), daemon=True).start()
        return True, (f"Eğitim başladı! {n} gerçek ölçüm + simüle taban veriyle "
                      f"kendimi yeniden eğitiyorum. Bittiğinde yeni model otomatik "
                      f"devreye girer (restart gerekmez).")


# ============================================================
# ENDPOINT'LER
# ============================================================
@app.route("/tahmin", methods=["POST"])
def tahmin():
    """
    Beklenen girdi (JSON):
      {"pencere": [[feat1, feat2, ...], ... PENCERE adet satır ...]}
      Özellik sırası: gru_model.py'deki OZELLIKLER ile aynı olmalı.
    Döndürür:
      {"olasilik": 0.0-1.0, "karar": "...", "mesaj": "..."}
    """
    yonetici.gerekirse_yenile()
    try:
        veri    = request.get_json()
        pencere = np.array(veri["pencere"], dtype=np.float32)

        # Yeterli veri yoksa son satırı tekrarlayarak doldur (demo başlangıcı)
        if len(pencere) < yonetici.pencere:
            eksik  = yonetici.pencere - len(pencere)
            pencere = np.vstack([np.repeat(pencere[:1], eksik, axis=0), pencere])
        pencere = pencere[-yonetici.pencere:]

        norm = (pencere - yonetici.mean) / yonetici.scale
        x    = torch.tensor(norm, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            olasilik = float(torch.sigmoid(yonetici.model(x)).item())

        esik = yonetici.threshold
        if olasilik > esik:
            karar = "SULAMA GEREKLI"
            mesaj = (f"Toprak nemi düşüşte. 30 dk içinde sulama gerekecek "
                     f"(olasılık %{olasilik*100:.0f}, eşik={esik}).")
        else:
            karar = "Sulama gerekmiyor"
            mesaj = (f"Sera koşulları dengede. 30 dk için sulama gerekmiyor "
                     f"(olasılık %{olasilik*100:.0f}, eşik={esik}).")

        return jsonify({"olasilik": olasilik, "karar": karar, "mesaj": mesaj})
    except Exception as e:
        return jsonify({"hata": str(e)}), 400


@app.route("/durum", methods=["GET"])
def durum():
    """
    kopru.py'nin hesapladığı SON canlı/simule durumu döndür.
    Dashboard ve ESP32 sayfası bunu periyodik GET eder.
    """
    d = _durum_oku()
    if d is None:
        return jsonify({
            "mod":   "yok",
            "mesaj": "Kopru (kopru.py) calismiyor. Dashboard kendi tahminine duser.",
        }), 200
    return jsonify(d)


@app.route("/chat", methods=["POST"])
def chat():
    """
    Ziyaretçi sohbeti: {"soru": "..."} → {"cevap": "...", "niyet": "..."}
    Cevaplar ezber değil; o anki durum.json + model meta verisinden üretilir.
    'yeniden eğit' niyeti gelirse eğitim de buradan tetiklenir.
    """
    yonetici.gerekirse_yenile()
    try:
        soru = (request.get_json() or {}).get("soru", "").strip()
    except Exception:
        soru = ""
    if not soru:
        return jsonify({"hata": "Bos soru"}), 400
    if len(soru) > 500:
        soru = soru[:500]  # ESP32 sayfasından gelen aşırı uzun girdiye karşı

    d = _durum_oku() or {"mod": "yok"}
    sonuc = asistan.cevap_uret(soru, d, yonetici.meta())

    # Sohbetten eğitim tetikleme — jüri demosunun yıldız anı :)
    if sonuc["niyet"] == "egitim":
        _, mesaj = egitim_baslat(force=False)
        sonuc["cevap"] = mesaj

    _csv_ekle(SOHBET_CSV,
              ["zaman", "soru", "niyet", "cevap"],
              {"zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "soru": soru, "niyet": sonuc["niyet"], "cevap": sonuc["cevap"]})
    return jsonify(sonuc)


@app.route("/geri_bildirim", methods=["POST", "GET"])
def geri_bildirim():
    """
    Ziyaretçi AI önerisini değerlendirir: {"tip": "dogru"|"yanlis", "yorum": "..."}
    Kayıtlar CSV'de birikir → hem jüriye 'kullanıcı doğrulaması' kanıtı,
    hem gelecekte etiket düzeltmesi (modelin hangi kararları yanlıştı?).
    """
    if request.method == "GET":
        sayilar = {"dogru": 0, "yanlis": 0}
        if os.path.exists(BILDIRIM_CSV):
            with open(BILDIRIM_CSV, newline="", encoding="utf-8") as f:
                for satir in csv.DictReader(f):
                    if satir.get("tip") in sayilar:
                        sayilar[satir["tip"]] += 1
        return jsonify(sayilar)

    veri = request.get_json() or {}
    tip = veri.get("tip")
    if tip not in ("dogru", "yanlis"):
        return jsonify({"hata": "tip 'dogru' veya 'yanlis' olmali"}), 400

    d = _durum_oku() or {}
    _csv_ekle(BILDIRIM_CSV,
              ["zaman", "tip", "karar", "olasilik", "yorum"],
              {"zaman": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "tip": tip,
               "karar": d.get("karar", "?"),
               "olasilik": d.get("olasilik", "?"),
               "yorum": (veri.get("yorum") or "")[:200]})
    return jsonify({"mesaj": "Değerlendirmeniz kaydedildi — teşekkürler! "
                             "Bu geri bildirimler modeli geliştirmek için kullanılacak."})


@app.route("/yeniden_egit", methods=["POST", "GET"])
def yeniden_egit():
    """POST: eğitimi başlat ({"force": true} gate'i atlar). GET: durum sorgula."""
    if request.method == "GET":
        with _egitim_kilidi:
            return jsonify({**egitim_durumu, "min_satir": MIN_SATIR,
                            "gercek_veri": asistan.gercek_veri_sayisi()})
    force = bool((request.get_json(silent=True) or {}).get("force"))
    basladi, mesaj = egitim_baslat(force=force)
    return jsonify({"basladi": basladi, "mesaj": mesaj}), (200 if basladi else 409)


@app.route("/saglik", methods=["GET"])
def saglik():
    """Model ve sensör durum bilgisi (dashboard rozeti + hata ayıklama)."""
    yonetici.gerekirse_yenile()
    d = _durum_oku()
    mod = d.get("mod", "yok") if d else "yok"
    return jsonify({
        "durum":          "calisiyor",
        "mod":            mod,             # "canli" / "simule" / "yok"
        "model":          "GRU",
        "model_version":  yonetici.surum,
        "egitim_zamani":  yonetici.egitim_zamani,
        "egitim_satir":   yonetici.veri_satir,
        "pencere":        yonetici.pencere,
        "ozellik_sayisi": len(yonetici.ozellikler),
        "sensors":        yonetici.sensorler,
        "egitim_durumu":  egitim_durumu["durum"],
        "gercek_veri":    asistan.gercek_veri_sayisi(),
    })


if __name__ == "__main__":
    # macOS'ta port 5000'i AirPlay Receiver tutar → 5001 kullanılır.
    # host=0.0.0.0: ESP32 ağındaki telefonlar da sohbet için erişebilsin.
    print("\nAPI baslatiliyor: http://0.0.0.0:5001")
    print("Dashboard'u veya ESP32 sayfasini acin, otomatik baglanacak.\n")
    app.run(host="0.0.0.0", port=5001, debug=False)
