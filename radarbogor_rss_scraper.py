#!/usr/bin/env python3
"""
RadarBogor JawaPos RSS Feed Scraper - Kategori Bansos
======================================================
Menggunakan Playwright (headless browser) untuk bypass Cloudflare.
Scrape halaman kategori bansos + konten artikel lengkap (multi-page 1-5).

Dijalankan otomatis via GitHub Actions + publish ke GitHub Pages.
"""

from playwright.sync_api import sync_playwright
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

MAX_ARTICLES = 20

FEED_TITLE = "Radar Bogor - Bansos"
FEED_DESCRIPTION = "RSS Feed kategori Bansos dari radarbogor.jawapos.com dengan konten artikel lengkap"
FEED_LINK = "https://radarbogor.jawapos.com/bansos"

OUTPUT_FILE = "docs/feed.xml"
REQUEST_DELAY = 3

WIB = timezone(timedelta(hours=7))

BULAN_MAP = {
    'januari': 1, 'februari': 2, 'maret': 3, 'april': 4,
    'mei': 5, 'juni': 6, 'juli': 7, 'agustus': 8,
    'september': 9, 'oktober': 10, 'november': 11, 'desember': 12
}

# ============================================================
# BROWSER SETUP
# ============================================================

browser = None
context = None
page = None


def init_browser():
    """Inisialisasi Playwright browser dengan stealth settings."""
    global browser, context, page

    pw = sync_playwright().start()

    browser = pw.chromium.launch(
        headless=True,
        args=[
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
        ]
    )

    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        locale='id-ID',
        timezone_id='Asia/Jakarta',
        extra_http_headers={
            'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    )

    # Hapus navigator.webdriver flag
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
    """)

    page = context.new_page()
    print("[*] Browser Playwright berhasil diinisialisasi")
    return pw


def fetch_page(url, retries=3):
    """Fetch halaman menggunakan Playwright browser."""
    for attempt in range(retries):
        try:
            print(f"  [>] Fetching: {url}")
            response = page.goto(url, wait_until='domcontentloaded', timeout=30000)

            if response is None:
                print(f"  [!] Response None (percobaan {attempt+1}/{retries})")
                time.sleep(REQUEST_DELAY * 2)
                continue

            status = response.status
            print(f"  [>] Status: {status}")

            # Jika Cloudflare challenge, tunggu redirect
            if status == 403 or status == 503:
                print(f"  [~] Cloudflare challenge terdeteksi, menunggu...")
                time.sleep(8)
                content = page.content()
                if len(content) > 5000:
                    print(f"  [+] Berhasil melewati Cloudflare ({len(content)} chars)")
                    return content
                else:
                    print(f"  [!] Gagal bypass Cloudflare (percobaan {attempt+1}/{retries})")
                    time.sleep(REQUEST_DELAY * 2)
                    continue

            if status == 200:
                # Tunggu sebentar untuk DOM stabil, JANGAN pakai networkidle
                # karena situs ini terus loading ads/tracker
                time.sleep(2)
                content = page.content()
                print(f"  [+] Berhasil ({len(content)} chars)")
                return content

            print(f"  [!] Status {status} (percobaan {attempt+1}/{retries})")
            time.sleep(REQUEST_DELAY * 2)

        except Exception as e:
            print(f"  [!] Error: {e} (percobaan {attempt+1}/{retries})")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY * 2)

    return None


def close_browser():
    """Tutup browser."""
    global browser, context
    try:
        if context:
            context.close()
        if browser:
            browser.close()
    except Exception:
        pass


# ============================================================
# PARSING FUNCTIONS
# ============================================================

def parse_list_page(url):
    """Parse halaman kategori untuk mendapatkan daftar artikel."""
    print(f"\n[*] Scraping halaman: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    articles = []

    # 1. Headline (h1.hl__b-title > a)
    headline = soup.select_one('h1.hl__b-title a.hl__link')
    if headline:
        href = headline.get('href', '')
        title = headline.get_text(strip=True)
        if href and title and '/bansos/' in href:
            if not href.startswith('http'):
                href = BASE_URL + href
            articles.append({'title': title, 'link': href})

    # 2. Latest items (div.latest__item)
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

    # JUDUL
    h1 = soup.select_one('h1.read__title')
    article_data['title'] = h1.get_text(strip=True) if h1 else ''

    # TANGGAL (dataLayer)
    pub_date_str = ''
    match = re.search(r'"published_date"\s*:\s*"([^"]+)"', html_content)
    if match:
        pub_date_str = match.group(1)
    if not pub_date_str:
        date_div = soup.select_one('div.read__info__date')
        if date_div:
            pub_date_str = date_div.get_text(strip=True)
    article_data['pub_date'] = parse_date(pub_date_str)

    # REPORTER
    reporter = ''
    match = re.search(r'"penulis"\s*:\s*"([^"]+)"', html_content)
    if match:
        reporter = match.group(1)
    else:
        author_div = soup.select_one('div.read__info__author a')
        if author_div:
            reporter = author_div.get_text(strip=True)
    article_data['reporter'] = reporter

    # EDITOR
    editor = ''
    match = re.search(r'"editor"\s*:\s*"([^"]+)"', html_content)
    if match:
        editor = match.group(1)
    article_data['editor'] = editor

    # GAMBAR
    main_image = ''
    og_image = soup.find('meta', property='og:image')
    if og_image:
        main_image = og_image.get('content', '')
    if not main_image:
        photo_img = soup.select_one('div.photo__img img')
        if photo_img:
            main_image = photo_img.get('data-src', '') or photo_img.get('src', '')
    article_data['image'] = main_image

    # CAPTION
    caption = ''
    caption_div = soup.select_one('div.photo__caption')
    if caption_div:
        caption = caption_div.get_text(strip=True)
    article_data['caption'] = caption

    # KONTEN (halaman 1)
    content_parts = extract_content(soup)
    article_data['content'] = '\n\n'.join(content_parts)

    # MULTI-PAGE (halaman 2-5)
    paging = soup.select_one('div.paging.paging--article')
    if paging:
        page_links = []
        for a in paging.select('a.paging__link'):
            href = a.get('href', '')
            text = a.get_text(strip=True)
            if 'paging__link--active' in a.get('class', []):
                continue
            if text.lower() in ['selanjutnya', 'sebelumnya', 'next', 'prev']:
                continue
            if href and href not in page_links:
                page_links.append(href)

        for page_url in page_links[:4]:
            if not page_url.startswith('http'):
                page_url = BASE_URL + page_url
            print(f"    [>] Halaman lanjutan: {page_url}")
            time.sleep(REQUEST_DELAY)
            page_content = fetch_additional_page(page_url)
            if page_content:
                article_data['content'] += '\n\n' + page_content

    # TAGS
    tags = []
    for tag_link in soup.select('ul.tag__list li h4 a'):
        tag_text = tag_link.get_text(strip=True)
        if tag_text and tag_text not in tags:
            tags.append(tag_text)
    article_data['tags'] = tags

    # KATEGORI
    category = ''
    match = re.search(r'"rubrik"\s*:\s*"([^"]+)"', html_content)
    if match:
        category = match.group(1)
    article_data['category'] = category

    return article_data


def extract_content(soup):
    """Ekstrak konten dari article.read__content."""
    content_parts = []

    article_elem = soup.select_one('article.read__content')
    if not article_elem:
        return content_parts

    for elem in article_elem.find_all(['p', 'h2', 'h3', 'h4']):
        if elem.find('strong', class_='read__others'):
            continue

        text = elem.get_text(strip=True)
        if not text or len(text) < 5:
            continue

        if elem.name in ['h2', 'h3', 'h4']:
            content_parts.append(f"\n### {text}\n")
        else:
            strong = elem.find('strong')
            if strong and strong.get_text(strip=True) == text and not elem.find('a'):
                content_parts.append(f"\n### {text}\n")
            else:
                clean_text = text.replace('\xa0', ' ').strip()
                if clean_text:
                    content_parts.append(clean_text)

    return content_parts


def fetch_additional_page(url):
    """Fetch halaman lanjutan artikel multi-page."""
    html_content = fetch_page(url)
    if not html_content:
        return ''

    soup = BeautifulSoup(html_content, 'lxml')
    content_parts = extract_content(soup)
    return '\n\n'.join(content_parts)


# ============================================================
# DATE PARSING
# ============================================================

def parse_date(date_str):
    """Parse tanggal ke format RFC 822."""
    if not date_str:
        return datetime.now(WIB).strftime('%a, %d %b %Y %H:%M:%S +0700')

    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    # "2026-02-24 08:50:51"
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', date_str)
    if m:
        year, month, day, hour, minute, sec = m.groups()
        try:
            dt = datetime(int(year), int(month), int(day), int(hour), int(minute), int(sec))
            return f"{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} +0700"
        except ValueError:
            pass

    # "Selasa, 24 Februari 2026 | 08:50 WIB"
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


# ============================================================
# RSS GENERATION
# ============================================================

def generate_rss(articles_data):
    """Generate file RSS XML."""
    print(f"\n[*] Generating RSS XML...")
    now = datetime.now(WIB).strftime('%a, %d %b %Y %H:%M:%S +0700')

    rss_items = []
    for article in articles_data:
        if not article:
            continue

        content_html = ''

        if article.get('image'):
            content_html += f'<p><img src="{html.escape(article["image"])}" alt="{html.escape(article.get("title", ""))}" style="max-width:100%;" /></p>\n'
        if article.get('caption'):
            content_html += f'<p><em>{html.escape(article["caption"])}</em></p>\n'
        if article.get('reporter'):
            content_html += f'<p><strong>Reporter:</strong> {html.escape(article["reporter"])}'
            if article.get('editor'):
                content_html += f' | <strong>Editor:</strong> {html.escape(article["editor"])}'
            content_html += '</p>\n'
        if article.get('content'):
            for para in article['content'].split('\n\n'):
                para = para.strip()
                if not para:
                    continue
                if para.startswith('### '):
                    content_html += f'<h3>{html.escape(para[4:])}</h3>\n'
                else:
                    content_html += f'<p>{html.escape(para)}</p>\n'
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
    <generator>RadarBogor RSS Scraper - Playwright (GitHub Actions)</generator>
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


# ============================================================
# MAIN
# ============================================================

def main():
    """Fungsi utama."""
    print("=" * 60)
    print("  RadarBogor JawaPos RSS Scraper - Bansos (Playwright)")
    print("=" * 60)
    print(f"  Feed Title : {FEED_TITLE}")
    print(f"  Output     : {OUTPUT_FILE}")
    print(f"  Max Artikel: {MAX_ARTICLES}")
    print(f"  Source URL : {CATEGORY_URL}")
    print("=" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    pw = init_browser()

    try:
        # Step 1: Scrape halaman kategori
        articles = parse_list_page(CATEGORY_URL)

        if not articles:
            print("\n[!] Tidak ada artikel ditemukan.")
            print("[!] Kemungkinan Cloudflare masih memblokir.")
            return

        # Hapus duplikat
        seen = set()
        unique_articles = []
        for article in articles:
            if article['link'] not in seen:
                seen.add(article['link'])
                unique_articles.append(article)

        print(f"\n[*] Total {len(unique_articles)} artikel unik")

        # Step 2: Fetch konten lengkap
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

    finally:
        close_browser()
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == '__main__':
    main()
