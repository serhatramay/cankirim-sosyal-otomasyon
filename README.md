# Çankırım Sosyal RSS Dağıtıcısı

`https://cankirim.com.tr/rss.xml` beslemesini 20 dakikada bir kontrol edip yeni yayımlanan haberleri açık grafik (`1200x630`) görseli, özeti ve Türkçe etiketleriyle Bluesky, Mastodon ve Facebook Sayfası (`@cankirimcom`) hesaplarına otomatik paylaşan hafif Python otomasyonu.

## Özellikler
- Harici bağımlılık içermez (yalnızca Python 3.12 standart kütüphanesi).
- Son gönderileri doğrudan Bluesky, Mastodon ve Facebook Graph API üzerinden okuyarak aynı haberin çift paylaşılmasını önler.
- Türkçe metinler için UTF-8 bayt bazlı Bluesky facet hesaplaması yapar.
- Her platformu ayrı hata korumasıyla çalıştırır; biri geçici hata verse bile diğeri paylaşımı tamamlar.
