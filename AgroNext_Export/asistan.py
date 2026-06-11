"""
AgroNext - Yapay Zekâ Asistan Modülü (asistan.py)
==================================================
Sistemin "KONUŞAN ve YORUMLAYAN" katmanı. Üç görevi var:

  1. ANOMALİ KIYASI : Canlı sensör verisini, modelin EĞİTİLDİĞİ verinin
     istatistikleriyle (checkpoint'teki min/max/p05/p95) karşılaştırır.
     "Bu değer modelin hiç görmediği bir aralıkta" gibi uyarılar üretir.
  2. ÖNERİ ÜRETİMİ  : GRU tahmini + agronomik kurallarla somut öneriler
     üretir (havalandır, gölgele, sulamaya hazırlan...).
  3. SOHBET         : Ziyaretçinin Türkçe sorularını anahtar-kelime
     eşleştirmesiyle anlayıp CANLI veriye dayalı cevap verir.

NASIL ÇALIŞIR (dürüst teknik not — sunumda da böyle anlatın):
  - Geleceği TAHMİN EDEN kısım GRU derin öğrenme modelidir (gru_model.py).
  - Sohbet kısmı ise KURAL TABANLI bir uzman sistemdir: dev dil modeli
    (LLM) değildir, ama cevapları ezber değil; o anki sensör verisinden,
    GRU çıktısından ve eğitim istatistiklerinden CANLI üretilir.

KULLANIM: kopru.py ve api.py bu modülü import eder; tek başına çalışmaz.
"""

import os
import re
import math
from datetime import datetime

# ============================================================
# Sera için ideal aralıklar (genel sebze serası, domates baz)
# Kaynak: FAO sera üretim rehberleri — danışman teyidi alınacak.
# ============================================================
IDEAL = {
    "temp":     {"alt": 18,    "ust": 27,    "birim": "°C",  "ad": "Sıcaklık"},
    "humidity": {"alt": 50,    "ust": 75,    "birim": "%",   "ad": "Hava nemi"},
    "soil_pct": {"alt": 40,    "ust": 75,    "birim": "%",   "ad": "Toprak nemi"},
    "co2":      {"alt": 400,   "ust": 1200,  "birim": "ppm", "ad": "CO₂"},
    "lux":      {"alt": 5000,  "ust": 80000, "birim": "lux", "ad": "Işık"},
    "ph":       {"alt": 5.5,   "ust": 6.8,   "birim": "",    "ad": "pH"},
    "vpd":      {"alt": 0.4,   "ust": 1.3,   "birim": "kPa", "ad": "VPD"},
}

# Anomali kıyasında atlanan özellikler (zaman kodlaması fiziksel sensör değil)
ANOMALI_ATLA = {"hour_sin", "hour_cos"}

# Özelliklerin insan-okur adları (anomali mesajları için)
OZELLIK_AD = {
    "temp": "Sıcaklık", "humidity": "Hava nemi", "soil_pct": "Toprak nemi",
    "co2": "CO₂", "pressure": "Basınç", "voc": "VOC", "lux": "Işık",
    "vpd": "VPD", "ph_imputed": "pH",
}
OZELLIK_BIRIM = {
    "temp": "°C", "humidity": "%", "soil_pct": "%", "co2": " ppm",
    "pressure": " hPa", "voc": " ohm", "lux": " lux", "vpd": " kPa",
    "ph_imputed": "",
}


# ============================================================
# 1) ANOMALİ KIYASI — canlı veri vs eğitim verisi
# ============================================================
def anomali_bul(ozellikler: dict, istatistikler: dict) -> list:
    """Canlı özellik değerlerini eğitim istatistikleriyle kıyasla.

    ozellikler    : kopru.py'nin türettiği özellik sözlüğü (temp, vpd, ...)
    istatistikler : checkpoint'teki {ozellik: {min,max,p05,p95,...}} sözlüğü

    Döndürür: Türkçe uyarı listesi. Boş liste = her şey eğitim aralığında.
    """
    uyarilar = []
    if not istatistikler:
        return uyarilar

    for ad, ist in istatistikler.items():
        if ad in ANOMALI_ATLA or ad not in ozellikler:
            continue
        deger = float(ozellikler[ad])
        insan = OZELLIK_AD.get(ad, ad)
        birim = OZELLIK_BIRIM.get(ad, "")

        # Sert sınır: eğitim verisinde HİÇ görülmemiş değer → model "kör"
        if deger < ist["min"] or deger > ist["max"]:
            uyarilar.append(
                f"{insan} {deger:.1f}{birim}: modelin eğitim verisinde hiç "
                f"görülmedi ({ist['min']:.1f}–{ist['max']:.1f}{birim}). "
                f"Tahmine temkinli yaklaşın."
            )
        # Yumuşak sınır: eğitim verisinin %90 aralığının dışında → uçta
        elif deger < ist["p05"] or deger > ist["p95"]:
            uyarilar.append(
                f"{insan} {deger:.1f}{birim}: eğitim verisinin tipik aralığının "
                f"({ist['p05']:.1f}–{ist['p95']:.1f}{birim}) ucunda."
            )
    return uyarilar


# ============================================================
# 2) ÖNERİ ÜRETİMİ — GRU tahmini + agronomik kurallar
# ============================================================
def oneri_uret(olcum: dict, olasilik: float, karar: str,
               vpd: float, anomaliler: list, esik: float = 0.4) -> list:
    """Somut, eyleme dönük öneriler üret.

    Her öneri: {"baslik": ..., "detay": ..., "seviye": kritik|uyari|bilgi}
    En kritik öneri başa gelir; liste en fazla 5 öğeyle sınırlanır
    (ESP32'nin RAM'i ve ekran alanı sınırlı).
    """
    oneriler = []
    temp = float(olcum.get("temp", 24))
    hum  = float(olcum.get("humidity", 60))
    soil = float(olcum.get("soil_pct", 50))
    co2  = float(olcum.get("co2", 420))
    lux  = float(olcum.get("lux", 0))
    saat = datetime.now().hour

    # --- 1. Sulama (GRU kararı — sistemin ana çıktısı) ---
    if olasilik > esik:
        oneriler.append({
            "baslik": "Sulamaya hazırlanın",
            "detay": f"GRU modeli 30 dk içinde sulama ihtiyacını %{olasilik*100:.0f} "
                     f"olasılıkla öngörüyor. Vana ve su hattını kontrol edin.",
            "seviye": "kritik",
        })
    else:
        oneriler.append({
            "baslik": "Sulama planlanmıyor",
            "detay": f"30 dk içinde sulama ihtiyacı düşük (%{olasilik*100:.0f}). "
                     f"Su tasarrufu: gereksiz sulama yapılmıyor.",
            "seviye": "bilgi",
        })

    # --- 2. VPD (bitki su stresi göstergesi) ---
    if vpd > 1.5:
        oneriler.append({
            "baslik": "Bitki su stresi riski",
            "detay": f"VPD {vpd:.2f} kPa (ideal 0.4–1.3). Sisleme/gölgeleme ile "
                     f"nemi artırın; bitki normalden hızlı su kaybediyor.",
            "seviye": "kritik" if vpd > 2.0 else "uyari",
        })
    elif 0 < vpd < 0.3:
        oneriler.append({
            "baslik": "Mantar hastalığı riski",
            "detay": f"VPD {vpd:.2f} kPa çok düşük — yapraklar kuruyamıyor. "
                     f"Havalandırmayı artırın.",
            "seviye": "uyari",
        })

    # --- 3. Sıcaklık ---
    if temp > 30:
        oneriler.append({
            "baslik": "Sera fazla sıcak",
            "detay": f"{temp:.1f}°C (ideal 18–27). Havalandırma açın veya "
                     f"gölgeleme perdesini kapatın.",
            "seviye": "kritik" if temp > 35 else "uyari",
        })
    elif temp < 15:
        oneriler.append({
            "baslik": "Sera fazla soğuk",
            "detay": f"{temp:.1f}°C (ideal 18–27). Isıtma gerekebilir; "
                     f"soğuk stres büyümeyi yavaşlatır.",
            "seviye": "uyari",
        })

    # --- 4. Hava nemi ---
    if hum > 85:
        oneriler.append({
            "baslik": "Nem çok yüksek",
            "detay": f"%{hum:.0f} hava nemi mantar hastalıklarını davet eder. "
                     f"Havalandırın.",
            "seviye": "uyari",
        })
    elif hum < 40:
        oneriler.append({
            "baslik": "Hava çok kuru",
            "detay": f"%{hum:.0f} nem düşük. Sisleme değerlendirin.",
            "seviye": "uyari",
        })

    # --- 5. Toprak nemi (ham eşik — GRU'dan bağımsız ikinci göz) ---
    if soil > 85:
        oneriler.append({
            "baslik": "Toprak aşırı ıslak",
            "detay": f"%{soil:.0f} toprak nemi kök çürümesi riski taşır. "
                     f"Sulamayı durdurun, drenajı kontrol edin.",
            "seviye": "uyari",
        })

    # --- 6. CO₂ ---
    if co2 > 1500:
        oneriler.append({
            "baslik": "CO₂ birikmiş",
            "detay": f"{co2:.0f} ppm yüksek. Çalışan varsa havalandırma şart.",
            "seviye": "uyari",
        })

    # --- 7. Işık (sadece gündüz anlamlı) ---
    if 8 <= saat <= 17 and lux < 3000:
        oneriler.append({
            "baslik": "Işık yetersiz",
            "detay": f"Gündüz {lux:.0f} lux düşük (hedef 5000+). Gölgeleme "
                     f"açıksa kaldırın; kışın ek LED aydınlatma değerlendirin.",
            "seviye": "bilgi",
        })

    # --- 8. Anomali varsa sensör kontrolü öner ---
    if anomaliler:
        oneriler.append({
            "baslik": "Sensör kontrolü önerilir",
            "detay": f"{len(anomaliler)} ölçüm eğitim verisi aralığının dışında. "
                     f"Sensör bağlantılarını kontrol edin veya modeli yeni "
                     f"veriyle yeniden eğitin.",
            "seviye": "uyari",
        })

    # Kritik > uyarı > bilgi sırala, en fazla 5 öneri döndür
    sira = {"kritik": 0, "uyari": 1, "bilgi": 2}
    oneriler.sort(key=lambda o: sira[o["seviye"]])
    return oneriler[:5]


# ============================================================
# 3) SOHBET — kural tabanlı Türkçe soru-cevap
# ============================================================
def _fmt(deger, ondalik=1):
    """Sayıyı Türkçe metne hazırla; None/eksikse '?' döndür."""
    try:
        return f"{float(deger):.{ondalik}f}"
    except (TypeError, ValueError):
        return "?"


def _ideal_cevap(anahtar: str, deger) -> str:
    """Bir sensör değerini ideal aralıkla karşılaştıran cümle kur."""
    i = IDEAL[anahtar]
    try:
        d = float(deger)
    except (TypeError, ValueError):
        return f"{i['ad']} şu an okunamıyor."
    if d < i["alt"]:
        durum = f"ideal aralığın ({i['alt']}–{i['ust']}{i['birim']}) ALTINDA"
    elif d > i["ust"]:
        durum = f"ideal aralığın ({i['alt']}–{i['ust']}{i['birim']}) ÜSTÜNDE"
    else:
        durum = f"ideal aralıkta ({i['alt']}–{i['ust']}{i['birim']})"
    return f"{i['ad']} şu an {_fmt(d)}{i['birim']} — {durum}."


def gercek_veri_sayisi(log_csv: str = "gercek_sera_verileri.csv") -> int:
    """Canlı logda kaç ölçüm birikmiş? (başlık satırı hariç)"""
    if not os.path.exists(log_csv):
        return 0
    with open(log_csv, encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


def cevap_uret(soru: str, durum: dict, meta: dict) -> dict:
    """Ziyaretçi sorusuna canlı veriye dayalı cevap üret.

    soru  : ziyaretçinin yazdığı metin
    durum : kopru.py'nin durum.json içeriği (son_olcum, olasilik, karar,
            mesaj, mod, oneriler, anomaliler, timestamp)
    meta  : model meta bilgisi (pencere, ufuk, esik, ozellikler,
            egitim_zamani, veri_satir, model_surum)

    Döndürür: {"cevap": str, "niyet": str}
    niyet == "egitim" ise api.py yeniden eğitimi tetikler.
    """
    s = soru.casefold().strip()
    olcum = durum.get("son_olcum") or {}
    olasilik = durum.get("olasilik")
    mod = durum.get("mod", "yok")

    def olasilik_metni():
        if olasilik is None:
            return "şu an tahmin üretilmiyor (köprü kapalı olabilir)"
        return f"%{float(olasilik)*100:.0f}"

    # --- Niyetler: (ad, regex) — İLK eşleşen kazanır, sıra önemli ---
    niyetler = [
        ("egitim",   r"(yeniden|kendini|tekrar)\s*(eğit|egit|öğren|ogren)|eğitil|egitil"),
        ("selam",    r"^(selam|merhaba|mrb|hey|sa$|günaydın|gunaydin|iyi (akşam|aksam|gün|gun))"),
        ("tesekkur", r"(teşekkür|tesekkur|sağol|sagol|eyvallah|süpersin|supersin)"),
        ("sulama",   r"sula|pompa|vana|su ver|kuru|tahmin|karar"),
        ("oneri",    r"öneri|oneri|tavsiye|ne yap|öner |oner "),
        ("anomali",  r"anomali|normal mi|garip|tuhaf|sorun var|kıyas|kiyas|karşılaştır|karsilastir"),
        ("sicaklik", r"sıcak|sicak|derece|temp|soğuk|soguk|ısı|isi\b"),
        ("toprak",   r"toprak"),
        ("nem",      r"\bnem|humidity|rutubet"),
        ("co2",      r"co2|co₂|karbondioksit|hava kalite"),
        ("isik",     r"ışık|isik|lux|aydınlık|aydinlik|güneş|gunes"),
        ("ph",       r"\bph\b|asit|alkali"),
        ("vpd",      r"vpd|buhar|stres"),
        ("basinc",   r"basınç|basinc|pressure|hpa"),
        ("veri",     r"kaç (veri|ölçüm|olcum)|veri (sayısı|sayisi)|ne kadar veri|kayıt|kayit|log"),
        ("model",    r"model|gru|yapay zek|nasıl çalış|nasil calis|algoritma|doğruluk|dogruluk|accuracy"),
        ("tasarruf", r"tasarruf|fayda|kazan|maliyet|fiyat|ucuz"),
        ("proje",    r"agronext|proje|teknofest|kim(sin|dir)|nedir bu"),
        ("mod",      r"\bmod\b|canlı mı|canli mi|simüle|simule|bağlı mı|bagli mi"),
        ("yardim",   r"yardım|yardim|ne sorabilirim|neler yapabilir|komut"),
    ]

    niyet = "bilinmiyor"
    for ad, desen in niyetler:
        if re.search(desen, s):
            niyet = ad
            break

    # --- Cevap üretimi ---
    if niyet == "selam":
        cevap = (f"Merhaba! Ben AgroNext'in sera asistanıyım. 🌱 Şu an sera "
                 f"{'CANLI sensörlerden izleniyor' if mod == 'canli' else 'simüle veriyle çalışıyor' if mod == 'simule' else 'beklemede'}. "
                 f"Sulama tahmini, sıcaklık, nem, öneriler... ne merak ediyorsan sor. "
                 f"('yardım' yazarsan neler bildiğimi listelerim.)")

    elif niyet == "tesekkur":
        cevap = "Rica ederim! Sera sağlığı için buradayım. 🌿"

    elif niyet == "sulama":
        karar = durum.get("karar", "henüz karar yok")
        cevap = (f"Sulama olasılığı şu an {olasilik_metni()} (eşik %{float(meta.get('esik', 0.4))*100:.0f}). "
                 f"Karar: {karar}. Bu tahmini, son {meta.get('pencere', 12)} ölçümün "
                 f"örüntüsüne bakan GRU derin öğrenme modeli üretti — yani 'şu an kuru mu' "
                 f"değil, '30 dakika SONRA sulama gerekecek mi' sorusuna cevap veriyor. "
                 f"Toprak nemi şu an %{_fmt(olcum.get('soil_pct'))}.")

    elif niyet == "oneri":
        oneriler = durum.get("oneriler") or []
        if oneriler:
            satirlar = [f"• {o['baslik']}: {o['detay']}" for o in oneriler[:4]]
            cevap = "Güncel önerilerim:\n" + "\n".join(satirlar)
        else:
            cevap = "Şu an özel bir önerim yok — sera koşulları dengede görünüyor."

    elif niyet == "anomali":
        anomaliler = durum.get("anomaliler") or []
        if anomaliler:
            cevap = ("Canlı veriyi eğitim verimle kıyasladım, dikkat çeken noktalar:\n"
                     + "\n".join(f"• {a}" for a in anomaliler[:4]))
        else:
            cevap = (f"Canlı veriyi eğitildiğim {meta.get('veri_satir', '?')} ölçümlük "
                     f"veriyle kıyasladım: tüm değerler eğitim aralığımın içinde. "
                     f"Tahminlerim bu bölgede güvenilir. ✅")

    elif niyet == "sicaklik":
        cevap = _ideal_cevap("temp", olcum.get("temp"))
    elif niyet == "toprak":
        cevap = (_ideal_cevap("soil_pct", olcum.get("soil_pct"))
                 + f" Sulama olasılığı: {olasilik_metni()}.")
    elif niyet == "nem":
        cevap = _ideal_cevap("humidity", olcum.get("humidity"))
    elif niyet == "co2":
        cevap = _ideal_cevap("co2", olcum.get("co2"))
    elif niyet == "isik":
        cevap = _ideal_cevap("lux", olcum.get("lux"))
    elif niyet == "ph":
        cevap = (_ideal_cevap("ph", olcum.get("ph"))
                 + " (Not: pH sensörü pilot aşamada — değer kalibre edilmemiş olabilir.)")
    elif niyet == "basinc":
        cevap = (f"Atmosfer basıncı {_fmt(olcum.get('pressure'))} hPa. Ani düşüş "
                 f"hava değişimi/fırtına habercisi olabilir; model bunu dolaylı kullanır.")

    elif niyet == "vpd":
        # VPD durum.json'a yazılmıyor; sıcaklık+nemden burada yeniden hesaplanır
        try:
            t = float(olcum.get("temp")); h = float(olcum.get("humidity"))
            es = 0.6108 * math.exp(17.27 * t / (t + 237.3))
            vpd = max(0.0, es - es * h / 100.0)
            cevap = (f"VPD (buhar basıncı açığı) şu an {vpd:.2f} kPa — ideal 0.4–1.3. "
                     f"VPD, bitkinin terleme yoluyla ne kadar su kaybettiğini gösterir; "
                     f"GRU modelimin 10 girdisinden biridir.")
        except (TypeError, ValueError):
            cevap = "VPD hesaplamak için sıcaklık ve nem verisi gerekli — şu an okunamıyor."

    elif niyet == "veri":
        n = gercek_veri_sayisi()
        cevap = (f"Eğitimimde {meta.get('veri_satir', '?')} ölçüm kullanıldı "
                 f"(son eğitim: {meta.get('egitim_zamani', '?')}). Canlı logda şu ana "
                 f"kadar {n} yeni ölçüm birikti — yeterince birikince 'yeniden eğit' "
                 f"diyerek beni güncel veriyle eğitebilirsin.")

    elif niyet == "model":
        cevap = (f"Ben {meta.get('model_surum', 'GRU')} sürümüyüm. Çekirdeğimde "
                 f"2 katmanlı GRU (Gated Recurrent Unit) derin öğrenme ağı var: "
                 f"son {meta.get('pencere', 12)} ölçümü (1 saat) okur, "
                 f"{len(meta.get('ozellikler', []) or [])} özellikten 30 dk sonrası için "
                 f"sulama olasılığı üretirim. Son eğitim: {meta.get('egitim_zamani', '?')} "
                 f"({meta.get('veri_satir', '?')} ölçümle). Eşik tabanlı sistemlerden farkım: "
                 f"tepki vermem, ÖNGÖRÜRÜM.")

    elif niyet == "tasarruf":
        cevap = ("Önceden tahmin sayesinde su, tam ihtiyaç anında ve gereken kadar verilir. "
                 "Simülasyonlarımızda eşik tabanlı sulamaya göre ~%30 su tasarrufu hedefliyoruz "
                 "(sahada gerçek veriyle doğrulanacak). Ticari sistemlerin ~onda biri maliyetle.")

    elif niyet == "proje":
        cevap = ("AgroNext, orta ölçekli seralar için yapay zekâ destekli akıllı sulama "
                 "sistemi — TEKNOFEST Tarım Teknolojileri yarışması lise takımı projesiyiz. "
                 "ESP32 sensörleri okur, GRU modelim sulamayı 30 dk önceden tahmin eder.")

    elif niyet == "mod":
        m = {"canli": "CANLI — ESP32 sensörlerinden gerçek veri akıyor",
             "simule": "SİMÜLE — ESP32'ye ulaşılamıyor, kayıtlı veriyle çalışıyorum",
             "yok": "BEKLEMEDE — köprü (kopru.py) çalışmıyor"}.get(mod, mod)
        cevap = f"Şu anki mod: {m}. Son güncelleme: {durum.get('timestamp', '?')}."

    elif niyet == "egitim":
        # Asıl tetikleme api.py'de — burada sadece niyet bildirilir
        cevap = ""  # api.py dolduracak

    elif niyet == "yardim":
        cevap = ("Şunları sorabilirsin:\n"
                 "• 'Sulama gerekecek mi?' — GRU tahminim\n"
                 "• 'Sıcaklık / nem / toprak / CO₂ / ışık / pH nasıl?'\n"
                 "• 'Önerin ne?' — güncel eylem önerilerim\n"
                 "• 'Veriler normal mi?' — eğitim verimle kıyas\n"
                 "• 'Modelini anlat' — nasıl çalıştığım\n"
                 "• 'Yeniden eğit' — birikmiş gerçek veriyle kendimi eğitirim")

    else:  # bilinmiyor
        cevap = (f"Bunu tam anlayamadım 🌱 ama elimdeki güncel durum: "
                 f"sulama olasılığı {olasilik_metni()}, "
                 f"sıcaklık {_fmt(olcum.get('temp'))}°C, "
                 f"toprak nemi %{_fmt(olcum.get('soil_pct'))}. "
                 f"'yardım' yazarsan neler sorabileceğini listelerim.")

    return {"cevap": cevap, "niyet": niyet}
