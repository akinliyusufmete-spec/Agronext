"""
AgroNext - Kendini Yeniden Eğitme Hattı (yeniden_egit.py)
==========================================================
Sistemin "KENDİNİ EĞİTEN" halkası. kopru.py çalıştıkça gerçek ölçümler
gercek_sera_verileri.csv'de birikir; bu script birikmiş veriyle modeli
YENİDEN eğitir. Eğitim bitince api.py ve kopru.py yeni modeli RESTART
GEREKMEDEN sıcak yükler (dosya mtime kontrolü).

VERİ STRATEJİSİ:
  - Gerçek veri az  (< 2000 satır): simüle taban + gerçek veri BİRLEŞTİRİLİR.
    (Az gerçek veriyle sıfırdan eğitim ezberler; simüle taban genellemeyi korur.)
  - Gerçek veri çok (>= 2000 satır): SADECE gerçek veriyle eğitilir.
  - İki bloğun birleşme noktasındaki ~18 pencere karışıktır; toplam içinde
    ihmal edilebilir (yorum olarak bilinçli bırakıldı).

GÜVENLİK: Eski model her eğitimden önce yedekler/ klasörüne kopyalanır.

KULLANIM:
    .venv_new/bin/python yeniden_egit.py            # veri yeterliyse eğitir
    .venv_new/bin/python yeniden_egit.py --force    # veri azsa da eğitir (test)
    AGRONEXT_MIN_SATIR=60 ... api.py                # demo için eşiği düşür

ÇIKIŞ KODLARI: 0=başarılı, 2=yetersiz veri, 1=hata
(api.py /yeniden_egit bu scripti subprocess olarak çalıştırır.)
"""

import os
import sys
import json
import shutil
from datetime import datetime

import pandas as pd

import gru_model   # egit_ve_kaydet() buradan gelir — eğitim kodu TEK yerde

GERCEK_CSV    = "gercek_sera_verileri.csv"
SIMULE_CSV    = "simule_sera_verileri.csv"
BIRLESIK_CSV  = "egitim_birlesik.csv"       # eğitime giren birleşik veri (iz için saklanır)
MODEL_DOSYA   = "agronext_gru_model.pt"
KAYIT_DOSYA   = "egitim_kaydi.json"         # api.py /saglik ve sohbet bunu okur
YEDEK_KLASOR  = "yedekler"

MIN_SATIR        = int(os.environ.get("AGRONEXT_MIN_SATIR", "360"))
SADECE_GERCEK_ESIK = 2000   # bu kadar gerçek satır varsa simüle taban bırakılır


def gercek_satir_sayisi() -> int:
    if not os.path.exists(GERCEK_CSV):
        return 0
    with open(GERCEK_CSV, encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)   # başlık hariç


def main():
    force = "--force" in sys.argv
    n_gercek = gercek_satir_sayisi()

    print(f"Gercek veri: {n_gercek} satir (esik: {MIN_SATIR}, force={force})")

    if n_gercek < MIN_SATIR and not force:
        print(f"YETERSIZ VERI: {n_gercek}/{MIN_SATIR}. Sistem calistikca birikir. "
              f"(Testte zorlamak icin: --force)")
        sys.exit(2)

    # --- 1. Eğitim verisini hazırla ---
    kaynaklar = []
    parcalar = []
    if n_gercek >= SADECE_GERCEK_ESIK:
        parcalar.append(pd.read_csv(GERCEK_CSV))
        kaynaklar.append(f"gercek({n_gercek})")
    else:
        parcalar.append(pd.read_csv(SIMULE_CSV))
        kaynaklar.append("simule(taban)")
        if n_gercek > 0:
            parcalar.append(pd.read_csv(GERCEK_CSV))
            kaynaklar.append(f"gercek({n_gercek})")

    df = pd.concat(parcalar, ignore_index=True)
    df.to_csv(BIRLESIK_CSV, index=False)
    print(f"Egitim verisi: {len(df)} satir  [{' + '.join(kaynaklar)}]")

    # --- 2. Eski modeli yedekle (kötü eğitime karşı geri dönüş yolu) ---
    if os.path.exists(MODEL_DOSYA):
        os.makedirs(YEDEK_KLASOR, exist_ok=True)
        yedek = os.path.join(
            YEDEK_KLASOR,
            f"agronext_gru_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pt")
        shutil.copy2(MODEL_DOSYA, yedek)
        print(f"Eski model yedeklendi: {yedek}")

    # --- 3. Eğit (gru_model.py'deki ortak fonksiyon — kod kopyası yok) ---
    metrikler = gru_model.egit_ve_kaydet(BIRLESIK_CSV, MODEL_DOSYA)

    # --- 4. Eğitim kaydını yaz (api.py /saglik + sohbet asistanı okur) ---
    kayit = {**metrikler, "kaynaklar": kaynaklar, "gercek_satir": n_gercek}
    with open(KAYIT_DOSYA, "w", encoding="utf-8") as f:
        json.dump(kayit, f, ensure_ascii=False, indent=2)

    print(f"\nYENIDEN EGITIM TAMAM. F1=%{metrikler['f1']*100:.1f} "
          f"Recall=%{metrikler['recall']*100:.1f} — kayit: {KAYIT_DOSYA}")
    print("api.py ve kopru.py yeni modeli otomatik sicak yukleyecek.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"HATA: {e}")
        sys.exit(1)
