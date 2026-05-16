import json
import os
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from cluster import first_image_from_html, group_similar_articles
from database import load_clusters
from news_ai import analyze_cluster, clean_html, source_domain_from_url
from refresh_pipeline import (
    CLUSTERED_NEWS_FILE,
    SUMMARIZED_NEWS_FILE,
    cluster_news,
    generate_summaries,
    preprocess_articles,
    resolve_cluster_media,
    save_image_cache,
)

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None


LOCAL_RESULT_LIMIT = 12
LIVE_RESULT_LIMIT = 25
MIN_LOCAL_RESULTS = 4
MIN_LOCAL_SCORE = 0.12
MAX_LOCAL_AGE_HOURS = 72

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def cluster_search_text(cluster):
    main = cluster.get("main_article", {})
    parts = [
        main.get("title", ""),
        main.get("source_domain", ""),
        cluster.get("category", ""),
        cluster.get("neutral_summary", ""),
        " ".join(cluster.get("ai_summary_bullets", []) or []),
        " ".join(cluster.get("trending_topics", []) or []),
    ]
    for article in cluster.get("articles", [])[:5]:
        parts.extend([article.get("title", ""), clean_html(article.get("summary_html", ""))])
    return " ".join(str(part) for part in parts if part)


def parse_datetime(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None


def cluster_age_hours(cluster):
    published = parse_datetime((cluster.get("main_article") or {}).get("published", ""))
    if not published:
        return 9999
    return max(0, (datetime.now(timezone.utc) - published).total_seconds() / 3600)


def load_local_memory():
    seen = set()
    memory = []
    sources = [load_clusters(limit=250), load_json(SUMMARIZED_NEWS_FILE), load_json(CLUSTERED_NEWS_FILE)]
    for source in sources:
        for cluster in source:
            main = cluster.get("main_article", {})
            key = main.get("link") or main.get("title") or cluster.get("cluster_id")
            if key in seen:
                continue
            seen.add(key)
            memory.append(cluster)
    return memory


def token_score(query, text):
    query_tokens = set(re.findall(r"[a-zA-Z0-9][a-zA-Z0-9-]{2,}", query.lower()))
    text_tokens = set(re.findall(r"[a-zA-Z0-9][a-zA-Z0-9-]{2,}", text.lower()))
    if not query_tokens or not text_tokens:
        return 0
    overlap = len(query_tokens & text_tokens) / len(query_tokens)
    phrase_bonus = 0.2 if query.lower() in text.lower() else 0
    return min(1.0, overlap + phrase_bonus)


def search_local_memory(query):
    memory = load_local_memory()
    if not memory:
        return []

    texts = [cluster_search_text(cluster) for cluster in memory]
    scores = []
    if TfidfVectorizer and cosine_similarity and len(memory) > 1:
        try:
            matrix = TfidfVectorizer(stop_words="english", ngram_range=(1, 2)).fit_transform([query, *texts])
            tfidf_scores = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
            scores = [max(float(score), token_score(query, text)) for score, text in zip(tfidf_scores, texts)]
        except Exception:
            scores = [token_score(query, text) for text in texts]
    else:
        scores = [token_score(query, text) for text in texts]

    ranked = []
    for cluster, score in zip(memory, scores):
        if score <= 0:
            continue
        copy = dict(cluster)
        copy["search_score"] = round(score, 4)
        copy["result_type"] = "local"
        copy["is_live_result"] = False
        ranked.append(copy)

    ranked.sort(key=lambda cluster: (cluster.get("search_score", 0), -cluster_age_hours(cluster)), reverse=True)
    return ranked[:LOCAL_RESULT_LIMIT]


def local_results_are_sufficient(results):
    if len(results) < MIN_LOCAL_RESULTS:
        return False
    if max((item.get("search_score", 0) for item in results), default=0) < MIN_LOCAL_SCORE:
        return False
    fresh_count = sum(1 for item in results if cluster_age_hours(item) <= MAX_LOCAL_AGE_HOURS)
    return fresh_count >= min(2, len(results))


def google_news_url(query):
    return "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en".format(
        urllib.parse.quote(query)
    )


def fetch_google_news_articles(query):
    url = google_news_url(query)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=ctx, timeout=12) as response:
        xml_text = response.read()
    root = ET.fromstring(xml_text)

    articles = []
    for item in root.findall(".//item")[:LIVE_RESULT_LIMIT]:
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source_name = source_node.text.strip() if source_node is not None and source_node.text else ""
        source_url = source_node.get("url") if source_node is not None else ""
        title = raw_title
        if " - " in raw_title and not source_name:
            title, source_name = raw_title.rsplit(" - ", 1)
        domain = source_domain_from_url(source_url) or source_domain_from_url(link) or source_name or "Google News"
        image_url = first_image_from_html(summary)
        articles.append({
            "source_domain": source_name or domain,
            "source_homepage": source_url,
            "feed_url": url,
            "title": title,
            "link": link,
            "summary_html": summary,
            "published": published,
            "image_url": image_url,
        })
    return articles


def analyze_live_results(query):
    raw_articles = fetch_google_news_articles(query)
    articles = preprocess_articles(raw_articles)
    clusters = cluster_news(articles)
    clusters = generate_summaries(clusters)
    for index, cluster in enumerate(clusters):
        cluster = analyze_cluster(cluster)
        cluster["cluster_id"] = f"live_search_{index}"
        cluster["result_type"] = "live"
        cluster["is_live_result"] = True
        cluster["media"] = resolve_cluster_media(cluster, allow_network=True)
        cluster["search_score"] = token_score(query, cluster_search_text(cluster))
        clusters[index] = cluster
    save_image_cache()
    return clusters


def run_hybrid_search(query):
    query = " ".join((query or "").split())
    if not query:
        return {
            "query": "",
            "mode": "empty",
            "steps": [],
            "clusters": [],
            "generated_at": utc_now(),
        }

    steps = ["Searching local intelligence memory..."]
    local_results = search_local_memory(query)
    if local_results_are_sufficient(local_results):
        steps.append("Local AI memory match found.")
        return {
            "query": query,
            "mode": "local",
            "steps": steps,
            "clusters": local_results,
            "local_count": len(local_results),
            "live_count": 0,
            "generated_at": utc_now(),
        }

    steps.append("No strong local match found.")
    steps.append("Fetching live global news...")
    live_results = []
    live_error = None
    try:
        live_results = analyze_live_results(query)
        steps.append("Running AI analysis...")
        if live_results:
            steps.append("Clusters generated successfully.")
        else:
            steps.append("No live global match returned by Google News.")
    except Exception as exc:
        live_error = str(exc)
        steps.append("Live global search failed.")

    clusters = live_results or local_results
    mode = "live" if live_results else "local_fallback"
    return {
        "query": query,
        "mode": mode,
        "steps": steps,
        "clusters": clusters,
        "local_count": len(local_results),
        "live_count": len(live_results),
        "error": live_error,
        "generated_at": utc_now(),
    }
