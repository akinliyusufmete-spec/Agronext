"""
AgroNext - Sahte ESP32 (sahte_esp32.py)
========================================
Gerçek ESP32 elimize ulaşana kadar firmware'i BİREBİR taklit eden simülatör.
Donanımsız uçtan uca test ve demo için: web sitesi, /oku, /ai, /ai_durum —
hepsi gerçek firmware ile AYNI arayüz.

NE İŞE YARAR:
  - Yusuf: tüm Python zincirini (kopru.py → api.py → site) donanımsız test eder.
  - Ali Kaan: firmware'in nasıl davranması gerektiğini buradan görür
    (endpoint'ler ve JSON şeması birebir aynı).
  - Jüri provası: sensörler gelmeden tam sistem demosu yapılabilir.

SİMÜLASYON: Toprak yavaşça kurur (~%3/dk) → %32'nin altında "pompa" çalışır
→ toprak hızla ıslanır. Böylece GRU'nun "sulamadan ÖNCE" tahmin etmesi
sitede canlı izlenebilir.

ÇALIŞTIRMA (3 terminal):
    .venv_new/bin/python sahte_esp32.py                            # bu (port 8032)
    AGRONEXT_ESP32=http://127.0.0.1:8032 .venv_new/bin/python kopru.py
    .venv_new/bin/python api.py
    → tarayıcıda http://127.0.0.1:8032  (gerçekte: http://192.168.4.1)
"""

import math
import time
import random
from datetime import datetime

from flask import Flask, request, jsonify, Response

PORT = 8032
SAYFA_DOSYA = "esp32_sayfa.html"   # firmware'deki PROGMEM sayfanın kaynağı

app = Flask(__name__)

# ============================================================
# Sensör simülasyonu — durum zamanla değişir (her /oku çağrısında ilerler)
# ============================================================
durum = {
    "soil_pct": 58.0,
    "pump": 0,
    "pump_bitis": 0.0,   # pompa ne zamana kadar açık kalacak (epoch sn)
    "son_adim": time.time(),
}

KURUMA_HIZI  = 0.05    # %/saniye (~%3/dk — demo için hızlandırılmış)
ISLANMA_HIZI = 0.9     # %/saniye (pompa açıkken)
POMPA_ESIK   = 32.0    # firmware'deki yerel yedek eşik (PUMP_ON_THRESHOLD benzeri)
POMPA_SURE   = 35.0    # saniye


def sensorleri_simule():
    """Gerçekçi sera değerleri üret; toprak kuruma/sulama döngüsünü ilerlet."""
    simdi = time.time()
    dt = min(simdi - durum["son_adim"], 30.0)   # uyuyan süreçte sıçrama olmasın
    durum["son_adim"] = simdi

    # --- Toprak nemi: kuru → pompa → ıslak döngüsü ---
    if durum["pump"]:
        durum["soil_pct"] += ISLANMA_HIZI * dt
        if simdi >= durum["pump_bitis"] or durum["soil_pct"] >= 65:
            durum["pump"] = 0
    else:
        durum["soil_pct"] -= KURUMA_HIZI * dt * random.uniform(0.7, 1.3)
        if durum["soil_pct"] <= POMPA_ESIK:
            durum["pump"] = 1
            durum["pump_bitis"] = simdi + POMPA_SURE
    durum["soil_pct"] = max(15.0, min(80.0, durum["soil_pct"]))

    # --- Günlük döngüye bağlı değerler (saat etkisi) ---
    saat = datetime.now().hour + datetime.now().minute / 60.0
    gun = math.sin(math.pi * max(0.0, min(1.0, (saat - 6) / 14)))  # 06-20 arası tepe

    temp = 21 + 7 * gun + random.uniform(-0.4, 0.4)
    hum  = 70 - 12 * gun + random.uniform(-1.5, 1.5)
    lux  = int(max(0, 28000 * gun + random.uniform(-800, 800)))
    co2  = int(520 - 80 * gun + random.uniform(-15, 15))
    pres = 1013 + 2 * math.sin(simdi / 3600) + random.uniform(-0.3, 0.3)
    voc  = int(25000 + 3000 * gun + random.uniform(-500, 500))
    ph   = round(6.4 + random.uniform(-0.05, 0.05), 2)

    soil_pct = round(durum["soil_pct"], 1)
    # Firmware'deki soilToPercent'in tersi: %'den ham ADC'ye (şema uyumu için)
    soil_raw = int(3200 - soil_pct / 100.0 * (3200 - 1200))

    # Firmware jsonUret() ile AYNI şema (CLAUDE.md - EN KRİTİK BAĞIMLILIK)
    return {
        "temp": round(temp, 1), "humidity": round(hum, 1),
        "pressure": round(pres, 1), "voc": voc,
        "soil_raw": soil_raw, "soil_pct": soil_pct,
        "co2": co2, "lux": lux, "ph": ph, "pump": durum["pump"],
    }


# ============================================================
# AI durumu — firmware'deki aiJson/beyinIp/aiZaman ile aynı mantık
# ============================================================
ai = {"json": None, "beyin_ip": "", "zaman": 0.0}


@app.route("/")
def site():
    """AgroNext web sitesi (gerçek ESP32'de PROGMEM'den sunulur)."""
    with open(SAYFA_DOSYA, encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


@app.route("/oku")
def oku():
    cevap = jsonify(sensorleri_simule())
    cevap.headers["Access-Control-Allow-Origin"] = "*"
    return cevap


@app.route("/ai", methods=["POST"])
def ai_post():
    govde = request.get_data(as_text=True)
    if not govde or len(govde) > 2048:
        return jsonify({"hata": "gecersiz govde"}), 400
    ai["json"] = govde
    ai["beyin_ip"] = request.remote_addr   # firmware: server.client().remoteIP()
    ai["zaman"] = time.time()
    return jsonify({"ok": 1})


@app.route("/ai_durum")
def ai_durum():
    if ai["json"]:
        yas = int(time.time() - ai["zaman"])
        govde = f'{{"ai":{ai["json"]},"beyin_ip":"{ai["beyin_ip"]}","yas_sn":{yas}}}'
    else:
        govde = '{"ai":null,"beyin_ip":"","yas_sn":-1}'
    return Response(govde, mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"})


if __name__ == "__main__":
    print(f"\nSahte ESP32 basladi: http://127.0.0.1:{PORT}")
    print("  Site     : /            (gercek ESP32: http://192.168.4.1)")
    print("  Sensor   : /oku")
    print("  AI push  : POST /ai     (kopru.py gonderir)")
    print("  AI durum : /ai_durum\n")
    app.run(host="127.0.0.1", port=PORT, debug=False)
