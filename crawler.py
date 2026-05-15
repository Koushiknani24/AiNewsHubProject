import urllib.request
import urllib.parse
from urllib.error import URLError, HTTPError
from bs4 import BeautifulSoup
import json
import os
import argparse
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
import time
import ssl

NEWS_FEEDS_FILE = 'newsFeeds.json'
# We disable SSL certificate verification for simple scraping to avoid errors with poorly configured sites
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Keywords to guess if a discovered link is another news source
NEWS_KEYWORDS = ['news', 'times', 'post', 'gazette', 'chronicle', 'tribune', 'journal', 'daily', 'herald', 'press', 'report', 'wire']
# Common RSS/Atom paths to check even if not declared in the head
COMMON_FEED_PATHS = ['/rss', '/feed', '/atom', '/rss.xml', '/index.xml']


def load_feeds():
    if os.path.exists(NEWS_FEEDS_FILE):
        try:
            with open(NEWS_FEEDS_FILE, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except json.JSONDecodeError:
            print("Warning: Could not decode newsFeeds.json.")
            return set()
    return set()


def save_feed(feed_url):
    feeds = load_feeds()
    if feed_url not in feeds:
        feeds.add(feed_url)
        with open(NEWS_FEEDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(feeds), f, indent=4)
        print(f"[+] Discovered new Feed: {feed_url}")
        return True
    return False

def is_valid_http_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ['http', 'https'] and bool(parsed.netloc)

def fetch_html(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            if 'text/html' not in response.getheader('Content-Type', ''):
                return None, None
            html = response.read()
            return html, response.geturl() # Return the final URL in case of redirects
    except (URLError, HTTPError, Exception) as e:
        print(f"[-] Error fetching {url}: {e}")
        return None, None

def find_rss_feeds(soup, base_url):
    feeds = set()
    
    # 1. Check for standard link tags in the <head>
    links = soup.find_all('link', rel='alternate')
    for link in links:
        link_type = link.get('type', '').lower()
        if 'application/rss+xml' in link_type or 'application/atom+xml' in link_type:
            href = link.get('href')
            if href:
                feeds.add(urljoin(base_url, href))
                
    # 2. Heuristic: Look for links pointing directly to XML files or feeds
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        if href.endswith('.xml') or href.endswith('/feed') or href.endswith('/rss'):
            feeds.add(urljoin(base_url, href))
            
    return feeds

def extract_outlinks(soup, base_url, is_directory):
    outlinks = set()
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        full_url = urljoin(base_url, href)
        
        parsed_base = urlparse(base_url)
        parsed_out = urlparse(full_url)
        
        if not is_valid_http_url(full_url):
            continue

        # Criteria for following a link:
        # If we are on a directory site (seed), we follow outlinks to other domains.
        # If we are on an actual site, we mostly stay on the same domain, or look for news keywords in the outlink domain.
        if is_directory and parsed_out.netloc != parsed_base.netloc:
            # Simple heuristing to avoid social media on directories
            if not any(bad in parsed_out.netloc for bad in ['facebook.com', 'twitter.com', 'instagram.com', 'linkedin.com', 'youtube.com']):
                 outlinks.add(full_url)
        elif parsed_out.netloc == parsed_base.netloc:
             outlinks.add(full_url)
        elif any(keyword in parsed_out.netloc.lower() for keyword in NEWS_KEYWORDS):
             # Try to discover new domains
             outlinks.add(f"{parsed_out.scheme}://{parsed_out.netloc}")
             
    return outlinks

def main():
    parser = argparse.ArgumentParser(description="RSS News Feed Discoverer")
    parser.add_argument('--seeds-file', type=str, default='seeds.txt', help='File with starting directory/hub URLs')
    parser.add_argument('--max-pages', type=int, default=100, help='Maximum pages to crawl')
    parser.add_argument('--workers', type=int, default=5, help='Concurrent threads')
    args = parser.parse_args()

    # Default seeds if no file
    seeds = [
        "https://news.google.com",
        "https://www.bbc.com/news",
        "https://timesofindia.indiatimes.com/",
        "https://www.aljazeera.com/",
        "https://en.wikipedia.org/wiki/List_of_newspapers_in_the_United_States",
        "https://en.wikipedia.org/wiki/List_of_international_broadcasters"
    ]
    
    if os.path.exists(args.seeds_file):
        with open(args.seeds_file, 'r') as f:
            file_seeds = [line.strip() for line in f if line.strip() and not line.startswith('#')]
            if file_seeds:
                seeds = file_seeds

    queue = [(seed, True) for seed in seeds] # Tuple of (URL, True if it's a hub/directory)
    visited = set()
    pages_crawled = 0

    print(f"Starting crawl with {len(seeds)} seed URLs...")

    current_feeds = load_feeds()
    print(f"Loaded {len(current_feeds)} existing feeds from {NEWS_FEEDS_FILE}")

    while queue and pages_crawled < args.max_pages:
        url, is_directory = queue.pop(0)
        
        # Normalize URL for visited set to prevent loops
        parsed_url = urlparse(url)
        norm_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}".rstrip("/")
        
        if norm_url in visited:
            continue
        visited.add(norm_url)
        
        print(f"Crawling ({pages_crawled}/{args.max_pages}): {url}")
        
        html, final_url = fetch_html(url)
        if not html:
            continue
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. Find and Save RSS Feeds
        found_feeds = find_rss_feeds(soup, final_url)
        for feed in found_feeds:
            # Basic validation: does it look like a feed URL?
            if is_valid_http_url(feed):
               save_feed(feed)
               
        # 2. Also try common paths for the domain
        if not is_directory: # Don't try to find standard feeds on wikipedia
           base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
           for path in COMMON_FEED_PATHS:
               save_feed(urljoin(base_domain, path)) # Will just try it, no guarantee it's XML unless we fetch it. We can refine this later.

        # 3. Find completely new links to continue crawling
        outlinks = extract_outlinks(soup, final_url, is_directory)
        for link in outlinks:
            norm_link = f"{urlparse(link).scheme}://{urlparse(link).netloc}{urlparse(link).path}".rstrip("/")
            if norm_link not in visited:
                queue.append((link, False)) # Found links are usually not hubs

        pages_crawled += 1
        time.sleep(0.5) # Polite scraping delay
        
    print(f"\nCrawl finished. Processed {pages_crawled} pages.")

if __name__ == '__main__':
    main()
