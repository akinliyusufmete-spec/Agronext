"""
AgroNext - GRU Sulama Tahmin Modeli (EĞİTİM)
=============================================
Sera sensör verisinin ZAMAN SERİSİ örüntüsünü öğrenir ve GELECEKTEKİ sulama
ihtiyacını ÖNCEDEN tahmin eder. Eşik tabanlı sistemlerden farkı: "şu an kuru"
değil, "30 dakika sonra kuruyacak" der.

Bu script:
  1. Veriyi ZAMANa göre böler (karıştırmadan — gelecek geçmişi kirletmesin)
  2. Scaler'ı yalnızca train verisine fit eder (veri sızıntısı yok)
  3. GRU modelini EarlyStopping + LR Scheduler ile eğitir
  4. Precision / Recall / F1 + PR eğrisi + karmaşıklık matrisi üretir
  5. Sunumda kullanılabilir grafikler kaydeder (PNG)
  6. Model, scaler ve meta bilgileri kaydeder → tahmin.py & api.py kullanır

Desteklenen sensörler (BME680 güncellemesiyle):
  temp, humidity, pressure, voc  → BME680 (I2C)
  soil_pct                       → Kapasitif toprak nemi (GPIO36)
  co2                            → MQ-135 (GPIO34)
  lux                            → BH1750 (I2C)
  hour_sin, hour_cos, vpd        → Türetilmiş özellikler

KURULUM:  pip install torch numpy pandas scikit-learn matplotlib
KULLANIM:
    python gru_model.py                       # simüle veriyle eğitir
    python gru_model.py sera_verileri.csv     # gerçek veriyle eğitir
"""

import sys
import copy
from datetime import datetime
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    precision_recall_fscore_support,
    confusion_matrix,
    roc_auc_score,
    precision_recall_curve,
    auc as sklearn_auc,
)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---- Ayarlar ----
PENCERE = 12          # geçmiş adım sayısı (12 × 5dk = 1 saat)
UFUK    = 6           # kaç adım sonrası tahmin (6 × 5dk = 30 dk)

# Yeni sensör setine göre güncellenmiş özellik listesi
OZELLIKLER = [
    "temp",       # BME680 sıcaklık (°C)
    "humidity",   # BME680 bağıl nem (%)
    "soil_pct",   # Kapasitif toprak nemi (%)
    "co2",        # MQ-135 CO2 (ppm)
    "pressure",   # BME680 atmosfer basıncı (hPa)
    "voc",        # BME680 VOC direnci (ohm)
    "lux",        # BH1750 ışık yoğunluğu (lux)
    "hour_sin",   # Zaman döngüsel kodlama — sinüs
    "hour_cos",   # Zaman döngüsel kodlama — kosinüs
    "vpd",        # Türetilmiş: buhar basıncı açığı (kPa), BME680 verisiyle
]

ESIK   = 0.40   # Sınıflandırma eşiği — Recall > Precision tercih edilir;
                # kaçırılan sulama, yanlış alarm uyarısından daha kötüdür
EPOCH  = 80     # Maks epoch (EarlyStopping erken durdurur)
BATCH  = 32
LR     = 0.001  # Düşük LR → daha kararlı val loss
HIDDEN = 32
LAYERS = 2
EARLY_STOP_PATIENCE   = 10
LR_SCHEDULER_PATIENCE = 5

# AgroNext grafik renkleri
LIME = "#4E8A2E"; DARK = "#2E5E1E"; AMBER = "#B5830A"; GRID = "#dde7da"


# ---------------------------------------------------------------------------
# Model mimarisi
# ---------------------------------------------------------------------------

class GRUNet(nn.Module):
    """Sulama ihtiyacını tahmin eden GRU sınıflandırıcı."""
    def __init__(self, n_features, hidden=HIDDEN, layers=LAYERS):
        super().__init__()
        self.gru = nn.GRU(
            n_features, hidden, layers,
            batch_first=True,
            dropout=0.2 if layers > 1 else 0,
        )
        self.fc = nn.Sequential(
            nn.Linear(hidden, 16), nn.ReLU(),
            nn.Linear(16, 1),
        )

    def forward(self, x):
        out, _ = self.gru(x)
        return self.fc(out[:, -1, :]).squeeze(-1)


# ---------------------------------------------------------------------------
# Veri hazırlama
# ---------------------------------------------------------------------------

def ozellik_ekle(df: pd.DataFrame) -> pd.DataFrame:
    """Türetilmiş özellikleri DataFrame'e ekle."""
    df = df.copy()

    # Zaman döngüsel kodlama (gündüz/gece örüntüsü için kritik)
    saat = df["timestamp"].dt.hour + df["timestamp"].dt.minute / 60.0
    df["hour_sin"] = np.sin(2 * np.pi * saat / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * saat / 24.0)

    # VPD: buhar basıncı açığı (kPa) — BME680 sıcaklık + nemden
    # es = doyma buhar basıncı, ea = gerçek buhar basıncı
    temp = df["temp"].values
    hum  = df["humidity"].values
    es   = 0.6108 * np.exp(17.27 * temp / (temp + 237.3))
    ea   = es * (hum / 100.0)
    df["vpd"] = np.clip(es - ea, 0, None)

    # pH kolonu varsa NaN → sütun ortalamasıyla doldur (opsiyonel sensör)
    if "ph" in df.columns and df["ph"].isna().any():
        df["ph"] = df["ph"].fillna(df["ph"].mean())

    return df


def istatistik_cikar(veri_ham: np.ndarray) -> dict:
    """Eğitim verisinin özellik bazında istatistiklerini çıkar.

    Bu istatistikler checkpoint'e kaydedilir; kopru.py ve asistan.py canlı
    sensör verisini "model bu aralıkta mı eğitildi?" diye KIYASLAMAK için
    kullanır (eğitim aralığı dışındaki veride tahmine temkinli yaklaşılır).
    """
    ist = {}
    for i, ad in enumerate(OZELLIKLER):
        kolon = veri_ham[:, i].astype(np.float64)
        ist[ad] = {
            "min":  float(np.min(kolon)),
            "max":  float(np.max(kolon)),
            "mean": float(np.mean(kolon)),
            "std":  float(np.std(kolon)),
            "p05":  float(np.percentile(kolon, 5)),   # %90 güven aralığı alt sınırı
            "p95":  float(np.percentile(kolon, 95)),  # %90 güven aralığı üst sınırı
        }
    return ist


def veri_hazirla(csv_yolu: str):
    """CSV oku, özellikleri türet, zaman-sıralı böl, scaler'ı train'e fit et.

    Dönüş:
        Xtr, ytr, Xval, yval, Xte, yte — numpy float32 dizileri
        scaler                          — train'e fit edilmiş StandardScaler
        istatistikler                   — eğitim verisi özet istatistikleri
        n                               — toplam satır sayısı
    """
    df = pd.read_csv(csv_yolu, parse_dates=["timestamp"])
    df = ozellik_ekle(df)
    df = df.dropna(subset=OZELLIKLER).reset_index(drop=True)

    veri_ham = df[OZELLIKLER].values.astype(np.float32)
    pump     = df["pump"].values
    n        = len(veri_ham)

    # Scaler yalnız train kısmına fit edilir — val/test sızıntısı önlenir
    n_raw_tr = int(n * 0.60)
    scaler   = StandardScaler()
    scaler.fit(veri_ham[:n_raw_tr])
    veri_norm = scaler.transform(veri_ham)

    # Eğitim kısmının istatistikleri (canlı veri kıyası için checkpoint'e gider)
    istatistikler = istatistik_cikar(veri_ham[:n_raw_tr])

    # Tüm sliding-window örnekleri oluştur
    X_all, y_all = [], []
    for i in range(n - PENCERE - UFUK):
        X_all.append(veri_norm[i : i + PENCERE])
        # Hedef: sonraki UFUK adım içinde sulama olacak mı?
        gelecek_pump = pump[i + PENCERE : i + PENCERE + UFUK].max()
        y_all.append(1.0 if gelecek_pump > 0 else 0.0)

    X_all = np.array(X_all, dtype=np.float32)
    y_all = np.array(y_all, dtype=np.float32)
    n_wins = len(X_all)

    # Zaman-sıralı bölme — karıştırma YOK (stratify kaldırıldı)
    n_tr  = int(n_wins * 0.60)
    n_val = int(n_wins * 0.20)

    Xtr,  ytr  = X_all[:n_tr],            y_all[:n_tr]
    Xval, yval = X_all[n_tr:n_tr+n_val],  y_all[n_tr:n_tr+n_val]
    Xte,  yte  = X_all[n_tr+n_val:],      y_all[n_tr+n_val:]

    return Xtr, ytr, Xval, yval, Xte, yte, scaler, istatistikler, n


# ---------------------------------------------------------------------------
# Grafik fonksiyonları
# ---------------------------------------------------------------------------

def grafik_loss(history: dict, best_epoch: int):
    plt.figure(figsize=(9, 4.5))
    ep = range(1, len(history["tr"]) + 1)
    plt.plot(ep, history["tr"],  color=LIME,  lw=2.2, label="Eğitim kaybı")
    plt.plot(ep, history["val"], color=AMBER, lw=2.2, label="Doğrulama kaybı")
    # En iyi epoch için noktalı dikey çizgi
    if best_epoch:
        plt.axvline(best_epoch, color=DARK, linestyle="--", alpha=0.75,
                    label=f"En iyi epoch ({best_epoch})")
    plt.title("GRU Model Eğitim Süreci", fontsize=13, fontweight="bold", color=DARK)
    plt.xlabel("Epoch"); plt.ylabel("Kayıp (BCE Loss)")
    plt.grid(True, color=GRID); plt.legend(); plt.tight_layout()
    plt.savefig("grafik_egitim_kaybi.png", dpi=140)
    plt.close()


def grafik_acc(history: dict):
    plt.figure(figsize=(8, 4.5))
    ep = range(1, len(history["tr_acc"]) + 1)
    plt.plot(ep, [a * 100 for a in history["tr_acc"]],  color=LIME,  lw=2.2, label="Eğitim doğruluğu")
    plt.plot(ep, [a * 100 for a in history["val_acc"]], color=AMBER, lw=2.2, label="Doğrulama doğruluğu")
    plt.title("Model Doğruluğu (Epoch Bazında)", fontsize=13, fontweight="bold", color=DARK)
    plt.xlabel("Epoch"); plt.ylabel("Doğruluk (%)")
    plt.ylim(0, 105); plt.grid(True, color=GRID); plt.legend(); plt.tight_layout()
    plt.savefig("grafik_dogruluk.png", dpi=140)
    plt.close()


def grafik_confusion(cm: np.ndarray):
    plt.figure(figsize=(5.2, 4.6))
    plt.imshow(cm, cmap="Greens")
    etiket = ["Sulama Yok", "Sulama Gerekli"]
    plt.xticks([0, 1], etiket); plt.yticks([0, 1], etiket)
    plt.xlabel("Tahmin"); plt.ylabel("Gerçek")
    plt.title("Karmaşıklık Matrisi", fontsize=13, fontweight="bold", color=DARK)
    for i in range(2):
        for j in range(2):
            renk = "white" if cm[i, j] > cm.max() / 2 else DARK
            plt.text(j, i, str(cm[i, j]), ha="center", va="center",
                     fontsize=16, fontweight="bold", color=renk)
    plt.tight_layout()
    plt.savefig("grafik_karmasiklik_matrisi.png", dpi=140)
    plt.close()


def grafik_pr(yte: np.ndarray, te_prob: np.ndarray) -> float:
    """Precision-Recall eğrisi (dengesiz sınıflandırmada ROC'tan daha bilgilendirici)."""
    prec_vals, rec_vals, _ = precision_recall_curve(yte, te_prob)
    pr_auc = sklearn_auc(rec_vals, prec_vals)

    plt.figure(figsize=(7, 5))
    plt.plot(rec_vals, prec_vals, color=LIME, lw=2.2)
    plt.axvline(0.5, color=GRID, linestyle=":", alpha=0.7, label="Recall=0.5")
    plt.xlabel("Recall (Duyarlılık)"); plt.ylabel("Precision (Kesinlik)")
    plt.title(f"Precision-Recall Eğrisi  (AUC = {pr_auc:.3f})",
              fontsize=13, fontweight="bold", color=DARK)
    plt.grid(True, color=GRID); plt.legend(); plt.tight_layout()
    plt.savefig("grafik_pr_egrisi.png", dpi=140)
    plt.close()
    return pr_auc


# ---------------------------------------------------------------------------
# Ana eğitim döngüsü
# ---------------------------------------------------------------------------

def egit_ve_kaydet(csv_yolu: str, model_dosya: str = "agronext_gru_model.pt") -> dict:
    """Modeli verilen CSV ile eğit, grafikleri ve checkpoint'i kaydet.

    Hem komut satırından (main) hem de yeniden_egit.py'den çağrılır —
    böylece "kendini eğitme" hattı aynı kodu kullanır, kopya yok.

    Dönüş: test metrikleri sözlüğü (yeniden_egit.py kayıt tutar).
    """
    print(f"Veri: {csv_yolu}")
    print(f"Ozellikler ({len(OZELLIKLER)}): {OZELLIKLER}\n")

    Xtr, ytr, Xval, yval, Xte, yte, scaler, istatistikler, n_satir = veri_hazirla(csv_yolu)
    print(f"Train: {len(Xtr)} | Val: {len(Xval)} | Test: {len(Xte)}")
    print(f"Sulama orani — Train: {ytr.mean()*100:.1f}% | Val: {yval.mean()*100:.1f}% | Test: {yte.mean()*100:.1f}%")

    tr_loader = DataLoader(
        TensorDataset(torch.tensor(Xtr), torch.tensor(ytr)),
        batch_size=BATCH, shuffle=True,
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model  = GRUNet(n_features=len(OZELLIKLER)).to(device)

    # pos_weight: dengesiz veri için negatif/pozitif oranı (sqrt olmadan — daha agresif)
    poz      = max(1.0, (len(ytr) - float(ytr.sum())) / max(1.0, float(ytr.sum())))
    loss_fn  = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(poz, device=device))
    opt      = torch.optim.Adam(model.parameters(), lr=LR)

    # LR Scheduler: val_loss iyileşmeyince öğrenme hızını yarıya indir
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        opt, mode="min", patience=LR_SCHEDULER_PATIENCE, factor=0.5,
    )

    Xval_t = torch.tensor(Xval).to(device)
    yval_t = torch.tensor(yval).to(device)
    history = {"tr": [], "val": [], "tr_acc": [], "val_acc": []}

    # EarlyStopping — PyTorch'ta manuel uygulanır
    best_val_loss   = float("inf")
    best_model_state = None
    best_epoch       = 0
    patience_counter = 0

    print(f"\nEgitim basliyor ({device}) — max {EPOCH} epoch, "
          f"early_stop={EARLY_STOP_PATIENCE}, lr_scheduler={LR_SCHEDULER_PATIENCE}...")

    for ep in range(1, EPOCH + 1):
        # ---- Eğitim adımı ----
        model.train()
        toplam = 0.0
        for xb, yb in tr_loader:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            opt.step()
            toplam += loss.item()
        tr_loss = toplam / len(tr_loader)

        # ---- Doğrulama adımı ----
        model.eval()
        with torch.no_grad():
            val_out  = model(Xval_t)
            val_loss = loss_fn(val_out, yval_t).item()
            val_pred = (torch.sigmoid(val_out) > ESIK).float()
            val_acc  = (val_pred == yval_t).float().mean().item()
            tr_out   = model(torch.tensor(Xtr).to(device))
            tr_acc   = (
                (torch.sigmoid(tr_out) > ESIK).float()
                == torch.tensor(ytr).to(device)
            ).float().mean().item()

        # LR güncelle
        scheduler.step(val_loss)
        history["tr"].append(tr_loss)
        history["val"].append(val_loss)
        history["tr_acc"].append(tr_acc)
        history["val_acc"].append(val_acc)

        if ep % 5 == 0 or ep == 1:
            lr_now = opt.param_groups[0]["lr"]
            print(f"  Epoch {ep:3d}/{EPOCH}  tr_loss={tr_loss:.4f}  "
                  f"val_loss={val_loss:.4f}  val_acc={val_acc*100:.1f}%  lr={lr_now:.6f}")

        # EarlyStopping: val_loss iyileştiyse en iyi modeli kaydet
        if val_loss < best_val_loss - 1e-5:
            best_val_loss    = val_loss
            best_model_state = copy.deepcopy(model.state_dict())
            best_epoch       = ep
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= EARLY_STOP_PATIENCE:
                print(f"\n  EarlyStopping: epoch {ep}'de durdu "
                      f"(patience={EARLY_STOP_PATIENCE})")
                break

    # En iyi epoch ağırlıklarını geri yükle
    if best_model_state is not None:
        model.load_state_dict(best_model_state)
    print(f"  → En iyi epoch: {best_epoch}  (val_loss={best_val_loss:.4f})")

    # ---- Test değerlendirmesi ----
    model.eval()
    with torch.no_grad():
        te_prob = torch.sigmoid(
            model(torch.tensor(Xte).to(device))
        ).cpu().numpy()

    te_pred = (te_prob > ESIK).astype(int)
    acc  = (te_pred == yte).mean()
    prec, rec, f1, _ = precision_recall_fscore_support(
        yte, te_pred, average="binary", zero_division=0
    )
    cm = confusion_matrix(yte, te_pred, labels=[0, 1])

    auc_roc = 0.0
    if len(np.unique(yte)) > 1:  # Her iki sınıf da varsa hesapla
        auc_roc = roc_auc_score(yte, te_prob)

    print("\n=== TEST SONUÇLARI ===")
    print(f"  Dogruluk          : {acc*100:.1f}%")
    print(f"  Precision         : {prec*100:.1f}%")
    print(f"  Recall            : {rec*100:.1f}%  <- ONEMLI")
    print(f"  F1 Skoru          : {f1*100:.1f}%")
    print(f"  AUC-ROC           : {auc_roc:.3f}")
    print(f"  Esik              : {ESIK}")
    print(f"  En iyi epoch      : {best_epoch} (val_loss={best_val_loss:.4f})")
    print(f"  Ozellik sayisi    : {len(OZELLIKLER)} (sensorler: BME680+toprak+MQ135+lux+zaman)")

    # ---- Grafikler ----
    grafik_loss(history, best_epoch)
    grafik_acc(history)
    grafik_confusion(cm)
    pr_auc = grafik_pr(yte, te_prob)
    print(f"\nGrafikler kaydedildi:")
    print(f"  grafik_egitim_kaybi.png, grafik_dogruluk.png,")
    print(f"  grafik_karmasiklik_matrisi.png, grafik_pr_egrisi.png")
    print(f"  PR AUC: {pr_auc:.3f}")

    # ---- Model kaydet ----
    egitim_zamani = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    torch.save(
        {
            "model":        model.state_dict(),
            "scaler_mean":  scaler.mean_,
            "scaler_scale": scaler.scale_,
            "pencere":      PENCERE,
            "ufuk":         UFUK,
            "ozellikler":   OZELLIKLER,
            "hidden":       HIDDEN,
            "layers":       LAYERS,
            "threshold":    ESIK,          # api.py bu eşiği okur
            "best_epoch":   best_epoch,
            "val_loss":     best_val_loss,
            "sensor_list":  "BME680+toprak+MQ135+lux",
            # --- Kendini eğitme + canlı veri kıyası için meta bilgiler ---
            "istatistikler":  istatistikler,   # özellik bazında min/max/mean/std/p05/p95
            "egitim_zamani":  egitim_zamani,
            "egitim_verisi":  csv_yolu,
            "veri_satir":     n_satir,
            "model_surum":    f"GRU-{PENCERE}x{len(OZELLIKLER)}-{datetime.now().strftime('%Y%m%d%H%M')}",
        },
        model_dosya,
    )
    print(f"\nModel kaydedildi: {model_dosya}  ->  tahmin.py & api.py & kopru.py kullanir")

    return {
        "dogruluk":  float(acc),
        "precision": float(prec),
        "recall":    float(rec),
        "f1":        float(f1),
        "auc_roc":   float(auc_roc),
        "pr_auc":    float(pr_auc),
        "best_epoch": int(best_epoch),
        "veri_satir": int(n_satir),
        "egitim_zamani": egitim_zamani,
        "egitim_verisi": csv_yolu,
    }


def main():
    csv_yolu = sys.argv[1] if len(sys.argv) > 1 else "simule_sera_verileri.csv"
    egit_ve_kaydet(csv_yolu)


if __name__ == "__main__":
    main()
