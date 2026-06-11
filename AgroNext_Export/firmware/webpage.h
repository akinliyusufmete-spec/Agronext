#pragma once
#include <pgmspace.h>

// AgroNext ESP32 Web Sitesi — v3.0
// Kaynak: esp32_sayfa.html (manuel değişiklik yapma, orijinali düzenle)
// Değiştirince esp32_sayfa.html'i de güncelle (senkron kuralı).

const char INDEX_HTML[] PROGMEM = R"HTMLDOC(
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgroNext Sera</title>
<style>
  :root{
    --bg:#0a1410; --panel:#11201a; --panel2:#0e1a15; --line:#1d3328;
    --txt:#e6f0ea; --sol:#9db8aa; --yesil:#4ade80; --acik:#7ee787;
    --amber:#fbbf24; --kirmizi:#f87171; --cyan:#5cc8d8;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);
       font-family:-apple-system,system-ui,'Segoe UI',Roboto,sans-serif;
       font-size:15px;line-height:1.45;padding:14px;max-width:980px;margin:0 auto}
  h1{font-size:1.25rem;display:flex;align-items:center;gap:8px}
  .ust{display:flex;justify-content:space-between;align-items:center;
       flex-wrap:wrap;gap:8px;margin-bottom:14px}
  .rozetler{display:flex;gap:6px;flex-wrap:wrap}
  .rozet{font-size:.72rem;padding:3px 10px;border-radius:999px;
         border:1px solid var(--line);background:var(--panel)}
  .rozet.on{color:var(--yesil);border-color:#2a5e3a}
  .rozet.off{color:var(--sol)}
  .rozet.kir{color:var(--kirmizi);border-color:#5e2a2a}
  .kart{background:var(--panel);border:1px solid var(--line);
        border-radius:14px;padding:14px;margin-bottom:12px}
  .baslik{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;
          color:var(--sol);margin-bottom:8px}
  .ai-ana{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .yuzde{font-size:2.4rem;font-weight:800;min-width:110px}
  .karar{font-weight:700;font-size:1.05rem}
  .mesaj{color:var(--sol);font-size:.85rem;margin-top:2px}
  .bar{height:10px;background:var(--panel2);border-radius:99px;
       overflow:hidden;margin-top:10px;border:1px solid var(--line)}
  .bar>div{height:100%;width:0%;background:var(--yesil);
           border-radius:99px;transition:width .8s, background .8s}
  .mini{font-size:.7rem;color:var(--sol);margin-top:8px}
  .oneri{display:flex;gap:8px;padding:7px 0;border-top:1px solid var(--line);
         font-size:.86rem;align-items:baseline}
  .oneri:first-of-type{border-top:none}
  .nokta{flex:none;width:8px;height:8px;border-radius:50%;position:relative;top:1px}
  .kritik .nokta{background:var(--kirmizi)} .uyari .nokta{background:var(--amber)}
  .bilgi .nokta{background:var(--yesil)}
  .oneri b{display:block}
  .oneri span{color:var(--sol);font-size:.8rem}
  .anomali{color:var(--amber);font-size:.8rem;padding:3px 0}
  .izgara{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}
  .sens{background:var(--panel2);border:1px solid var(--line);
        border-radius:10px;padding:9px 10px}
  .sens .ad{font-size:.64rem;color:var(--sol);text-transform:uppercase;letter-spacing:.05em}
  .sens .deger{font-size:1.15rem;font-weight:700;margin-top:2px}
  .sens .deger small{font-size:.66rem;color:var(--sol);font-weight:400}
  .oy{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}
  .oy button{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
             padding:6px 14px;border-radius:9px;cursor:pointer;font-size:.85rem}
  .oy button:active{transform:scale(.96)}
  .oy .tesekkur{color:var(--yesil);font-size:.8rem}
  #sohbet{height:230px;overflow-y:auto;display:flex;flex-direction:column;
          gap:8px;padding:6px 2px;margin-bottom:10px}
  .balon{max-width:85%;padding:8px 12px;border-radius:12px;font-size:.86rem;
         white-space:pre-wrap;word-break:break-word}
  .ben{align-self:flex-end;background:#1d3a2a;border:1px solid #2a5e3a}
  .ai{align-self:flex-start;background:var(--panel2);border:1px solid var(--line)}
  .ai.bekliyor{color:var(--sol);font-style:italic}
  .yaz{display:flex;gap:8px}
  .yaz input{flex:1;background:var(--panel2);border:1px solid var(--line);
             color:var(--txt);padding:10px 12px;border-radius:10px;font-size:.9rem;outline:none}
  .yaz input:focus{border-color:#2a5e3a}
  .yaz button{background:var(--yesil);color:#06250f;border:none;font-weight:700;
              padding:0 18px;border-radius:10px;cursor:pointer;font-size:.9rem}
  .yaz button:disabled{opacity:.4}
  .cipler{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
  .cip{font-size:.72rem;border:1px solid var(--line);background:var(--panel2);
       color:var(--sol);padding:4px 10px;border-radius:999px;cursor:pointer}
  .cip:hover{color:var(--txt);border-color:#2a5e3a}
  footer{color:var(--sol);font-size:.68rem;text-align:center;margin:16px 0 6px}
</style>
</head>
<body>

<div class="ust">
  <h1>&#127807; AgroNext <span style="font-size:.7rem;color:var(--sol);font-weight:400">Akilli Sera</span></h1>
  <div class="rozetler">
    <span class="rozet off" id="rSensor">&#9203; Sensor</span>
    <span class="rozet off" id="rAi">&#9203; AI Beyni</span>
  </div>
</div>

<!-- AI KARAR KARTI -->
<div class="kart">
  <div class="baslik">Yapay Zeka Tahmini &mdash; 30 dk sonrasi</div>
  <div class="ai-ana">
    <div class="yuzde" id="aiYuzde">--%</div>
    <div style="flex:1;min-width:200px">
      <div class="karar" id="aiKarar">AI beyni bekleniyor...</div>
      <div class="mesaj" id="aiMesaj">Laptopda kopru.py + api.py calisinca tahminler burada gorunur.</div>
    </div>
  </div>
  <div class="bar"><div id="aiBar"></div></div>
  <div class="mini" id="aiMini">model: &mdash; &middot; guncelleme: &mdash;</div>
  <div class="oy">
    <span style="font-size:.8rem;color:var(--sol)">Bu oneri sence dogru mu?</span>
    <button onclick="oyVer('dogru')">&#128077; Dogru</button>
    <button onclick="oyVer('yanlis')">&#128078; Yanlis</button>
    <span class="tesekkur" id="oyMesaj"></span>
  </div>
</div>

<!-- ONERILER + ANOMALi -->
<div class="kart">
  <div class="baslik">AI Onerileri</div>
  <div id="oneriListe" style="color:var(--sol);font-size:.85rem">Bekleniyor...</div>
  <div id="anomaliKutu" style="display:none;margin-top:10px;border-top:1px dashed var(--line);padding-top:8px">
    <div class="baslik" style="color:var(--amber)">&#9888; Egitim Verisiyle Kiyaslama</div>
    <div id="anomaliListe"></div>
  </div>
</div>

<!-- CANLI SENSORLER -->
<div class="kart">
  <div class="baslik">Canli Sensorler <span id="sensZaman" style="text-transform:none;letter-spacing:0"></span></div>
  <div class="izgara" id="sensIzgara"></div>
</div>

<!-- SOHBET -->
<div class="kart">
  <div class="baslik">AI Asistanla Konus</div>
  <div class="cipler">
    <span class="cip" onclick="hizliSor('Sulama gerekecek mi?')">Sulama gerekecek mi?</span>
    <span class="cip" onclick="hizliSor('Onerin ne?')">Onerin ne?</span>
    <span class="cip" onclick="hizliSor('Veriler normal mi?')">Veriler normal mi?</span>
    <span class="cip" onclick="hizliSor('Modelini anlat')">Modelini anlat</span>
    <span class="cip" onclick="hizliSor('Yeniden egit')">Yeniden egit</span>
  </div>
  <div id="sohbet">
    <div class="balon ai">Merhaba! &#127807; Seranin yapay zeka asistaniyim. Sulama tahmini, sensor durumu veya onerilerimi sorabilirsin.</div>
  </div>
  <div class="yaz">
    <input id="soruKutu" placeholder="Sorunu yaz... (orn: toprak nemi nasil?)"
           onkeydown="if(event.key==='Enter')sor()">
    <button id="gonderBtn" onclick="sor()">Gonder</button>
  </div>
</div>

<footer>AgroNext &middot; TEKNOFEST Tarim Teknolojileri &middot; GRU derin ogrenme ile tahmini sulama</footer>

<script>
var beyinUrl = null;
var sonAi    = null;

function getj(url, cb){
  fetch(url, {signal: AbortSignal.timeout ? AbortSignal.timeout(4000) : undefined})
    .then(function(r){ return r.json(); }).then(cb).catch(function(){ cb(null); });
}

var SENSORLER = [
  ["temp","Sicaklik","C"], ["humidity","Hava Nemi","%"], ["soil_pct","Toprak Nemi","%"],
  ["co2","CO2","ppm"], ["lux","Isik","lux"], ["pressure","Basinc","hPa"],
  ["voc","VOC","Ohm"], ["ph","pH",""], ["pump","Pompa",""]
];

function sensorCiz(d){
  var h = "";
  for (var i=0;i<SENSORLER.length;i++){
    var k=SENSORLER[i][0], ad=SENSORLER[i][1], birim=SENSORLER[i][2];
    var v = d ? d[k] : null;
    var goster = (v===null||v===undefined) ? "--"
               : (k==="pump" ? (v==1?"ACIK":"KAPALI") : v);
    h += '<div class="sens"><div class="ad">'+ad+'</div><div class="deger"'+
         (k==="pump"&&v==1?' style="color:var(--cyan)"':'')+'>'+goster+
         (birim?' <small>'+birim+'</small>':'')+'</div></div>';
  }
  document.getElementById("sensIzgara").innerHTML = h;
}

function sensorGuncelle(){
  getj("/oku", function(d){
    var r = document.getElementById("rSensor");
    if (d){ r.className="rozet on"; r.textContent="Sensor Canli";
            sensorCiz(d);
            document.getElementById("sensZaman").textContent =
              " - " + new Date().toLocaleTimeString("tr-TR"); }
    else  { r.className="rozet kir"; r.textContent="Sensor Yok"; }
  });
}

function aiGuncelle(){
  getj("/ai_durum", function(d){
    var r = document.getElementById("rAi");
    if (!d || !d.ai){ r.className="rozet off"; r.textContent="AI Bekleniyor"; return; }
    sonAi = d.ai;
    beyinUrl = "http://" + d.beyin_ip + ":" + (d.ai.beyin_port || 5001);

    var taze = (d.yas_sn !== undefined && d.yas_sn < 20);
    r.className = "rozet " + (taze ? "on" : "off");
    r.textContent = taze ? "AI Bagli" : "AI Cevrimdisi";

    var p = Math.round((d.ai.olasilik || 0) * 100);
    var sulama = d.ai.karar && d.ai.karar.indexOf("GEREKLI") >= 0;
    document.getElementById("aiYuzde").textContent = "%" + p;
    document.getElementById("aiYuzde").style.color = sulama ? "var(--amber)" : "var(--yesil)";
    document.getElementById("aiKarar").textContent =
      (sulama ? "Sulama Gerekli " : "Sulama Gerekmez ") + (d.ai.karar || "");
    document.getElementById("aiMesaj").textContent = d.ai.mesaj || "";
    var bar = document.getElementById("aiBar");
    bar.style.width = p + "%";
    bar.style.background = sulama ? "var(--amber)" : "var(--yesil)";
    document.getElementById("aiMini").textContent =
      "model: " + (d.ai.model_surum||"?") + " - guncelleme: " + (d.ai.zaman||"?");

    var ol = d.ai.oneriler || [];
    var h = "";
    for (var i=0;i<ol.length;i++){
      h += '<div class="oneri '+(ol[i].seviye||"bilgi")+'"><span class="nokta"></span>'+
           '<div><b>'+ol[i].baslik+'</b><span>'+ol[i].detay+'</span></div></div>';
    }
    document.getElementById("oneriListe").innerHTML =
      h || '<span style="color:var(--sol)">Su an oneri yok - kosullar dengede.</span>';

    var an = d.ai.anomaliler || [];
    document.getElementById("anomaliKutu").style.display = an.length ? "block" : "none";
    var ah = "";
    for (var j=0;j<an.length;j++) ah += '<div class="anomali">- '+an[j]+'</div>';
    document.getElementById("anomaliListe").innerHTML = ah;
  });
}

function balonEkle(metin, sinif){
  var s = document.getElementById("sohbet");
  var b = document.createElement("div");
  b.className = "balon " + sinif;
  b.textContent = metin;
  s.appendChild(b);
  s.scrollTop = s.scrollHeight;
  return b;
}
function hizliSor(t){ document.getElementById("soruKutu").value = t; sor(); }
function sor(){
  var kutu = document.getElementById("soruKutu");
  var soru = kutu.value.trim();
  if (!soru) return;
  kutu.value = "";
  balonEkle(soru, "ben");
  if (!beyinUrl){
    balonEkle("AI beynine henuz baglanamadim. Laptopda kopru.py + api.py calisiyor mu?", "ai");
    return;
  }
  var bekle = balonEkle("dusunuyorum...", "ai bekliyor");
  document.getElementById("gonderBtn").disabled = true;
  fetch(beyinUrl + "/chat", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({soru: soru})
  }).then(function(r){ return r.json(); })
    .then(function(d){ bekle.textContent = d.cevap || d.hata || "(bos cevap)";
                       bekle.className = "balon ai"; })
    .catch(function(){ bekle.textContent =
      "AI beynine ulasilamadi (laptop uyyumus olabilir). Az sonra tekrar dene.";
      bekle.className = "balon ai"; })
    .finally(function(){ document.getElementById("gonderBtn").disabled = false; });
}

function oyVer(tip){
  var m = document.getElementById("oyMesaj");
  if (!beyinUrl){ m.textContent = "AI beyni bagli degil."; return; }
  fetch(beyinUrl + "/geri_bildirim", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({tip: tip})
  }).then(function(r){ return r.json(); })
    .then(function(){ m.textContent = "Kaydedildi, tesekkurler!"; })
    .catch(function(){ m.textContent = "Gonderilemedi."; });
  setTimeout(function(){ m.textContent = ""; }, 4000);
}

sensorGuncelle(); aiGuncelle();
setInterval(sensorGuncelle, 3000);
setInterval(aiGuncelle, 3000);
</script>
</body>
</html>
)HTMLDOC";
