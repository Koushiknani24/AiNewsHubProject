from flask import Flask, jsonify, request, send_from_directory
import json
import os
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
    save_user_event,
)
from refresh_pipeline import (
    LATEST_REFRESH_FILE,
    NEWS_DATA_SCRIPT_FILE,
    load_live_sources,
    run_full_refresh_pipeline,
)
from search_pipeline import run_hybrid_search
from source_manager import source_count, source_metadata, source_stats

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
    "latest_update": None,
    "article_count": 0,
    "cluster_count": 0,
    "next_refresh_at": None,
    "run_count": 0,
    "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
}


@app.after_request
def allow_local_file_frontend(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    if request.path.startswith("/api/") or request.path in {"/summarizedNews.json", "/clusteredNews.json", "/newsData.js"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
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

    pipeline_status.update({
        "running": True,
        "phase": "starting refresh",
        "last_started": utc_now(),
        "last_finished": None,
        "last_error": None,
        "last_output": None,
    })

    def worker():
        def set_phase(phase):
            pipeline_status["phase"] = phase

        try:
            result = run_full_refresh_pipeline(status_callback=set_phase)
            latest_update = result["metadata"]["latest_update"]
            pipeline_status["last_success"] = latest_update
            pipeline_status["latest_update"] = latest_update
            pipeline_status["article_count"] = result["metadata"].get("article_count", 0)
            pipeline_status["cluster_count"] = result["metadata"].get("cluster_count", 0)
            pipeline_status["run_count"] += 1
            pipeline_status["last_output"] = (
                f"Regenerated {pipeline_status['cluster_count']} clusters "
                f"from {pipeline_status['article_count']} live articles."
            )
        except Exception as exc:
            pipeline_status["last_error"] = str(exc)[-4000:]
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

@app.route('/clusteredNews.json')
def serve_clustered_news_data():
    return send_from_directory('.', 'clusteredNews.json')

@app.route('/newsData.js')
def serve_news_data_script():
    return send_from_directory('.', NEWS_DATA_SCRIPT_FILE)

@app.route('/api/news')
def get_news():
    if not os.path.exists(DATA_FILE):
        db_data = load_clusters()
        if db_data:
            return jsonify(db_data)
        return jsonify({"error": "Data file not found. Please run the crawler pipeline first."}), 404
        
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
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
    status["sources_count"] = source_count()
    status["source_stats"] = source_stats()
    status["data_file_exists"] = os.path.exists(DATA_FILE)
    status["database"] = database_stats()
    if os.path.exists(LATEST_REFRESH_FILE):
        try:
            with open(LATEST_REFRESH_FILE, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            status["latest_update"] = metadata.get("latest_update")
            status["article_count"] = metadata.get("article_count", status.get("article_count", 0))
            status["cluster_count"] = metadata.get("cluster_count", status.get("cluster_count", 0))
            status["configured_source_count"] = metadata.get("configured_source_count", source_count())
            status["processed_source_count"] = metadata.get("processed_source_count")
        except Exception:
            pass
    if os.path.exists(DATA_FILE):
        status["data_updated_at"] = datetime.fromtimestamp(os.path.getmtime(DATA_FILE), timezone.utc).isoformat()
    if os.path.exists(NEWS_DATA_SCRIPT_FILE):
        status["news_data_script_updated_at"] = datetime.fromtimestamp(os.path.getmtime(NEWS_DATA_SCRIPT_FILE), timezone.utc).isoformat()
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
    return jsonify(run_hybrid_search(query))


@app.route('/api/sources', methods=['GET', 'POST', 'DELETE'])
def manage_sources():
    sources = load_sources()

    if request.method == 'GET':
        return jsonify({
            "custom_sources": sources,
            "custom_count": len(sources),
            "sources": source_metadata(),
            "count": source_count(),
            "stats": source_stats(),
        })

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
