# Yapay Zeka Gündemi

Yapay zeka haberlerini 18 RSS kaynağından (OpenAI, DeepMind, Google, Hugging Face, NVIDIA, Microsoft, AWS, TechCrunch, The Verge, Wired, MIT Tech Review, Ars Technica, Hacker News, Webtekno, ShiftDelete…) toplayıp tek sayfada yayınlayan, saatlik kendiliğinden güncellenen site.

Ücretli API yok: toplayıcı yalnız Python standart kütüphanesini kullanır, barındırma GitHub Pages (ücretsiz), zamanlama GitHub Actions (herkese açık depoda ücretsiz).

## Dosyalar

| Dosya | Görev |
|---|---|
| `kaynaklar.json` | RSS kaynak listesi. `"filtre": true` olan kaynaklarda yalnız yapay zeka ile ilgili haberler alınır. |
| `topla.py` | Akışları çeker, tekrarları ayıklar, kategori atar; `site/haberler.js`, `site/haberler.json`, `site/feed.xml` yazar. Son 30 günü tutar. |
| `site/index.html` | Site: üstte kendiliğinden ilerleyen hikâye oynatıcısı (son 48 saatten 8 haber, kategori renginde iki tonlu görsel, kelime kelime beliren başlık), altında günlere ayrılmış akış. Habere tıklayınca okuma paneli açılır: kısa özet, uzun bölüm, kaynağa git, paylaş (X, LinkedIn, WhatsApp, Telegram, bağlantı). Arama, kategori/kaynak filtresi, açık/koyu tema. |
| `.github/workflows/guncelle.yml` | Her saat topla → depoya işle → GitHub Pages'e yayınla. |

## Yerelde çalıştırma

```bash
python topla.py
```

Sonra `site/index.html` dosyasını tarayıcıda aç.

### İsteğe bağlı: Türkçe özet (yerel Ollama, ücretsiz)

```bash
python topla.py --ollama qwen2.5:7b
```

Yeni haberlere Türkçe 2 cümlelik özet ekler (`ozet_tr`); site varsa onu gösterir. Ollama GitHub sunucusunda yok, bu yüzden bu adım yalnız kendi bilgisayarında çalışır. Windows Görev Zamanlayıcı ile saatlik çalıştırıp `git push` yapılabilir.

## Yayına alma (GitHub Pages)

1. GitHub'da herkese açık bir depo aç (ör. `yapay-zeka-haber`) ve bu klasörü gönder.
2. Depo → Settings → Pages → Source: **GitHub Actions**.
3. Actions sekmesinde "Haberleri güncelle ve yayınla" iş akışını bir kez elle çalıştır.
4. Site adresi: https://gunayboss.github.io/yapay-zeka-haber/

## Sosyal medyaya otomatik paylaşım

`site/feed.xml` sitenin kendi RSS akışıdır. Bunu RSS→sosyal medya araçlarına bağlayarak her yeni haberi otomatik paylaşabilirsin:

- **Telegram kanalı:** IFTTT veya bir RSS botu (ör. @TheFeedReaderBot) ile akışı kanala bağla.
- **X / Bluesky / Mastodon / LinkedIn:** IFTTT, Zapier veya dlvr.it "RSS'ten gönderi" tarifleri.
