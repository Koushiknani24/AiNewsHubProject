from flask import Flask, jsonify, request, send_from_directory
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from ai_concepts import get_ai_concepts
from database import (
    database_features,
    database_stats,
    init_db,
    load_clusters,
    load_latest_analytics,
    load_user_events,
    save_clusters,
    save_user_event,
)

app = Flask(__name__, static_folder='static', template_folder='.')

DATA_FILE = 'summarizedNews.json'
NEWS_FEEDS_FILE = 'newsFeeds.json'
REFRESH_INTERVAL_SECONDS = 600
AUTO_REFRESH_ON_STARTUP = True

pipeline_lock = threading.Lock()
pipeline_status = {
    "running": False,
    "phase": "idle",
    "last_started": None,
    "last_finished": None,
    "last_success": None,
    "last_error": None,
    "last_output": None,
    "next_refresh_at": None,
    "run_count": 0,
    "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
}


@app.after_request
def allow_local_file_frontend(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def utc_now_dt():
    return datetime.now(timezone.utc)


def load_sources():
    if not os.path.exists(NEWS_FEEDS_FILE):
        return []
    with open(NEWS_FEEDS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_sources(sources):
    cleaned = []
    seen = set()
    for source in sources:
        source = source.strip()
        if source and source not in seen:
            cleaned.append(source)
            seen.add(source)
    with open(NEWS_FEEDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cleaned, f, indent=4)


def run_refresh_pipeline():
    if not pipeline_lock.acquire(blocking=False):
        return False, "Refresh already running."

    def worker():
        pipeline_status.update({
            "running": True,
            "phase": "fetching and clustering",
            "last_started": utc_now(),
            "last_finished": None,
            "last_error": None,
            "last_output": None,
        })
        combined_output = []
        try:
            commands = [
                ("fetching and clustering", [sys.executable, "cluster.py"]),
                ("summarizing and scoring", [sys.executable, "summarize.py"]),
            ]
            for phase, command in commands:
                pipeline_status["phase"] = phase
                env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
                result = subprocess.run(command, cwd=os.getcwd(), env=env, check=True, capture_output=True, text=True, encoding="utf-8", timeout=540)
                combined_output.append(result.stdout[-2000:])
            pipeline_status["last_success"] = utc_now()
            pipeline_status["run_count"] += 1
            pipeline_status["last_output"] = "\n".join(combined_output)[-4000:]
        except Exception as exc:
            error = str(exc)
            if isinstance(exc, subprocess.CalledProcessError):
                error = (exc.stderr or exc.stdout or str(exc))[-4000:]
            pipeline_status["last_error"] = error
        finally:
            pipeline_status["running"] = False
            pipeline_status["phase"] = "idle"
            pipeline_status["last_finished"] = utc_now()
            pipeline_status["next_refresh_at"] = (utc_now_dt() + timedelta(seconds=REFRESH_INTERVAL_SECONDS)).isoformat()
            pipeline_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return True, "Refresh started."


def scheduled_refresh_loop():
    while True:
        pipeline_status["next_refresh_at"] = (utc_now_dt() + timedelta(seconds=REFRESH_INTERVAL_SECONDS)).isoformat()
        time.sleep(REFRESH_INTERVAL_SECONDS)
        run_refresh_pipeline()


def start_background_services():
    init_db()
    pipeline_status["next_refresh_at"] = (utc_now_dt() + timedelta(seconds=REFRESH_INTERVAL_SECONDS)).isoformat()
    threading.Thread(target=scheduled_refresh_loop, daemon=True).start()
    if AUTO_REFRESH_ON_STARTUP:
        run_refresh_pipeline()

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/summarizedNews.json')
def serve_static_news_data():
    return send_from_directory('.', DATA_FILE)

@app.route('/api/news')
def get_news():
    if not os.path.exists(DATA_FILE):
        db_data = load_clusters()
        if db_data:
            return jsonify(db_data)
        return jsonify({"error": "Data file not found. Please run the crawler pipeline first."}), 404
        
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if data:
        save_clusters(data)
    return jsonify(data)


@app.route('/api/db/news')
def get_database_news():
    limit = min(int(request.args.get("limit", 100)), 250)
    category = request.args.get("category") or None
    sentiment = request.args.get("sentiment") or None
    return jsonify(load_clusters(limit=limit, category=category, sentiment=sentiment))


@app.route('/api/db/stats')
def get_database_stats():
    return jsonify(database_stats())


@app.route('/api/db/features')
def get_database_features():
    return jsonify(database_features())


@app.route('/api/db/analytics')
def get_database_analytics():
    return jsonify(load_latest_analytics())


@app.route('/api/user/events', methods=['GET', 'POST'])
def user_events():
    if request.method == 'GET':
        user_id = request.args.get("user_id", "anonymous")
        limit = min(int(request.args.get("limit", 100)), 250)
        return jsonify({"user_id": user_id, "events": load_user_events(user_id, limit=limit)})

    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id", "anonymous")
    event_type = body.get("event_type", "interaction")
    payload = body.get("payload", {})
    return jsonify(save_user_event(user_id, event_type, payload)), 201


@app.route('/api/status')
def get_status():
    status = dict(pipeline_status)
    status["sources_count"] = len(load_sources())
    status["data_file_exists"] = os.path.exists(DATA_FILE)
    status["database"] = database_stats()
    if os.path.exists(DATA_FILE):
        status["data_updated_at"] = datetime.fromtimestamp(os.path.getmtime(DATA_FILE), timezone.utc).isoformat()
    return jsonify(status)


@app.route('/api/ai-concepts')
def get_ai_stack():
    return jsonify({"concepts": get_ai_concepts()})


@app.route('/api/refresh', methods=['POST'])
def refresh_news():
    started, message = run_refresh_pipeline()
    return jsonify({"started": started, "message": message, "status": pipeline_status}), 202 if started else 409


@app.route('/api/search')
def live_search():
    query = request.args.get("q", "")
    if not query:
        return jsonify([])
    
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET
    import ssl
    import re
    from news_ai import clean_html, classify_category, sentiment_analysis, bias_intensity, claim_agreement, fake_news_assessment, source_rating, trending_topics
    
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, context=ctx, timeout=12) as response:
            xml_text = response.read()
        root = ET.fromstring(xml_text)
    except Exception as exc:
        print(f"[-] Search feed parse failed: {exc}")
        return jsonify([])

    clusters = []
    rss_items = root.findall(".//item")
    for i, item in enumerate(rss_items[:15]):
        link_node = item.find("link")
        link = (link_node.text if link_node is not None else "").strip()
        summary = (item.findtext("description") or "").strip()
        title = (item.findtext("title") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        
        domain = urllib.parse.urlparse(link).netloc.lower().replace("www.", "")
        if "news.google.com" in domain:
            source_name = title.split(" - ")[-1] if " - " in title else "Google News"
        else:
            source_name = domain

        match = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", summary, flags=re.IGNORECASE)
        image_url = match.group(1) if match else None

        article = {
            "source_domain": source_name,
            "title": title,
            "link": link,
            "summary_html": summary,
            "published": published,
            "image_url": image_url
        }

        # Real AI analysis for the live search result
        clean_summary = clean_html(summary)
        text = f"{title}. {clean_summary}"
        rating = source_rating(source_name)["score"]
        agreement = claim_agreement([article])
        fake_news = fake_news_assessment([article], rating, agreement, text)

        cluster = {
            "cluster_id": f"search_{i}",
            "main_article": article,
            "articles": [article],
            "sources": [{"domain": source_name, "link": link}],
            "trust_score": rating / 10.0,
            "bias_meter": 0,
            "category": classify_category(text),
            "ai_summary_bullets": [clean_summary[:300] + "..."],
            "credibility_score": fake_news["score"],
            "claim_analysis": agreement,
            "fake_news": fake_news,
            "sentiment": sentiment_analysis(text),
            "bias_intensity": bias_intensity(0, text),
            "trending_topics": trending_topics(text)
        }
        clusters.append(cluster)

    return jsonify(clusters)


@app.route('/api/sources', methods=['GET', 'POST', 'DELETE'])
def manage_sources():
    sources = load_sources()

    if request.method == 'GET':
        return jsonify({"sources": sources, "count": len(sources)})

    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        source = (body.get("url") or "").strip()
        if not source.startswith(("http://", "https://")):
            return jsonify({"error": "Source must be an http or https RSS URL."}), 400
        if source not in sources:
            sources.append(source)
            save_sources(sources)
        return jsonify({"sources": sources, "count": len(sources)})

    source = (request.args.get("url") or "").strip()
    if source not in sources:
        return jsonify({"error": "Source not found."}), 404
    sources = [item for item in sources if item != source]
    save_sources(sources)
    return jsonify({"sources": sources, "count": len(sources)})

if __name__ == '__main__':
    start_background_services()
    print("Starting AI News Hub on http://localhost:5000")
    app.run(debug=True, port=5000, use_reloader=False)
