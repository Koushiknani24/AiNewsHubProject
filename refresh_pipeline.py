import json
import os
import ssl
import tempfile
import threading
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlparse

from cluster import (
    HEADERS,
    CLUSTERED_NEWS_FILE,
    extract_entry_image,
    first_image_from_html,
    group_similar_articles,
    official_media_for_articles,
)
from database import replace_clusters
from news_ai import analyze_cluster, clean_html, source_domain_from_url
from source_manager import get_all_sources, get_source_urls, source_count
from summarize import SUMMARIZED_NEWS_FILE, generate_bullet_points

try:
    import feedparser
except ImportError:
    feedparser = None


NEWS_DATA_SCRIPT_FILE = "newsData.js"
NEWS_FEEDS_FILE = "newsFeeds.json"
LATEST_REFRESH_FILE = "latestRefresh.json"
IMAGE_CACHE_FILE = "imageCache.json"
MAX_ARTICLES_PER_REFRESH = int(os.getenv("MAX_ARTICLES_PER_REFRESH", "220"))
MAX_CLUSTERS_PER_REFRESH = int(os.getenv("MAX_CLUSTERS_PER_REFRESH", "80"))
BREAKING_HEADLINE_RESERVE = int(os.getenv("BREAKING_HEADLINE_RESERVE", "28"))
MAX_SOURCES_PER_REFRESH = int(os.getenv("MAX_SOURCES_PER_REFRESH", "220"))
ARTICLES_PER_FEED = int(os.getenv("ARTICLES_PER_FEED", "3"))
FEED_FETCH_TIMEOUT = float(os.getenv("FEED_FETCH_TIMEOUT", "2.5"))
GLOBAL_FETCH_TIMEOUT = float(os.getenv("GLOBAL_FETCH_TIMEOUT", "7.0"))
FEED_FETCH_WORKERS = int(os.getenv("FEED_FETCH_WORKERS", "80"))
REFRESH_DEEP_MEDIA = os.getenv("REFRESH_DEEP_MEDIA", "0") == "1"
IMAGE_FETCH_WORKERS = int(os.getenv("IMAGE_FETCH_WORKERS", "18"))
_image_cache = None
_image_cache_dirty = False
_image_cache_lock = threading.Lock()
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def update_phase(callback, phase):
    if callback:
        callback(phase)


def read_json_file(path, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def write_json_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, temp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def write_news_data_script(clusters):
    with open(NEWS_DATA_SCRIPT_FILE, "w", encoding="utf-8") as f:
        f.write("window.__NEWS_DATA__ = ")
        json.dump(clusters, f, ensure_ascii=False)
        f.write(";\n")


def load_image_cache():
    global _image_cache
    with _image_cache_lock:
        if _image_cache is None:
            _image_cache = read_json_file(IMAGE_CACHE_FILE, {})
            if not isinstance(_image_cache, dict):
                _image_cache = {}
        return _image_cache


def save_image_cache():
    global _image_cache_dirty
    with _image_cache_lock:
        if _image_cache is not None and _image_cache_dirty:
            write_json_atomic(IMAGE_CACHE_FILE, _image_cache)
            _image_cache_dirty = False


def cache_set(key, value):
    global _image_cache_dirty
    if not key:
        return
    cache = load_image_cache()
    with _image_cache_lock:
        cache[key] = value
        _image_cache_dirty = True


def article_image(article):
    return (
        article.get("image_url")
        or article.get("image")
        or article.get("thumbnail")
        or article.get("urlToImage")
    )


def article_cache_key(article):
    return (article.get("link") or article.get("title") or "").strip()


def source_logo_url(article):
    domain = (
        urlparse(article.get("source_homepage") or "").netloc
        or urlparse(article.get("link") or "").netloc
        or article.get("source_domain")
        or ""
    )
    domain = domain.lower().replace("www.", "")
    if not domain:
        return None
    if "." not in domain:
        return None
    return f"https://www.google.com/s2/favicons?domain={quote(domain)}&sz=256"


def resolve_cluster_media(cluster, allow_network=True):
    articles = [cluster.get("main_article"), *cluster.get("articles", [])]
    usable_articles = [article for article in articles if article]

    for article in usable_articles:
        image = article_image(article)
        if image:
            return {"image": image, "video": None, "source": "rss"}

    cache = load_image_cache()
    for article in usable_articles:
        cached = cache.get(article_cache_key(article))
        if cached:
            return cached

    if allow_network:
        media = official_media_for_articles(usable_articles)
        if media.get("image"):
            media["source"] = "publisher"
            for article in usable_articles:
                cache_set(article_cache_key(article), media)
            return media

    for article in usable_articles:
        logo = source_logo_url(article)
        if logo:
            media = {
                "image": logo,
                "video": None,
                "source": "source_logo",
                "is_logo_fallback": True,
            }
            cache_set(article_cache_key(article), media)
            return media

    return {"image": None, "video": None}


def load_live_sources():
    return get_source_urls(include_custom=True)


def parsed_timestamp(article):
    value = article.get("published", "")
    if not value:
        return 0
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0


def balanced_select(items, limit, key_getter, reserve=0, timestamp_getter=None):
    if len(items) <= limit:
        return items
    timestamp_getter = timestamp_getter or (lambda item: 0)
    sorted_items = sorted(items, key=timestamp_getter, reverse=True)
    selected = []
    seen_ids = set()

    for item in sorted_items[:max(0, min(reserve, limit))]:
        marker = id(item)
        selected.append(item)
        seen_ids.add(marker)

    groups = {}
    for item in sorted_items:
        marker = id(item)
        if marker in seen_ids:
            continue
        key = key_getter(item) or "Global"
        groups.setdefault(key, []).append(item)

    group_keys = sorted(groups, key=lambda key: (-len(groups[key]), key))
    while len(selected) < limit and group_keys:
        progressed = False
        for key in list(group_keys):
            queue = groups.get(key, [])
            if not queue:
                group_keys.remove(key)
                continue
            selected.append(queue.pop(0))
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected[:limit]


def normalize_feed_entry(entry, source):
    title = entry.get("title", "") if isinstance(entry, dict) else ""
    link = entry.get("link", "") if isinstance(entry, dict) else ""
    summary = entry.get("summary", "") if isinstance(entry, dict) else ""
    published = entry.get("published") or entry.get("updated") or ""
    image_url = extract_entry_image(entry, link or source.url)
    article_domain = source_domain_from_url(link) or urlparse(source.url).netloc or source.name
    if not title or not link:
        return None
    return {
        "source_domain": article_domain,
        "source_name": source.name,
        "source_country": source.country,
        "source_region": source.region,
        "source_category": source.category,
        "source_reliability": source.reliability,
        "source_homepage": f"{urlparse(source.url).scheme}://{urlparse(source.url).netloc}",
        "feed_url": source.url,
        "title": title,
        "link": link,
        "summary_html": summary,
        "published": published,
        "image_url": image_url,
    }


def parse_feed_without_feedparser_bytes(xml_text, source):
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    entries = []
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "media": "http://search.yahoo.com/mrss/",
    }
    for item in root.findall(".//item")[:ARTICLES_PER_FEED]:
        link_node = item.find("link")
        link = (link_node.text if link_node is not None else "").strip()
        summary = (item.findtext("description") or "").strip()
        media_node = item.find("media:content", namespaces) or item.find("media:thumbnail", namespaces)
        enclosure = item.find("enclosure")
        entry = {
            "title": (item.findtext("title") or "").strip(),
            "link": link,
            "summary": summary,
            "published": (item.findtext("pubDate") or "").strip(),
            "image": (
                media_node.get("url") if media_node is not None else
                enclosure.get("url") if enclosure is not None and str(enclosure.get("type", "")).startswith("image/") else
                first_image_from_html(summary)
            ),
        }
        article = normalize_feed_entry(entry, source)
        if article:
            entries.append(article)
    for item in root.findall(".//atom:entry", namespaces)[:ARTICLES_PER_FEED]:
        link_node = item.find("atom:link", namespaces)
        link = (link_node.get("href") if link_node is not None else "").strip()
        summary = (item.findtext("atom:summary", default="", namespaces=namespaces) or "").strip()
        media_node = item.find("media:content", namespaces) or item.find("media:thumbnail", namespaces)
        entry = {
            "title": (item.findtext("atom:title", default="", namespaces=namespaces) or "").strip(),
            "link": link,
            "summary": summary,
            "published": (item.findtext("atom:updated", default="", namespaces=namespaces) or "").strip(),
            "image": media_node.get("url") if media_node is not None else first_image_from_html(summary),
        }
        article = normalize_feed_entry(entry, source)
        if article:
            entries.append(article)
    return entries


def fetch_source_articles(source):
    try:
        req = urllib.request.Request(source.url, headers=HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=FEED_FETCH_TIMEOUT) as response:
            xml_text = response.read()
    except Exception:
        return []

    if feedparser:
        try:
            parsed = feedparser.parse(xml_text)
            articles = []
            for entry in parsed.entries[:ARTICLES_PER_FEED]:
                article = normalize_feed_entry(entry, source)
                if article:
                    articles.append(article)
            return articles
        except Exception:
            pass
    return parse_feed_without_feedparser_bytes(xml_text, source)


def prioritized_sources():
    frequency_rank = {"live": 0, "fast": 1, "standard": 2, "custom": 3}
    sources = sorted(
        get_all_sources(include_custom=True),
        key=lambda source: (frequency_rank.get(source.update_frequency, 4), -source.reliability, source.name),
    )
    return sources[:MAX_SOURCES_PER_REFRESH]


def fetch_latest_news(status_callback=None):
    update_phase(status_callback, "fetching latest global RSS feeds")
    sources = prioritized_sources()
    articles = []
    with ThreadPoolExecutor(max_workers=min(FEED_FETCH_WORKERS, max(8, len(sources)))) as executor:
        futures = [executor.submit(fetch_source_articles, source) for source in sources]
        try:
            for future in as_completed(futures, timeout=GLOBAL_FETCH_TIMEOUT):
                articles.extend(future.result())
        except TimeoutError:
            for future in futures:
                future.cancel()
    return articles


def preprocess_articles(articles, status_callback=None):
    update_phase(status_callback, "deduplicating and preprocessing articles")
    deduped = {}
    for article in articles:
        link = (article.get("link") or "").strip()
        title = " ".join((article.get("title") or "").split())
        if not link or not title:
            continue
        key = link.lower().split("?")[0] or title.lower()
        article["title"] = title
        article["summary_html"] = article.get("summary_html") or ""
        deduped[key] = article
    fresh_articles = sorted(deduped.values(), key=parsed_timestamp, reverse=True)
    return balanced_select(
        fresh_articles,
        MAX_ARTICLES_PER_REFRESH,
        key_getter=lambda article: article.get("source_country") or article.get("source_region"),
        reserve=BREAKING_HEADLINE_RESERVE,
        timestamp_getter=parsed_timestamp,
    )


def cluster_news(articles, status_callback=None):
    update_phase(status_callback, "running clustering algorithm")
    clusters = group_similar_articles(articles)
    clusters = sorted(
        clusters,
        key=lambda cluster: parsed_timestamp(cluster.get("main_article", {})),
        reverse=True,
    )
    clusters = balanced_select(
        clusters,
        MAX_CLUSTERS_PER_REFRESH,
        key_getter=lambda cluster: (cluster.get("main_article") or {}).get("source_country") or (cluster.get("main_article") or {}).get("source_region"),
        reserve=max(10, BREAKING_HEADLINE_RESERVE // 2),
        timestamp_getter=lambda cluster: parsed_timestamp(cluster.get("main_article", {})),
    )
    for index, cluster in enumerate(clusters):
        cluster["cluster_id"] = index
    return clusters


def cluster_text(cluster):
    articles = cluster.get("articles", [])
    return " ".join(
        f"{article.get('title', '')}. {clean_html(article.get('summary_html', ''))}"
        for article in articles
    )


def process_cluster_fast(cluster):
    main_article = cluster.get("main_article", {})
    text = cluster_text(cluster) or f"{main_article.get('title', '')}. {clean_html(main_article.get('summary_html', ''))}"
    bullets = generate_bullet_points(text)
    cluster["ai_summary_bullets"] = bullets
    cluster["neutral_summary"] = " ".join(bullets[:2]) if bullets else "Neutral summary unavailable."
    return analyze_cluster(cluster)


def generate_summaries(clusters, status_callback=None):
    update_phase(status_callback, "regenerating AI summaries")
    with ThreadPoolExecutor(max_workers=8) as executor:
        return list(executor.map(process_cluster_fast, clusters))


def run_sentiment_analysis(clusters, status_callback=None):
    update_phase(status_callback, "rerunning sentiment analysis")
    return clusters


def calculate_credibility(clusters, status_callback=None):
    update_phase(status_callback, "recalculating credibility")
    return clusters


def generate_bias_analysis(clusters, status_callback=None):
    update_phase(status_callback, "recalculating bias and risk")
    return clusters


def enrich_media(clusters, status_callback=None):
    update_phase(status_callback, "refreshing story media")
    def attach_media(cluster):
        cluster["media"] = resolve_cluster_media(cluster, allow_network=REFRESH_DEEP_MEDIA)
        return cluster

    with ThreadPoolExecutor(max_workers=IMAGE_FETCH_WORKERS) as executor:
        enriched = list(executor.map(attach_media, clusters))
    save_image_cache()
    return enriched


def save_updated_results(clusters, status_callback=None):
    update_phase(status_callback, "saving regenerated clusters")
    write_json_atomic(CLUSTERED_NEWS_FILE, clusters)
    write_json_atomic(SUMMARIZED_NEWS_FILE, clusters)
    write_news_data_script(clusters)
    replace_clusters(clusters)
    return clusters


def update_latest_timestamp(metadata=None, status_callback=None):
    update_phase(status_callback, "updating latest refresh timestamp")
    payload = {
        "latest_update": utc_now(),
        **(metadata or {}),
    }
    write_json_atomic(LATEST_REFRESH_FILE, payload)
    return payload


def run_full_refresh_pipeline(status_callback=None):
    articles = fetch_latest_news(status_callback)
    articles = preprocess_articles(articles, status_callback)
    if not articles:
        raise RuntimeError("No live articles were retrieved from RSS sources.")

    clusters = cluster_news(articles, status_callback)
    if not clusters:
        raise RuntimeError("No clusters were generated from live articles.")

    clusters = enrich_media(clusters, status_callback)
    clusters = generate_summaries(clusters, status_callback)
    clusters = run_sentiment_analysis(clusters, status_callback)
    clusters = calculate_credibility(clusters, status_callback)
    clusters = generate_bias_analysis(clusters, status_callback)
    clusters = save_updated_results(clusters, status_callback)
    metadata = update_latest_timestamp(
        {
            "article_count": len(articles),
            "cluster_count": len(clusters),
            "source_count": len(load_live_sources()),
            "configured_source_count": source_count(),
            "processed_source_count": min(MAX_SOURCES_PER_REFRESH, source_count()),
        },
        status_callback,
    )
    return {
        "clusters": clusters,
        "metadata": metadata,
    }
