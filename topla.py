"""Yapay zeka haberlerini RSS/Atom kaynaklarından toplar, siteye veri yazar.

Yalnız Python standart kütüphanesi kullanır; ücretli API yok.
Kullanım:
    python topla.py                      # topla + site/ verisini yaz
    python topla.py --ollama qwen2.5:7b  # yeni haberlere yerel Ollama ile Türkçe özet ekle
"""
import argparse
import concurrent.futures as cf
import email.utils
import gzip
import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

KOK = Path(__file__).resolve().parent
VERI = KOK / "veri" / "haberler.json"
SITE = KOK / "site"
SITE_URL = "https://gunayboss.github.io/yapay-zeka-haber/"

SAKLA_GUN = 30
EN_FAZLA = 800
KAYNAK_BASI = 40  # tek kaynak akışı boğmasın
UA = "Mozilla/5.0 (YapayZekaHaber RSS toplayici)"

AI_DESENI = re.compile(
    r"\b(ai|a\.i\.|yapay zek[aâ]|artificial intelligence|llm|gpt|chatgpt|claude|gemini|"
    r"openai|anthropic|deepmind|copilot|machine learning|makine öğren|neural|nöral|"
    r"genai|generative|üretken|agent|ajan|transformer|diffusion|mistral|llama|grok|"
    r"deepseek|qwen|sora|midjourney|nvidia|gpu)\b",
    re.I,
)

KATEGORILER = [
    ("Model", r"\b(model|gpt-?\d|claude|gemini|llama|mistral|qwen|deepseek|grok|o\d|release|launch|benchmark|sürüm|piyasaya)\b"),
    ("Araştırma", r"\b(research|paper|arxiv|study|araştırma|makale|reasoning|alignment|interpretab)\w*"),
    ("Ürün & Araç", r"\b(app|tool|feature|api|sdk|plugin|copilot|agent|ajan|uygulama|özellik|araç)\w*"),
    ("İş & Yatırım", r"\b(funding|raises|valuation|acquisition|acquire|ipo|revenue|deal|yatırım|satın|milyar|billion|million)\w*"),
    ("Politika & Etik", r"\b(regulat|law|policy|lawsuit|copyright|safety|ethic|ban|eu ai act|yasa|düzenleme|dava|güvenlik|etik)\w*"),
    ("Donanım", r"\b(chip|gpu|nvidia|tpu|data ?center|semiconductor|çip|veri merkezi)\w*"),
]

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def indir(url, zaman_asimi=20):
    istek = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:
        veri = yanit.read()
    if veri[:2] == bytes([0x1F, 0x8B]):  # bazı sunucular istenmeden gzip döndürüyor
        veri = gzip.decompress(veri)
    return veri


def temiz_metin(ham, sinir=300):
    if not ham:
        return ""
    metin = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", ham, flags=re.S | re.I)
    metin = re.sub(r"<[^>]+>", " ", metin)
    metin = html.unescape(metin)
    metin = re.sub(r"\s+", " ", metin).strip()
    if len(metin) > sinir:
        metin = metin[:sinir].rsplit(" ", 1)[0] + "…"
    return metin


def paragraflar(ham, sinir=1400):
    """Okuma paneli için HTML'den düz paragraflar çıkarır (en fazla `sinir` karakter)."""
    if not ham:
        return []
    metin_ = re.sub(r"<(script|style|figure|figcaption)[^>]*>.*?</\1>", " ", ham, flags=re.S | re.I)
    metin_ = re.sub(r"</(p|div|li|h[1-6]|blockquote)>|<br\s*/?>", "\n", metin_, flags=re.I)
    metin_ = html.unescape(re.sub(r"<[^>]+>", " ", metin_))
    sonuc, toplam = [], 0
    for p in metin_.split("\n"):
        p = re.sub(r"\s+", " ", p).strip()
        if len(p) < 40 or p.startswith(("Article URL:", "Comments URL:", "The post ")):
            continue
        if toplam + len(p) > sinir:
            if not sonuc:
                sonuc.append(p[:sinir].rsplit(" ", 1)[0] + "…")
            break
        sonuc.append(p)
        toplam += len(p)
    return sonuc


def kisa_ozet(metin_, sinir=170):
    """Hikâye kartı için ilk bir-iki cümle."""
    cumleler = re.split(r"(?<=[.!?])\s+", metin_)
    sonuc = ""
    for c in cumleler:
        if sonuc and len(sonuc) + len(c) > sinir:
            break
        sonuc = (sonuc + " " + c).strip()
    if len(sonuc) > sinir + 40:
        sonuc = sonuc[:sinir].rsplit(" ", 1)[0] + "…"
    return sonuc


def tarih_coz(deger):
    if not deger:
        return None
    deger = deger.strip()
    try:
        t = email.utils.parsedate_to_datetime(deger)
    except (TypeError, ValueError):
        try:
            t = datetime.fromisoformat(deger.replace("Z", "+00:00"))
        except ValueError:
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def gorsel_bul(oge, ham_html):
    for etiket in ("media:content", "media:thumbnail"):
        for m in oge.findall(etiket, NS):
            url = m.get("url")
            if url and (m.get("medium") in (None, "image") or re.search(r"\.(jpe?g|png|webp|gif)", url, re.I)):
                return url
    enc = oge.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        return enc.get("url")
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', ham_html or "")
    return html.unescape(m.group(1)) if m else None


def metin(oge, yol):
    e = oge.find(yol, NS)
    return (e.text or "") if e is not None else ""


def ayristir(veri, kaynak):
    kok = ET.fromstring(veri)
    haberler = []
    if kok.tag.endswith("feed"):  # Atom
        for oge in kok.findall("atom:entry", NS):
            link = ""
            for l in oge.findall("atom:link", NS):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href", "")
                    break
            ham = metin(oge, "atom:summary") or metin(oge, "atom:content")
            uzun = metin(oge, "atom:content") or ham
            haberler.append({
                "detay": paragraflar(uzun),
                "baslik": temiz_metin(metin(oge, "atom:title"), 220),
                "link": link,
                "tarih": tarih_coz(metin(oge, "atom:published") or metin(oge, "atom:updated")),
                "ozet": temiz_metin(ham),
                "gorsel": gorsel_bul(oge, ham),
            })
    else:  # RSS 2.0
        for oge in kok.iter("item"):
            ham = metin(oge, "description") or metin(oge, "content:encoded")
            uzun = metin(oge, "content:encoded") or ham
            haberler.append({
                "detay": paragraflar(uzun),
                "baslik": temiz_metin(metin(oge, "title"), 220),
                "link": metin(oge, "link").strip() or metin(oge, "guid").strip(),
                "tarih": tarih_coz(metin(oge, "pubDate")),
                "ozet": temiz_metin(ham),
                "gorsel": gorsel_bul(oge, metin(oge, "content:encoded") or ham),
            })
    sonuc = []
    for h in haberler:
        if not h["baslik"] or not h["link"].startswith("http"):
            continue
        if h["ozet"].startswith("Article URL:"):  # hnrss özeti yalnız bağlantı listesi
            h["ozet"] = ""
        if kaynak.get("filtre") and not AI_DESENI.search(h["baslik"] + " " + h["ozet"]):
            continue
        h["kaynak"] = kaynak["ad"]
        h["tur"] = kaynak["tur"]
        sonuc.append(h)
    return sonuc


def kategori(h):
    metin_ = (h["baslik"] + " " + h["ozet"]).lower()
    for ad, desen in KATEGORILER:
        if re.search(desen, metin_, re.I):
            return ad
    return "Genel"


def anahtar(baslik):
    return re.sub(r"[^a-z0-9çğıöşü]+", "", baslik.lower())[:80]


def kaynak_cek(kaynak):
    try:
        haberler = ayristir(indir(kaynak["url"]), kaynak)
        haberler.sort(key=lambda h: h["tarih"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return kaynak["ad"], haberler[:KAYNAK_BASI], None
    except Exception as hata:  # tek kaynak düşerse diğerleri devam etsin
        return kaynak["ad"], [], f"{type(hata).__name__}: {hata}"


def ollama_ozet(model, h):
    istem = (
        "Aşağıdaki yapay zeka haberini Türkçe olarak, tarafsız ve en fazla 2 cümleyle özetle. "
        "Yalnız özeti yaz.\n\n"
        f"Başlık: {h['baslik']}\nMetin: {h['ozet']}"
    )
    govde = json.dumps({"model": model, "prompt": istem, "stream": False}).encode()
    istek = urllib.request.Request("http://localhost:11434/api/generate", data=govde, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(istek, timeout=120) as yanit:
        return json.loads(yanit.read())["response"].strip()


def rss_yaz(haberler, guncellendi):
    def x(s):
        return html.escape(s or "", quote=True)
    ogeler = []
    for h in haberler[:60]:
        tarih = email.utils.format_datetime(datetime.fromisoformat(h["tarih"]))
        aciklama = h.get("ozet_tr") or h["ozet"]
        ogeler.append(
            f"<item><title>{x(h['baslik'])}</title><link>{x(h['link'])}</link>"
            f"<guid isPermaLink=\"false\">{h['id']}</guid><pubDate>{tarih}</pubDate>"
            f"<category>{x(h['kategori'])}</category><source url=\"{x(h['link'])}\">{x(h['kaynak'])}</source>"
            f"<description>{x(aciklama)}</description></item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>Yapay Zeka Gündemi</title><link>{SITE_URL}</link>"
        "<description>Yapay zeka dünyasından güncel haberler, tek akışta.</description>"
        f"<language>tr</language><lastBuildDate>{email.utils.format_datetime(guncellendi)}</lastBuildDate>"
        + "".join(ogeler) + "</channel></rss>\n"
    )


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", metavar="MODEL", help="yeni haberlere yerel Ollama ile Türkçe özet ekle")
    ap.add_argument("--ozet-siniri", type=int, default=25, help="bir çalıştırmada en fazla kaç özet üretilsin")
    args = ap.parse_args()

    kaynaklar = json.loads((KOK / "kaynaklar.json").read_text(encoding="utf-8"))
    eski = json.loads(VERI.read_text(encoding="utf-8")) if VERI.exists() else []
    hepsi = {h["id"]: h for h in eski}
    gorulen_baslik = {anahtar(h["baslik"]) for h in eski}

    simdi = datetime.now(timezone.utc)
    esik = simdi - timedelta(days=SAKLA_GUN)
    yeni_sayi = 0
    durum = {}

    with cf.ThreadPoolExecutor(max_workers=8) as havuz:
        for ad, haberler, hata in havuz.map(kaynak_cek, kaynaklar):
            durum[ad] = hata or f"{len(haberler)} haber"
            for h in haberler:
                tarih = h["tarih"] or simdi
                if tarih < esik:
                    continue
                kimlik = hashlib.sha1(h["link"].encode()).hexdigest()[:12]
                b = anahtar(h["baslik"])
                if kimlik in hepsi or b in gorulen_baslik:
                    continue
                h["tarih"] = min(tarih, simdi).isoformat()
                h["id"] = kimlik
                h["kategori"] = kategori(h)
                h["kisa"] = kisa_ozet(h["ozet"])
                hepsi[kimlik] = h
                gorulen_baslik.add(b)
                yeni_sayi += 1

    liste = [h for h in hepsi.values() if datetime.fromisoformat(h["tarih"]) >= esik]
    liste.sort(key=lambda h: h["tarih"], reverse=True)
    liste = liste[:EN_FAZLA]

    if args.ollama:
        uretilen = 0
        for h in liste:
            if uretilen >= args.ozet_siniri:
                break
            if h.get("ozet_tr"):
                continue
            try:
                h["ozet_tr"] = ollama_ozet(args.ollama, h)
                uretilen += 1
            except Exception as hata:
                print(f"Ollama özeti atlandı: {hata}", file=sys.stderr)
                break
        print(f"Ollama: {uretilen} Türkçe özet üretildi")

    VERI.parent.mkdir(parents=True, exist_ok=True)
    VERI.write_text(json.dumps(liste, ensure_ascii=False, indent=1), encoding="utf-8")

    SITE.mkdir(exist_ok=True)
    paket = {"guncellendi": simdi.isoformat(), "haberler": liste}
    (SITE / "haberler.json").write_text(json.dumps(paket, ensure_ascii=False), encoding="utf-8")
    (SITE / "haberler.js").write_text("window.HABERLER=" + json.dumps(paket, ensure_ascii=False) + ";\n", encoding="utf-8")
    (SITE / "feed.xml").write_text(rss_yaz(liste, simdi), encoding="utf-8")

    for ad, d in durum.items():
        print(f"  {ad:16} {d}")
    print(f"Yeni: {yeni_sayi} · Toplam: {len(liste)}")


if __name__ == "__main__":
    main()
