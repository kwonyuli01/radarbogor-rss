#!/usr/bin/env python3
"""
RadarBogor JawaPos RSS Feed Scraper - Kategori Bansos
======================================================
Scrape halaman kategori bansos dari radarbogor.jawapos.com
dengan konten artikel lengkap (termasuk multi-page hingga 5 halaman).

Dijalankan otomatis via GitHub Actions + publish ke GitHub Pages.
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import time
import re
import os
import html
import hashlib

# ============================================================
# KONFIGURASI
# ============================================================

BASE_URL = "https://radarbogor.jawapos.com"
CATEGORY_URL = "https://radarbogor.jawapos.com/bansos"

# Jumlah artikel maksimal
MAX_ARTICLES = 20

# Nama dan deskripsi feed
FEED_TITLE = "Radar Bogor - Bansos"
FEED_DESCRIPTION = "RSS Feed kategori Bansos dari radarbogor.jawapos.com dengan konten artikel lengkap"
FEED_LINK = "https://radarbogor.jawapos.com/bansos"

# File output
OUTPUT_FILE = "docs/feed.xml"

# Delay antar request (detik)
REQUEST_DELAY = 2

# User Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Timezone WIB
WIB = timezone(timedelta(hours=7))

# Bulan Indonesia ke angka
BULAN_MAP = {
    'januari': 1, 'februari': 2, 'maret': 3, 'april': 4,
    'mei': 5, 'juni': 6, 'juli': 7, 'agustus': 8,
    'september': 9, 'oktober': 10, 'november': 11, 'desember': 12
}

# ============================================================
# KODE UTAMA
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
})


def fetch_page(url, retries=3):
    """Fetch halaman web dengan retry."""
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.RequestException as e:
            print(f"  [!] Gagal fetch {url} (percobaan {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY * 2)
    return None


def parse_list_page(url):
    """Parse halaman kategori untuk mendapatkan daftar artikel."""
    print(f"\n[*] Scraping halaman: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    articles = []

    # 1. Ambil headline article (h1.hl__b-title > a)
    headline = soup.select_one('h1.hl__b-title a.hl__link')
    if headline:
        href = headline.get('href', '')
        title = headline.get_text(strip=True)
        if href and title and '/bansos/' in href:
            if not href.startswith('http'):
                href = BASE_URL + href
            articles.append({'title': title, 'link': href})

    # 2. Ambil semua latest articles (div.latest__item)
    for item in soup.select('div.latest__item'):
        link_tag = item.select_one('a.latest__link')
        if not link_tag:
            continue

        href = link_tag.get('href', '')
        title = link_tag.get_text(strip=True)

        if not href or not title:
            continue

        if not href.startswith('http'):
            href = BASE_URL + href

        # Hindari duplikat
        if any(a['link'] == href for a in articles):
            continue

        articles.append({'title': title, 'link': href})

        if len(articles) >= MAX_ARTICLES:
            break

    print(f"  [+] Ditemukan {len(articles)} artikel")
    return articles


def parse_article_page(url):
    """Parse halaman artikel untuk mendapatkan konten lengkap."""
    print(f"  [>] Mengambil artikel: {url}")

    html_content = fetch_page(url)
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'lxml')
    article_data = {}

    # === JUDUL ===
    h1 = soup.select_one('h1.read__title')
    article_data['title'] = h1.get_text(strip=True) if h1 else ''

    # === TANGGAL ===
    # Prioritas 1: dataLayer published_date "2026-02-24 08:50:51"
    pub_date_str = ''
    match = re.search(r'"published_date"\s*:\s*"([^"]+)"', html_content)
    if match:
        pub_date_str = match.group(1)

    # Prioritas 2: div.read__info__date "- Selasa, 24 Februari 2026 | 08:50 WIB"
    if not pub_date_str:
        date_div = soup.select_one('div.read__info__date')
        if date_div:
            pub_date_str = date_div.get_text(strip=True)

    article_data['pub_date'] = parse_date(pub_date_str)

    # === REPORTER ===
    reporter = ''
    # dataLayer penulis
    match = re.search(r'"penulis"\s*:\s*"([^"]+)"', html_content)
    if match:
        reporter = match.group(1)
    else:
        author_div = soup.select_one('div.read__info__author a')
        if author_div:
            reporter = author_div.get_text(strip=True)
    article_data['reporter'] = reporter

    # === EDITOR ===
    editor = ''
    match = re.search(r'"editor"\s*:\s*"([^"]+)"', html_content)
    if match:
        editor = match.group(1)
    article_data['editor'] = editor

    # === GAMBAR UTAMA ===
    main_image = ''
    og_image = soup.find('meta', property='og:image')
    if og_image:
        main_image = og_image.get('content', '')

    if not main_image:
        # Fallback: lazyload image di photo div
        photo_img = soup.select_one('div.photo__img img')
        if photo_img:
            main_image = photo_img.get('data-src', '') or photo_img.get('src', '')

    article_data['image'] = main_image

    # === CAPTION ===
    caption = ''
    caption_div = soup.select_one('div.photo__caption')
    if caption_div:
        caption = caption_div.get_text(strip=True)
    article_data['caption'] = caption

    # === KONTEN ARTIKEL (halaman 1) ===
    content_parts = extract_content(soup)
    article_data['content'] = '\n\n'.join(content_parts)

    # === MULTI-PAGE: Halaman 2-5 ===
    paging = soup.select_one('div.paging.paging--article')
    if paging:
        page_links = []
        for a in paging.select('a.paging__link'):
            href = a.get('href', '')
            text = a.get_text(strip=True)
            # Skip halaman aktif, "Selanjutnya", dan duplikat
            if 'paging__link--active' in a.get('class', []):
                continue
            if text.lower() in ['selanjutnya', 'sebelumnya', 'next', 'prev']:
                continue
            if href and href not in page_links:
                page_links.append(href)

        for page_url in page_links[:4]:  # Maks 4 halaman tambahan (total 5)
            if not page_url.startswith('http'):
                page_url = BASE_URL + page_url
            print(f"    [>] Halaman lanjutan: {page_url}")
            time.sleep(REQUEST_DELAY)
            page_content = fetch_additional_page(page_url)
            if page_content:
                article_data['content'] += '\n\n' + page_content

    # === TAGS ===
    tags = []
    tag_list = soup.select('ul.tag__list li h4 a')
    for tag_link in tag_list:
        tag_text = tag_link.get_text(strip=True)
        if tag_text and tag_text not in tags:
            tags.append(tag_text)
    article_data['tags'] = tags

    # === KATEGORI ===
    category = ''
    match = re.search(r'"rubrik"\s*:\s*"([^"]+)"', html_content)
    if match:
        category = match.group(1)
    article_data['category'] = category

    return article_data


def extract_content(soup):
    """Ekstrak konten artikel dari article.read__content."""
    content_parts = []

    article_elem = soup.select_one('article.read__content')
    if not article_elem:
        return content_parts

    for elem in article_elem.find_all(['p', 'h2', 'h3', 'h4']):
        # Skip "Baca Juga" links
        baca_juga = elem.find('strong', class_='read__others')
        if baca_juga:
            continue

        text = elem.get_text(strip=True)

        if not text:
            continue

        # Skip komentar HTML dan placeholder
        if text.startswith('<!--') or text == '':
            continue

        # Skip teks sangat pendek
        if len(text) < 5:
            continue

        if elem.name in ['h2', 'h3', 'h4']:
            content_parts.append(f"\n### {text}\n")
        else:
            # Cek apakah paragraf berisi sub-judul (bold saja)
            strong = elem.find('strong')
            if strong and strong.get_text(strip=True) == text and not elem.find('a'):
                content_parts.append(f"\n### {text}\n")
            else:
                clean_text = text.replace('\xa0', ' ').strip()
                if clean_text:
                    content_parts.append(clean_text)

    return content_parts


def fetch_additional_page(url):
    """Fetch halaman lanjutan dari artikel multi-page."""
    html_content = fetch_page(url)
    if not html_content:
        return ''

    soup = BeautifulSoup(html_content, 'lxml')
    content_parts = extract_content(soup)
    return '\n\n'.join(content_parts)


def parse_date(date_str):
    """Parse tanggal ke format RFC 822."""
    if not date_str:
        return datetime.now(WIB).strftime('%a, %d %b %Y %H:%M:%S +0700')

    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    # Format 1: "2026-02-24 08:50:51" (dataLayer)
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', date_str)
    if m:
        year, month, day, hour, minute, sec = m.groups()
        try:
            dt = datetime(int(year), int(month), int(day), int(hour), int(minute), int(sec))
            return f"{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} +0700"
        except ValueError:
            pass

    # Format 2: "Selasa, 24 Februari 2026 | 08:50 WIB"
    m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})\s*\|\s*(\d{2}):(\d{2})', date_str)
    if m:
        day, bulan_str, year, hour, minute = m.groups()
        bulan_num = BULAN_MAP.get(bulan_str.lower(), 0)
        if bulan_num:
            try:
                dt = datetime(int(year), bulan_num, int(day), int(hour), int(minute))
                return f"{days[dt.weekday()]}, {int(day):02d} {months[bulan_num-1]} {int(year)} {int(hour):02d}:{int(minute):02d}:00 +0700"
            except ValueError:
                pass

    return datetime.now(WIB).strftime('%a, %d %b %Y %H:%M:%S +0700')


def generate_rss(articles_data):
    """Generate file RSS XML dari data artikel."""
    print(f"\n[*] Generating RSS XML...")
    now = datetime.now(WIB).strftime('%a, %d %b %Y %H:%M:%S +0700')

    rss_items = []
    for article in articles_data:
        if not article:
            continue

        content_html = ''

        # Gambar utama
        if article.get('image'):
            content_html += f'<p><img src="{html.escape(article["image"])}" alt="{html.escape(article.get("title", ""))}" style="max-width:100%;" /></p>\n'

        # Caption
        if article.get('caption'):
            content_html += f'<p><em>{html.escape(article["caption"])}</em></p>\n'

        # Reporter/Editor
        if article.get('reporter'):
            content_html += f'<p><strong>Reporter:</strong> {html.escape(article["reporter"])}'
            if article.get('editor'):
                content_html += f' | <strong>Editor:</strong> {html.escape(article["editor"])}'
            content_html += '</p>\n'

        # Konten
        if article.get('content'):
            paragraphs = article['content'].split('\n\n')
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if para.startswith('### '):
                    content_html += f'<h3>{html.escape(para[4:])}</h3>\n'
                else:
                    content_html += f'<p>{html.escape(para)}</p>\n'

        # Tags
        if article.get('tags'):
            tags_str = ', '.join(article['tags'])
            content_html += f'<p><strong>Tags:</strong> {html.escape(tags_str)}</p>\n'

        guid = article.get('link', hashlib.md5(article.get('title', '').encode()).hexdigest())

        rss_items.append({
            'title': article.get('title', 'Tanpa Judul'),
            'link': article.get('link', ''),
            'description': content_html,
            'pubDate': article.get('pub_date', now),
            'category': article.get('category', ''),
            'tags': article.get('tags', []),
            'guid': guid,
            'image': article.get('image', ''),
        })

    rss_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>{html.escape(FEED_TITLE)}</title>
    <description>{html.escape(FEED_DESCRIPTION)}</description>
    <link>{html.escape(FEED_LINK)}</link>
    <language>id</language>
    <lastBuildDate>{now}</lastBuildDate>
    <generator>RadarBogor RSS Scraper (GitHub Actions)</generator>
'''

    for item in rss_items:
        rss_xml += f'''    <item>
      <title><![CDATA[{item['title']}]]></title>
      <link>{html.escape(item['link'])}</link>
      <guid isPermaLink="true">{html.escape(item['guid'])}</guid>
      <pubDate>{item['pubDate']}</pubDate>
'''
        if item['category']:
            rss_xml += f'      <category><![CDATA[{item["category"]}]]></category>\n'
        for tag in item.get('tags', []):
            rss_xml += f'      <category><![CDATA[{tag}]]></category>\n'
        if item['image']:
            rss_xml += f'      <media:content url="{html.escape(item["image"])}" medium="image" />\n'
        rss_xml += f'      <description><![CDATA[{item["description"]}]]></description>\n'
        rss_xml += f'      <content:encoded><![CDATA[{item["description"]}]]></content:encoded>\n'
        rss_xml += '    </item>\n'

    rss_xml += '''  </channel>
</rss>'''

    return rss_xml


def main():
    """Fungsi utama."""
    print("=" * 60)
    print("  RadarBogor JawaPos RSS Scraper - Bansos")
    print("=" * 60)
    print(f"  Feed Title : {FEED_TITLE}")
    print(f"  Output     : {OUTPUT_FILE}")
    print(f"  Max Artikel: {MAX_ARTICLES}")
    print(f"  Source URL : {CATEGORY_URL}")
    print("=" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    # Step 1: Scrape halaman kategori
    articles = parse_list_page(CATEGORY_URL)

    if not articles:
        print("\n[!] Tidak ada artikel ditemukan.")
        return

    # Hapus duplikat
    seen = set()
    unique_articles = []
    for article in articles:
        if article['link'] not in seen:
            seen.add(article['link'])
            unique_articles.append(article)

    print(f"\n[*] Total {len(unique_articles)} artikel unik")

    # Step 2: Fetch konten lengkap setiap artikel
    articles_data = []
    for i, article in enumerate(unique_articles):
        print(f"\n--- Artikel {i+1}/{len(unique_articles)} ---")
        article_data = parse_article_page(article['link'])

        if article_data:
            if not article_data.get('title'):
                article_data['title'] = article['title']
            article_data['link'] = article['link']
            articles_data.append(article_data)
        else:
            articles_data.append({
                'title': article['title'],
                'link': article['link'],
                'content': '(Konten tidak dapat diambil)',
                'pub_date': datetime.now(WIB).strftime('%a, %d %b %Y %H:%M:%S +0700'),
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })

        time.sleep(REQUEST_DELAY)

    # Step 3: Generate & simpan RSS
    rss_xml = generate_rss(articles_data)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(rss_xml)

    print(f"\n{'=' * 60}")
    print(f"  SELESAI! File: {OUTPUT_FILE}")
    print(f"  Total artikel: {len(articles_data)}")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
