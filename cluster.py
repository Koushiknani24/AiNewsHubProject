import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
import urllib.request
import urllib.error
import ssl
from concurrent.futures import ThreadPoolExecutor
from news_ai import analyze_cluster, clean_html, source_domain_from_url

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None

NEWS_FEEDS_FILE = 'newsFeeds.json'
CLUSTERED_NEWS_FILE = 'clusteredNews.json'
SIMILARITY_THRESHOLD = 0.65 # Adjust as needed. >0.65 usually means it's the same core story

# Added for Trust & Bias calculation
SOURCE_RATINGS = {
    # Format: "domain": {"trust": score_out_of_10, "bias": score_from_-5_to_5}
    # Bias: -5 (Far Left), -2 (Left), 0 (Center), +2 (Right), +5 (Far Right)
    "bbc.com": {"trust": 3.0, "bias": -1},
    "news.google.com": {"trust": 7.0, "bias": 0},
    "timesofindia.indiatimes.com": {"trust": 7.5, "bias": 1},
    "aljazeera.com": {"trust": 4.0, "bias": -2},
    "reuters.com": {"trust": 9.5, "bias": 0},
    "apnews.com": {"trust": 9.5, "bias": 0},
    "foxnews.com": {"trust": 4.0, "bias": 4},
    "cnn.com": {"trust": 5.0, "bias": -3},
    "npr.org": {"trust": 8.0, "bias": -2},
    "nytimes.com": {"trust": 7.5, "bias": -2},
    "wsj.com": {"trust": 8.0, "bias": 1},
    "washingtonpost.com": {"trust": 7.5, "bias": -2},
    "theguardian.com": {"trust": 7.0, "bias": -3},
    "bloomberg.com": {"trust": 8.5, "bias": 0},
    "cnbc.com": {"trust": 8.0, "bias": 0},
}

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}


def safe_log(message):
    try:
        print(message)
    except UnicodeEncodeError:
        print(str(message).encode("ascii", errors="replace").decode("ascii"))


def first_image_from_html(html):
    if not html:
        return None
    if BeautifulSoup:
        soup = BeautifulSoup(html, 'html.parser')
        image = soup.find('img')
        if image:
            return image.get('src')
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_entry_image(entry, base_url):
    candidates = []
    for key in ("media_content", "media_thumbnail"):
        for item in entry.get(key, []) or []:
            candidates.append(item.get("url"))
    for enclosure in entry.get("enclosures", []) or []:
        if str(enclosure.get("type", "")).startswith("image/"):
            candidates.append(enclosure.get("href") or enclosure.get("url"))
    for link in entry.get("links", []) or []:
        if str(link.get("type", "")).startswith("image/"):
            candidates.append(link.get("href"))
    candidates.append(entry.get("image"))
    candidates.append(first_image_from_html(entry.get("summary", "")))
    normalized = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("href") or candidate.get("url")
        normalized.append(candidate)
    image = next((candidate for candidate in normalized if isinstance(candidate, str) and candidate.strip()), None)
    return urljoin(base_url, image) if image else None

def load_feeds():
    if os.path.exists(NEWS_FEEDS_FILE):
        with open(NEWS_FEEDS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def extract_media(url):
    """Fetches the article URL to grab high quality og:image/og:video if missing from RSS."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            html = response.read().decode("utf-8", errors="ignore")
            image_url = None
            video_url = None

            if BeautifulSoup:
                soup = BeautifulSoup(html, 'html.parser')

                og_image = soup.find('meta', property='og:image')
                if og_image: image_url = og_image.get('content')

                og_video = soup.find('meta', property='og:video')
                if og_video: video_url = og_video.get('content')

                if not image_url:
                    secure_image = soup.find('meta', property='og:image:secure_url')
                    if secure_image: image_url = secure_image.get('content')

                if not image_url:
                    tw_image = soup.find('meta', attrs={'name': 'twitter:image'}) or soup.find('meta', property='twitter:image')
                    if tw_image: image_url = tw_image.get('content')

            if not image_url:
                image_url = (
                    extract_meta_content(html, ["og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"])
                    or extract_link_href(html, ["image_src"])
                    or extract_jsonld_image(html)
                )
            if not video_url:
                video_url = extract_meta_content(html, ["og:video", "twitter:player"])

            return {
                "image": urljoin(url, image_url) if image_url else None,
                "video": urljoin(url, video_url) if video_url else None,
            }
    except Exception as e:
        return {"image": None, "video": None}


def extract_meta_content(html, names):
    names = {name.lower() for name in names}
    meta_tags = re.findall(r"<meta\b[^>]*>", html, flags=re.IGNORECASE)
    for tag in meta_tags:
        key_match = re.search(r"(?:property|name)=['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if not key_match or key_match.group(1).strip().lower() not in names:
            continue
        content_match = re.search(r"content=['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if content_match:
            return content_match.group(1)
    return None


def extract_link_href(html, rel_names):
    rel_names = {name.lower() for name in rel_names}
    link_tags = re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE)
    for tag in link_tags:
        rel_match = re.search(r"rel=['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if not rel_match:
            continue
        rel_values = {value.lower() for value in rel_match.group(1).split()}
        if not rel_values & rel_names:
            continue
        href_match = re.search(r"href=['\"]([^'\"]+)['\"]", tag, flags=re.IGNORECASE)
        if href_match:
            return href_match.group(1)
    return None


def extract_jsonld_image(html):
    scripts = re.findall(
        r"<script\b[^>]*type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for script in scripts:
        try:
            data = json.loads(script.strip())
        except Exception:
            continue
        image = find_json_image(data)
        if image:
            return image
    return None


def find_json_image(value):
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://", "//", "/")) else None
    if isinstance(value, list):
        for item in value:
            image = find_json_image(item)
            if image:
                return image
    if isinstance(value, dict):
        image_value = value.get("image")
        if isinstance(image_value, str):
            return image_value
        if isinstance(image_value, dict):
            return image_value.get("url") or image_value.get("contentUrl")
        if isinstance(image_value, list):
            image = find_json_image(image_value)
            if image:
                return image
        for key in ("thumbnailUrl", "url", "contentUrl"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.lower().startswith(("http://", "https://", "//", "/")):
                return candidate
    return None


def official_media_for_articles(articles):
    for article in articles:
        link = article.get("link")
        if not link:
            continue
        media = extract_media(link)
        if media.get("image"):
            return media
    for article in articles:
        image = article_image_url(article)
        if image:
            return {"image": image, "video": None}
    return {"image": None, "video": None}


def article_image_url(article):
    return (
        article.get("image_url")
        or article.get("image")
        or article.get("thumbnail")
        or article.get("urlToImage")
    )

def fetch_articles(feed_url):
    print(f"Fetching feed: {feed_url}")
    if feedparser:
        parsed_entries = feedparser.parse(feed_url).entries
    else:
        parsed_entries = parse_feed_without_feedparser(feed_url)
    articles = []
    
    source_domain = urlparse(feed_url).netloc
    
    for entry in parsed_entries[:10]: # Limit to top 10 per feed to avoid overload
        title = entry.get('title', '')
        link = entry.get('link', '')
        summary = entry.get('summary', '') # often contains HTML or short text
        published = entry.get('published', '')
        image_url = extract_entry_image(entry, link or feed_url)
        article_domain = source_domain_from_url(link) or source_domain
        
        # Basic cleanup
        if not title or not link: continue
        
        articles.append({
            'source_domain': article_domain,
            'feed_url': feed_url,
            'title': title,
            'link': link,
            'summary_html': summary,
            'published': published,
            'image_url': image_url
        })
    return articles


def parse_feed_without_feedparser(feed_url):
    try:
        req = urllib.request.Request(feed_url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=12) as response:
            xml_text = response.read()
        root = ET.fromstring(xml_text)
    except Exception as exc:
        print(f"[-] Feed parse failed for {feed_url}: {exc}")
        return []

    entries = []
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
    }
    rss_items = root.findall(".//item")
    atom_items = root.findall(".//atom:entry", namespaces)
    for item in rss_items:
        link_node = item.find("link")
        link = (link_node.text if link_node is not None else "").strip()
        summary = (item.findtext("description") or "").strip()
        media_node = item.find("media:content", namespaces) or item.find("media:thumbnail", namespaces)
        enclosure = item.find("enclosure")
        entries.append({
            "title": (item.findtext("title") or "").strip(),
            "link": link,
            "summary": summary,
            "published": (item.findtext("pubDate") or "").strip(),
            "image": (
                media_node.get("url") if media_node is not None else
                enclosure.get("url") if enclosure is not None and str(enclosure.get("type", "")).startswith("image/") else
                first_image_from_html(summary)
            ),
        })
    for item in atom_items:
        link_node = item.find("atom:link", namespaces)
        link = (link_node.get("href") if link_node is not None else "").strip()
        summary = (item.findtext("atom:summary", default="", namespaces=namespaces) or "").strip()
        media_node = item.find("media:content", namespaces) or item.find("media:thumbnail", namespaces)
        entries.append({
            "title": (item.findtext("atom:title", default="", namespaces=namespaces) or "").strip(),
            "link": link,
            "summary": summary,
            "published": (item.findtext("atom:updated", default="", namespaces=namespaces) or "").strip(),
            "image": (
                media_node.get("url") if media_node is not None else
                first_image_from_html(summary)
            ),
        })
    return entries

def calculate_cluster_metrics(cluster_articles):
    total_trust = 0
    total_bias = 0
    count = 0
    
    for a in cluster_articles:
        domain = a['source_domain'].lower().replace('www.', '')
        
        # Simple domain matching
        matched_rating = {"trust": 5.0, "bias": 0.0} # default neutral
        for known_domain, rating in SOURCE_RATINGS.items():
            if known_domain in domain:
                matched_rating = rating
                break
                
        source_reliability = a.get("source_reliability")
        total_trust += (float(source_reliability) / 10.0) if source_reliability else matched_rating['trust']
        total_bias += matched_rating['bias']
        count += 1
        
    if count == 0:
        return {"trust_score": 5.0, "bias_meter": 0.0}
        
    return {
        "trust_score": round(total_trust / count, 1),
        "bias_meter": round(total_bias / count, 1)
    }

def group_similar_articles(articles):
    if not articles: return []
    
    # 1. Prepare texts for TF-IDF (combine title and a bit of summary stripped of HTML)
    texts = []
    for a in articles:
        # Strip simple HTML from summary for text comp
        raw_text = clean_html(a.get('summary_html', ''))
        combined = f"{a['title']} {raw_text[:200]}" # Focus on title and first 200 chars
        texts.append(combined)
        
    if not TfidfVectorizer or not cosine_similarity:
        return group_by_token_overlap(articles, texts)

    # 2. Vectorize
    vectorizer = TfidfVectorizer(stop_words='english')
    try:
        tfidf_matrix = vectorizer.fit_transform(texts)
    except ValueError:
        return [{ "cluster_id": 0, "articles": articles, "main_article": articles[0] }]
        
    similarity_matrix = cosine_similarity(tfidf_matrix)
    
    # 3. Cluster
    clusters = []
    visited_indices = set()
    
    for i in range(len(articles)):
        if i in visited_indices: continue
        
        current_cluster = [articles[i]]
        visited_indices.add(i)
        
        for j in range(i + 1, len(articles)):
            if j not in visited_indices and similarity_matrix[i][j] >= SIMILARITY_THRESHOLD:
                current_cluster.append(articles[j])
                visited_indices.add(j)
                
        metrics = calculate_cluster_metrics(current_cluster)
        
        cluster = {
            "cluster_id": len(clusters),
            "main_article": current_cluster[0], # The first one acts as the representative master article
            "sources": [{"domain": a['source_domain'], "link": a['link']} for a in current_cluster],
            "articles": current_cluster,
            "trust_score": metrics["trust_score"],
            "bias_meter": metrics["bias_meter"]
        }
        clusters.append(analyze_cluster(cluster))
        
    return clusters


def text_tokens(text):
    return set(re.findall(r"[a-zA-Z][a-zA-Z]{3,}", text.lower()))


def group_by_token_overlap(articles, texts):
    clusters = []
    visited_indices = set()
    token_sets = [text_tokens(text) for text in texts]

    for i in range(len(articles)):
        if i in visited_indices:
            continue
        current_cluster = [articles[i]]
        visited_indices.add(i)

        for j in range(i + 1, len(articles)):
            if j in visited_indices:
                continue
            union = token_sets[i] | token_sets[j]
            overlap = len(token_sets[i] & token_sets[j]) / len(union) if union else 0
            if overlap >= 0.22:
                current_cluster.append(articles[j])
                visited_indices.add(j)

        metrics = calculate_cluster_metrics(current_cluster)
        cluster = {
            "cluster_id": len(clusters),
            "main_article": current_cluster[0],
            "sources": [{"domain": a['source_domain'], "link": a['link']} for a in current_cluster],
            "articles": current_cluster,
            "trust_score": metrics["trust_score"],
            "bias_meter": metrics["bias_meter"]
        }
        clusters.append(analyze_cluster(cluster))
    return clusters

def enrich_cluster_with_media(cluster):
    safe_log(f"Enriching media for cluster: {cluster['main_article']['title'][:50]}...")
    articles = [cluster.get("main_article"), *cluster.get("articles", [])]
    media = official_media_for_articles([article for article in articles if article])
    cluster['media'] = media
    return cluster

def main():
    feeds = load_feeds()
    if not feeds:
        print(f"No feeds found in {NEWS_FEEDS_FILE}. Run crawler.py first.")
        return
        
    print(f"Loaded {len(feeds)} feeds. Downloading articles...")
    all_articles = []
    
    # Fetch feeds concurrently
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(fetch_articles, feeds)
        for res in results:
            all_articles.extend(res)
            
    print(f"\nTotal articles fetched: {len(all_articles)}")
    
    print("Clustering identical news stories...")
    clusters = group_similar_articles(all_articles)
    print(f"Grouped into {len(clusters)} unique news stories.")
    
    print("\nExtracting rich media (images/videos) for each cluster...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        clusters = list(executor.map(enrich_cluster_with_media, clusters))
    
    # Save the output
    with open(CLUSTERED_NEWS_FILE, 'w', encoding='utf-8') as f:
        json.dump(clusters, f, indent=4)
        
    print(f"\nDone! Saved clustered news with media to {CLUSTERED_NEWS_FILE}")

if __name__ == "__main__":
    main()
