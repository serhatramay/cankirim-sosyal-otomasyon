# Çankırım Sosyal RSS Dağıtıcısı

`https://cankirim.com.tr/rss.xml` beslemesini 20 dakikada bir kontrol edip yeni yayımlanan haberleri açık grafik (`1200x630`) görseli, özeti ve Türkçe etiketleriyle Bluesky, Mastodon, Facebook Sayfası (`@cankirimcom`) ve Threads (`@cankirimcomtr`) hesaplarına otomatik paylaşan hafif Python otomasyonu.

## Özellikler
- Harici bağımlılık içermez (yalnızca Python 3.12 standart kütüphanesi).
- Son gönderileri doğrudan Bluesky, Mastodon, Facebook ve Threads API üzerinden okuyarak aynı haberin çift paylaşılmasını önler.
- `00.30 - 08.00 TSİ` gece sessizliği molası uygular; gece saatlerinde paylaşım yapmaz.
- Platform bazlı hız freni ve 24 saatlik tavan uygular (`Facebook: 2,5 saat / maks 6`, `Threads ve Mastodon: 2 saat / maks 8`, `Bluesky: 1,5 saat / maks 10`), her turda platform başına en fazla 1 haber gönderir.
- Çankırı ve 12 ilçeyi ilgilendiren yerel haberlere öncelik verir; vefat ve cenaze haberlerini otomatik eler.
- Türkçe metinler için UTF-8 bayt bazlı Bluesky facet hesaplaması yapar.
- Her platformu ayrı hata korumasıyla çalıştırır; biri geçici hata verse bile diğeri paylaşımı tamamlar.
