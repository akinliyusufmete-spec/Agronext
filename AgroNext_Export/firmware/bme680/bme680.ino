/*
 * AgroNext ESP32 — v3.0
 * =====================================================
 * Web Sunucu + TFT Ekran + Buton Navigasyon + AI Köprüsü
 *
 * v2.0 Özellikleri (korunanlar):
 *   ✓ BME680 hata tespiti + otomatik yeniden bağlanma
 *   ✓ 5 örnekli hareketli ortalama (analog gürültü azaltma)
 *   ✓ MQ-135 ısınma zamanlayıcısı (3 dakika)
 *   ✓ WiFi otomatik yeniden bağlanma
 *   ✓ Önbellekli sensör okuma (her web isteğinde çift okuma yok)
 *   ✓ Renk kodlu TFT dashboard
 *   ✓ Sensör durum şeridi (bağlı/hata göstergesi)
 *   ✓ Çalışma süresi (uptime) gösterimi
 *
 * v3.0 Yenilikleri (AgroNext AI entegrasyonu):
 *   ✓ GET /oku    → AgroNext uyumlu JSON şeması (kopru.py okur)
 *   ✓ POST /ai    → Laptopdaki AI özeti alır + röle günceller
 *   ✓ GET /ai_durum → ESP32 web sitesi için AI durumu
 *   ✓ Röle kontrolü (GPIO13, AKTİF LOW) — AI kararına göre
 *   ✓ TFT Sayfa 4: AI durumu + tahmin + pompa gösterimi
 *   ✓ Güvenlik zaman aşımı: 60 sn AI gelmezse pompa kapanır
 *
 * Pin Bağlantıları:
 *   BME680      → SDA=GPIO21, SCL=GPIO22, VCC=3V3
 *   Toprak Nemi → AOUT=GPIO36, VCC=3V3
 *   pH          → PO=GPIO35, VCC=5V
 *   MQ-135      → AO→10kΩ→GPIO34→10kΩ→GND, VCC=5V
 *   TFT ST7735  → CS=5, DC=16, RST=17, MOSI=23, SCK=18
 *   Yeşil Buton → GPIO25 + GND
 *   Mavi Buton  → GPIO26 + GND
 *   Röle        → IN1=GPIO13, VCC=5V (AKTİF LOW: LOW=açık, HIGH=kapalı)
 */

#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_Sensor.h>
#include "Adafruit_BME680.h"
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>

// ---- WiFi ----
const char* SSID     = "BK_Mdm";
const char* PASSWORD = "Qq123456!@";

// ---- Pin Tanımları ----
#define SOIL_PIN   36
#define SOIL_DRY   2830
#define SOIL_WET   1150
#define MQ135_PIN  34
#define PH_PIN     35
#define TFT_CS      5
#define TFT_DC     16
#define TFT_RST    17
#define BTN_YESIL  25
#define BTN_MAVI   26
#define ROLE_PIN   13    // Röle: AKTİF LOW (LOW=açık, HIGH=kapalı)

// ---- Sayfa Yönetimi ----
#define SAYFA_SAYISI 4
int sayfaNo = 0;
unsigned long sonBasYesil = 0, sonBasMavi = 0;
#define DEBOUNCE_MS 250

// ---- Nesneler ----
Adafruit_BME680 bme;
WebServer       server(80);
Adafruit_ST7735 tft = Adafruit_ST7735(TFT_CS, TFT_DC, TFT_RST);

// ---- Sensör Durumu ----
bool bmeOk   = false;
unsigned long bootMs = 0;
#define MQ_ISINMA_MS 180000UL   // 3 dakika ısınma

// ---- Hareketli Ortalama (5 örnek, analog gürültü azaltır) ----
#define AVG_N 5
int soilBuf[AVG_N] = {2830,2830,2830,2830,2830}; // başlangıç: kuru
int phBuf[AVG_N]   = {0,0,0,0,0};
int mqBuf[AVG_N]   = {0,0,0,0,0};
int bufIdx = 0;

int ortalama(int* buf) {
  long s = 0;
  for (int i = 0; i < AVG_N; i++) s += buf[i];
  return (int)(s / AVG_N);
}

// ---- pH Kalibrasyon Sabitleri ----
const float PH_HAM_4 = 2606.0f;   // pH 4.01 buffer → 2.10V → ADC kalibrasyon
const float PH_HAM_7 = 2048.0f;   // pH 7.00 buffer → 1.65V → ADC kalibrasyon

// ---- Önbellekli Sensör Değerleri ----
float g_temp  = 0, g_hum  = 0, g_press = 0;
long  g_gas   = 0;
int   g_soil  = 0, g_soilHam = 0;
float g_ph    = 7.0f;
int   g_phHam = 0;
int   g_mq135 = 0, g_co2ppm = 400;

// ---- AI + Pompa ----
String        aiJson     = "";
String        beyinIp    = "";
unsigned long aiZaman    = 0;
float         aiOlasilik = 0.0f;
int           g_pump     = 0;

// ---- TFT Renkler ----
#define C_YESIL   0x07E0
#define C_BEYAZ   0xFFFF
#define C_SIYAH   0x0000
#define C_SARI    0xFFE0
#define C_CAMGOBE 0x07FF
#define C_MOR     0xF81F
#define C_TURUNCU 0xFC60
#define C_GRI     0x7BEF
#define C_KIRMIZI 0xF800
#define C_LACIVERT 0x000F
#define C_PEMBE   0xF8BB
#define C_ACIK_YSL 0xAFE5

// =======================================================
// HTML DASHBOARD — v3.0 (AgroNext AI web sitesi)
// =======================================================
#include "webpage.h"

// =======================================================
// TFT YARDIMCI FONKSİYONLAR
// =======================================================

void tftSatir(int y, uint16_t renkEtiket, const char* etiket, String deger, const char* birim) {
  tft.fillRect(0, y, 160, 14, C_SIYAH);
  tft.setTextColor(renkEtiket); tft.setTextSize(1);
  tft.setCursor(4, y+4); tft.print(etiket);
  tft.setTextColor(C_BEYAZ);
  tft.setCursor(80, y+4); tft.print(deger);
  tft.setTextColor(0x7BEF); tft.print(" "); tft.print(birim);
}

void tftSayfaBaslik(int sayfa, const char* baslik) {
  tft.fillScreen(C_SIYAH);
  tft.fillRect(0, 0, 160, 20, 0x0260);
  tft.drawFastHLine(0, 20, 160, C_YESIL);
  tft.setTextColor(C_YESIL); tft.setTextSize(1);
  tft.setCursor(6, 7); tft.print(baslik);
  tft.setTextColor(0x8410);
  tft.setCursor(135, 7);
  tft.print(sayfa+1); tft.print("/"); tft.print(SAYFA_SAYISI);
  tft.fillRect(0, 120, 160, 8, 0x0120);
  tft.setTextColor(0x4208); tft.setTextSize(1);
  tft.setCursor(10, 121); tft.print("< MAVI");
  tft.setCursor(95, 121); tft.print("YESIL >");
}

void tftBaglaniyor(int nokta) {
  tft.fillScreen(C_SIYAH);
  tft.fillRect(0, 0, 160, 20, 0x0260);
  tft.drawFastHLine(0, 20, 160, C_YESIL);
  tft.setTextColor(C_YESIL); tft.setTextSize(1);
  tft.setCursor(40, 7); tft.print("AgroNext");
  tft.setTextColor(C_GRI); tft.setCursor(4, 32); tft.print("WiFi baglaniyor...");
  tft.setTextColor(0x07E0); tft.setCursor(4, 48);
  for (int i = 0; i < nokta % 8; i++) tft.print(". ");
}

void tftBaglandi(String ip) {
  tft.fillScreen(C_SIYAH);
  tft.fillRect(0, 0, 160, 20, 0x0260);
  tft.drawFastHLine(0, 20, 160, C_YESIL);
  tft.setTextColor(C_YESIL); tft.setTextSize(1);
  tft.setCursor(40, 7); tft.print("AgroNext");
  tft.setTextSize(2); tft.setTextColor(C_YESIL);
  tft.setCursor(12, 30); tft.print("BAGLANDI!");
  tft.setTextSize(1);
  tft.setTextColor(C_CAMGOBE); tft.setCursor(4, 58); tft.print("http://");
  tft.setCursor(4, 70); tft.print(ip);
  tft.setTextColor(C_GRI); tft.setCursor(4, 86); tft.print("Tarayicidan ac ^");
}

// ---- SAYFA 1: Hava (BME680) ----
void tftSayfa1() {
  tftSayfaBaslik(0, "Hava Durumu");

  if (bmeOk) {
    tft.fillRect(0, 22, 160, 26, 0x0120);
    tft.setTextColor(C_SARI); tft.setTextSize(1);
    tft.setCursor(6, 26); tft.print("SICAKLIK");
    tft.setTextSize(2); tft.setTextColor(C_BEYAZ);
    tft.setCursor(78, 24); tft.print(String(g_temp, 1));
    tft.setTextSize(1); tft.setTextColor(C_SARI);
    tft.setCursor(148, 24); tft.print("C");

    tft.drawFastHLine(0, 48, 160, 0x2945);

    tftSatir(50,  C_CAMGOBE, "Nem      ", String(g_hum, 0),   "%");
    tftSatir(65,  0xA534,    "Basinc   ", String(g_press, 0), "hPa");
    tftSatir(80,  0x07FF,    "Hava Kal.", String(g_gas/1000), "kOhm");

    tft.fillRect(0, 98, 160, 20, 0x0140);
    tft.setTextColor(C_YESIL); tft.setTextSize(1);
    tft.setCursor(6, 105); tft.print("BME680 OK");
    tft.setTextColor(C_GRI); tft.setCursor(90, 105);
    tft.print(String(g_temp,0)); tft.print("C ");
    tft.print(String((int)g_hum)); tft.print("%");

  } else {
    tft.fillRect(0, 22, 160, 96, 0x2000);
    tft.setTextColor(C_KIRMIZI); tft.setTextSize(1);
    tft.setCursor(4, 32); tft.print("BME680 BULUNAMADI!");
    tft.setTextColor(C_GRI);
    tft.setCursor(4, 50); tft.print("SDA -> GPIO21");
    tft.setCursor(4, 62); tft.print("SCL -> GPIO22");
    tft.setCursor(4, 74); tft.print("VCC -> 3.3V");
    tft.setCursor(4, 86); tft.print("CS  -> 3.3V");
  }
}

// ---- SAYFA 2: Toprak & Kimya ----
void tftSayfa2() {
  tftSayfaBaslik(1, "Toprak & Kimya");

  bool soilOk = (g_soilHam > 100 && g_soilHam < 3600);
  tft.fillRect(0, 22, 160, 26, 0x0120);
  tft.setTextColor(C_YESIL); tft.setTextSize(1);
  tft.setCursor(6, 26); tft.print("TOPRAK");
  if (soilOk) {
    tft.setTextSize(2); tft.setTextColor(C_BEYAZ);
    tft.setCursor(68, 24); tft.print(String(g_soil));
    tft.setTextSize(1); tft.setTextColor(C_YESIL);
    tft.setCursor(114, 24); tft.print("%");
    int barW = map(g_soil, 0, 100, 0, 148);
    tft.fillRect(6, 44, 148, 3, 0x2945);
    uint16_t barRenk = (g_soil < 30) ? C_KIRMIZI : (g_soil < 50 ? C_SARI : C_YESIL);
    tft.fillRect(6, 44, barW, 3, barRenk);
  } else {
    tft.setTextColor(C_KIRMIZI); tft.setCursor(68, 28); tft.print("---");
  }

  tft.drawFastHLine(0, 50, 160, 0x2945);

  String phStr = (g_phHam > 200) ? String(g_ph, 1) : "---";
  tftSatir(52, C_MOR, "pH       ", phStr, "");

  tftSatir(67, C_TURUNCU, "MQ-135   ", String(g_mq135), "ham");

  bool mqHazir = (millis() - bootMs > MQ_ISINMA_MS) && g_mq135 > 5;
  tft.fillRect(0, 83, 160, 13, C_SIYAH);
  if (!mqHazir) {
    unsigned long kalan = (MQ_ISINMA_MS - (millis() - bootMs)) / 1000;
    if (kalan > MQ_ISINMA_MS/1000) kalan = 0;
    tft.setTextColor(C_TURUNCU); tft.setTextSize(1);
    tft.setCursor(4, 86); tft.print("MQ isinıyor: ");
    tft.print(kalan); tft.print("sn");
  } else {
    tftSatir(83, 0xFF60, "CO2 ~    ", String(g_co2ppm), "ppm");
  }
}

// ---- SAYFA 3: Sistem Bilgisi ----
void tftSayfa3() {
  tftSayfaBaslik(2, "Sistem");

  bool bagliMi = (WiFi.status() == WL_CONNECTED);

  tft.fillRect(0, 22, 160, 20, bagliMi ? 0x0140 : 0x2000);
  tft.setTextColor(bagliMi ? C_YESIL : C_KIRMIZI);
  tft.setTextSize(1); tft.setCursor(6, 29);
  tft.print(bagliMi ? "WiFi: BAGLI" : "WiFi: KOPUK!");

  tft.setTextColor(C_GRI); tft.setCursor(100, 29);
  if (bagliMi) tft.print(":80");

  tft.drawFastHLine(0, 43, 160, 0x2945);

  tft.setTextColor(C_CAMGOBE); tft.setTextSize(1);
  tft.setCursor(4, 48); tft.print("IP:");
  tft.setTextColor(C_BEYAZ);
  tft.setCursor(28, 48); tft.print(WiFi.localIP().toString());

  unsigned long sn = millis() / 1000;
  int ss = sn%60, dk=(sn/60)%60, sa=sn/3600;
  tft.setTextColor(C_GRI); tft.setCursor(4, 63); tft.print("Sure:");
  tft.setTextColor(C_BEYAZ); tft.setCursor(44, 63);
  if (sa > 0) { tft.print(sa); tft.print("sa "); }
  tft.print(dk); tft.print("dk "); tft.print(ss); tft.print("sn");

  tft.setTextColor(C_GRI); tft.setCursor(4, 78); tft.print("Web :  ");
  tft.setTextColor(C_YESIL); tft.print("Aktif - :80");

  tft.drawFastHLine(0, 91, 160, 0x2945);
  tft.setTextColor(C_GRI); tft.setCursor(4, 96); tft.print("Toprak:");
  tft.setTextColor(C_YESIL); tft.setCursor(56, 96);
  tft.print(g_soil); tft.print("%");
  tft.setTextColor(C_GRI); tft.setCursor(90, 96); tft.print("MQ:");
  tft.setTextColor(C_TURUNCU); tft.print(g_mq135);
}

// ---- SAYFA 4: Yapay Zeka Durumu ----
void tftSayfa4() {
  tftSayfaBaslik(3, "Yapay Zeka");

  bool aiVar   = (aiJson.length() > 0);
  bool aiTaze  = aiVar && ((millis() - aiZaman) < 60000UL);
  bool sulama  = aiTaze && (aiJson.indexOf("GEREKLI") >= 0);

  // AI bağlantı kutusu
  tft.fillRect(0, 22, 160, 20, aiTaze ? 0x0140 : 0x2945);
  tft.setTextColor(aiTaze ? C_YESIL : C_GRI);
  tft.setTextSize(1); tft.setCursor(6, 29);
  if (aiTaze)      tft.print("AI Bagli");
  else if (aiVar)  tft.print("AI Zaman Asimi");
  else             tft.print("AI Bekleniyor...");

  tft.drawFastHLine(0, 43, 160, 0x2945);

  if (aiTaze) {
    // Tahmin yüzdesi + karar
    tft.setTextColor(C_GRI); tft.setTextSize(1);
    tft.setCursor(4, 49); tft.print("Tahmin:");
    tft.setTextColor(sulama ? C_SARI : C_YESIL);
    tft.setCursor(60, 49);
    tft.print("%");
    tft.print((int)(aiOlasilik * 100));
    tft.print(sulama ? " SULAMA" : " Normal");

    // Pompa durumu
    tft.setTextColor(C_GRI); tft.setCursor(4, 63); tft.print("Pompa:");
    tft.setTextColor(g_pump ? C_CAMGOBE : C_BEYAZ);
    tft.setCursor(60, 63);
    tft.print(g_pump ? "ACIK" : "KAPALI");

    // Beyin IP
    tft.setTextColor(C_GRI); tft.setCursor(4, 77); tft.print("Beyin:");
    tft.setTextColor(C_CAMGOBE); tft.setCursor(48, 77);
    tft.print(beyinIp.length() > 0 ? beyinIp : "---");

    // Yaş
    unsigned long yas = (millis() - aiZaman) / 1000;
    tft.setTextColor(C_GRI); tft.setCursor(4, 91); tft.print("Son:");
    tft.setTextColor(C_BEYAZ); tft.setCursor(36, 91);
    tft.print(yas); tft.print("sn once");

  } else {
    // Bilgi: AI beyni nerede başlatılır
    tft.setTextColor(0x4208); tft.setTextSize(1);
    tft.setCursor(4, 52); tft.print("Laptoptan calistir:");
    tft.setTextColor(C_GRI);
    tft.setCursor(4, 64); tft.print("kopru.py + api.py");
    tft.setCursor(4, 76); tft.print("ayni WiFi aginda");
  }
}

void sayfayiGoster() {
  switch (sayfaNo) {
    case 0: tftSayfa1(); break;
    case 1: tftSayfa2(); break;
    case 2: tftSayfa3(); break;
    case 3: tftSayfa4(); break;
  }
}

// =======================================================
// MQ-135 → CO2 PPM
// =======================================================
int mq135Ppm(int ham) {
  if (ham < 10) return 0;
  float vAo   = (ham * 3.3f / 4095.0f) * 2.0f;
  if (vAo < 0.05f) return 400;
  float rs    = ((5.0f - vAo) / vAo) * 10000.0f;
  float ratio = rs / 76630.0f;
  int   ppm   = (int)(110.47f * pow(ratio, -2.862f));
  return constrain(ppm, 400, 10000);
}

// =======================================================
// BME680 YAPILANDIRMA
// =======================================================
bool bmeBaslat() {
  if (!bme.begin(0x77) && !bme.begin(0x76)) return false;
  bme.setTemperatureOversampling(BME680_OS_8X);
  bme.setHumidityOversampling(BME680_OS_2X);
  bme.setPressureOversampling(BME680_OS_4X);
  bme.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme.setGasHeater(320, 150);
  return true;
}

// =======================================================
// SENSÖR OKUMA
// =======================================================
void sensorlariOku() {
  if (bmeOk) {
    if (bme.performReading()) {
      g_temp  = bme.temperature;
      g_hum   = bme.humidity;
      g_press = bme.pressure / 100.0f;
      g_gas   = bme.gas_resistance;
    }
  } else {
    static unsigned long sonRetry = 0;
    if (millis() - sonRetry > 10000) {
      sonRetry = millis();
      bmeOk = bmeBaslat();
      if (bmeOk) Serial.println("BME680 yeniden baglandi!");
    }
  }

  soilBuf[bufIdx] = analogRead(SOIL_PIN);
  phBuf[bufIdx]   = analogRead(PH_PIN);
  mqBuf[bufIdx]   = analogRead(MQ135_PIN);
  bufIdx = (bufIdx + 1) % AVG_N;

  g_soilHam = ortalama(soilBuf);
  g_phHam   = ortalama(phBuf);
  g_mq135   = ortalama(mqBuf);

  g_soil = constrain(map(g_soilHam, SOIL_DRY, SOIL_WET, 0, 100), 0, 100);

  float egim = (7.0f - 4.0f) / (PH_HAM_7 - PH_HAM_4);
  g_ph = constrain(4.0f + (g_phHam - PH_HAM_4) * egim, 0.0f, 14.0f);

  g_co2ppm = mq135Ppm(g_mq135);
}

// =======================================================
// RÖLE + POMPA KONTROLÜ
// AI gelmezse veya 60 saniye geçerse pompa kapanır (güvenlik)
// =======================================================
void roleAyarla() {
  bool pompaAc = (aiJson.length() > 0)
              && ((millis() - aiZaman) < 60000UL)
              && (aiJson.indexOf("GEREKLI") >= 0);
  g_pump = pompaAc ? 1 : 0;
  // AKTİF LOW: LOW = röle açık (sulama çalışıyor)
  digitalWrite(ROLE_PIN, pompaAc ? LOW : HIGH);
}

// =======================================================
// BUTON KONTROLÜ
// =======================================================
void butonKontrol() {
  unsigned long simdi = millis();
  if (digitalRead(BTN_YESIL) == LOW && simdi - sonBasYesil > DEBOUNCE_MS) {
    sonBasYesil = simdi;
    sayfaNo = (sayfaNo + 1) % SAYFA_SAYISI;
    sayfayiGoster();
  }
  if (digitalRead(BTN_MAVI) == LOW && simdi - sonBasMavi > DEBOUNCE_MS) {
    sonBasMavi = simdi;
    sayfaNo = (sayfaNo - 1 + SAYFA_SAYISI) % SAYFA_SAYISI;
    sayfayiGoster();
  }
}

// =======================================================
// WEB SUNUCU HANDLER'LARI
// =======================================================

// GET / → AgroNext web sitesi (AI sohbet + sensör + öneri)
void handleRoot() {
  server.send_P(200, "text/html", INDEX_HTML);
}

// GET /data → Geriye dönük uyumluluk (eski API)
void handleData() {
  bool mqHazir = (millis() - bootMs > MQ_ISINMA_MS) && g_mq135 > 5;

  String json = "{";
  json += "\"bme_ok\":"    + String(bmeOk ? "true" : "false") + ",";
  json += "\"temp\":"      + String(g_temp, 1)    + ",";
  json += "\"humidity\":"  + String(g_hum, 1)     + ",";
  json += "\"pressure\":"  + String(g_press, 1)   + ",";
  json += "\"gas\":"       + String(g_gas)         + ",";
  json += "\"soil_pct\":"  + String(g_soil)        + ",";
  json += "\"soil_ham\":"  + String(g_soilHam)     + ",";
  json += "\"ph\":"        + String(g_ph, 2)       + ",";
  json += "\"ph_ham\":"    + String(g_phHam)       + ",";
  json += "\"mq135\":"     + String(g_mq135)       + ",";
  json += "\"mq_ready\":"  + String(mqHazir ? "true" : "false") + ",";
  json += "\"co2_ppm\":"   + String(g_co2ppm)      + ",";
  json += "\"uptime_ms\":" + String(millis())       + ",";
  json += "\"ip\":\""      + WiFi.localIP().toString() + "\"";
  json += "}";

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

// GET /oku → AgroNext uyumlu şema (kopru.py + esp32_sayfa.html için)
// Şema: kopru.py'nin beklediği 10 alan + pump
// voc = gas_resistance (Ohm), co2 = co2_ppm, lux = 0 (BH1750 yok)
// ph = 0 ise kopru.py 6.5 olarak impute eder
void handleOku() {
  String json = "{";
  json += "\"temp\":"     + String(g_temp, 1)  + ",";
  json += "\"humidity\":" + String(g_hum, 1)   + ",";
  json += "\"pressure\":" + String(g_press, 1) + ",";
  json += "\"voc\":"      + String(g_gas)       + ",";
  json += "\"soil_raw\":" + String(g_soilHam)   + ",";
  json += "\"soil_pct\":" + String(g_soil)      + ",";
  json += "\"co2\":"      + String(g_co2ppm)    + ",";
  json += "\"lux\":0,";
  // ph: g_phHam < 200 ise sensör yok → 0 gönder, kopru.py 6.5 impute eder
  json += "\"ph\":"       + (g_phHam > 200 ? String(g_ph, 2) : String("0")) + ",";
  json += "\"pump\":"     + String(g_pump);
  json += "}";

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

// POST /ai → Laptopdaki kopru.py AI özetini gönderir
// Gövde: JSON string (≤2048 byte)
// Firmware remoteIP'yi beyinIp olarak kaydeder → web sitesi buradan beyinUrl öğrenir
void handleAiPost() {
  String govde = server.arg("plain");
  if (govde.length() == 0 || govde.length() > 2048) {
    server.send(400, "application/json", "{\"hata\":\"gecersiz govde\"}");
    return;
  }
  aiJson  = govde;
  beyinIp = server.client().remoteIP().toString();
  aiZaman = millis();

  // olasilik'i TFT gösterimi için çıkar (ArduinoJson olmadan basit arama)
  int idx = aiJson.indexOf("\"olasilik\":");
  if (idx >= 0) {
    aiOlasilik = aiJson.substring(idx + 11).toFloat();
  }

  roleAyarla();  // röleyi hemen güncelle

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", "{\"ok\":1}");
}

// GET /ai_durum → Web sitesi bu endpoint'i okur; AI JSON + beyin_ip + yaş döndürür
void handleAiDurum() {
  String cevap = "{\"ai\":";
  if (aiJson.length() > 0) {
    cevap += aiJson;
    cevap += ",\"beyin_ip\":\"";
    cevap += beyinIp;
    cevap += "\",\"yas_sn\":";
    cevap += String((millis() - aiZaman) / 1000);
  } else {
    cevap += "null,\"beyin_ip\":\"\",\"yas_sn\":-1";
  }
  cevap += "}";

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", cevap);
}

// =======================================================
// SETUP
// =======================================================
void setup() {
  Serial.begin(115200);
  bootMs = millis();

  Wire.begin();

  // TFT başlat
  tft.initR(INITR_BLACKTAB);
  tft.setRotation(1);
  tft.fillScreen(C_SIYAH);
  delay(200);
  tftBaglaniyor(0);
  delay(300);

  // BME680 başlat
  bmeOk = bmeBaslat();
  if (bmeOk) {
    Serial.println("BME680 hazir.");
  } else {
    Serial.println("UYARI: BME680 bulunamadi! GPIO21=SDA, GPIO22=SCL kontrol et.");
  }

  // Butonlar
  pinMode(BTN_YESIL, INPUT_PULLUP);
  pinMode(BTN_MAVI,  INPUT_PULLUP);

  // Röle: başlangıçta kapalı (AKTİF LOW → HIGH = kapalı)
  pinMode(ROLE_PIN, OUTPUT);
  digitalWrite(ROLE_PIN, HIGH);

  // Analog sensörler için ilk okumaları doldur
  for (int i = 0; i < AVG_N; i++) {
    soilBuf[i] = analogRead(SOIL_PIN);
    phBuf[i]   = analogRead(PH_PIN);
    mqBuf[i]   = analogRead(MQ135_PIN);
  }

  // WiFi bağlan
  Serial.print("WiFi baglaniyor: "); Serial.println(SSID);
  WiFi.begin(SSID, PASSWORD);
  int deneme = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500); deneme++;
    tftBaglaniyor(deneme);
    Serial.print(".");
    if (deneme > 30) {
      Serial.println("\nWiFi baglanamiyor!");
      tft.fillScreen(C_SIYAH);
      tft.setTextColor(C_KIRMIZI); tft.setCursor(4, 30); tft.print("WiFi HATA!");
      tft.setTextColor(C_BEYAZ);   tft.setCursor(4, 50); tft.print(SSID);
      break;
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    String ip = WiFi.localIP().toString();
    Serial.println("\nBaglandi! http://" + ip);
    tftBaglandi(ip);
    delay(3000);
  }

  // Web sunucu rotaları
  server.on("/",         handleRoot);
  server.on("/data",     handleData);     // geriye dönük uyumluluk
  server.on("/oku",      handleOku);      // AgroNext ana endpoint
  server.on("/ai",       HTTP_POST, handleAiPost);
  server.on("/ai_durum", handleAiDurum);
  server.begin();
  Serial.println("Web sunucu baslatildi.");
  Serial.println("  GET /oku       -> AgroNext sensor JSON");
  Serial.println("  POST /ai       -> AI ozeti al");
  Serial.println("  GET /ai_durum  -> AI durumu");

  // İlk okuma ve ekran
  sensorlariOku();
  sayfayiGoster();
}

// =======================================================
// LOOP
// =======================================================
unsigned long sonOkuma     = 0;
unsigned long sonWifiKontrol = 0;

void loop() {
  server.handleClient();
  butonKontrol();

  // Her 2 saniyede sensör oku + röle kontrolü + TFT güncelle
  if (millis() - sonOkuma >= 2000) {
    sonOkuma = millis();
    sensorlariOku();
    roleAyarla();   // 60 sn AI gelmezse pompayı güvenli kapat
    sayfayiGoster();

    // Serial debug
    Serial.printf(
      "T:%.1f H:%.0f%% P:%.0f BME:%s | Toprak:%d%%(ham:%d) pH_ham:%d MQ:%d CO2:%dppm | AI:%s Pompa:%d\n",
      g_temp, g_hum, g_press, bmeOk ? "OK" : "HATA",
      g_soil, g_soilHam, g_phHam, g_mq135, g_co2ppm,
      aiJson.length() > 0 ? "var" : "yok", g_pump
    );
  }

  // Her 30 saniyede WiFi kontrol et
  if (millis() - sonWifiKontrol >= 30000) {
    sonWifiKontrol = millis();
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi koptu, yeniden baglaniyor...");
      WiFi.begin(SSID, PASSWORD);
    }
  }
}
