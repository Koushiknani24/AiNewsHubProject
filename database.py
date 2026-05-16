import json
import os
import sqlite3
import importlib.util
from collections import Counter
from datetime import datetime, timezone


DB_FILE = "news_hub.db"
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "ai_news_hub")
POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
MYSQL_URL = os.getenv("MYSQL_URL")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clusters (
                cluster_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT,
                sentiment_label TEXT,
                sentiment_score INTEGER,
                credibility_score INTEGER,
                fake_news_label TEXT,
                fake_news_score INTEGER,
                source_count INTEGER,
                published TEXT,
                main_link TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                link TEXT PRIMARY KEY,
                cluster_id INTEGER,
                title TEXT,
                source_domain TEXT,
                feed_url TEXT,
                published TEXT,
                summary_html TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_category ON clusters(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clusters_sentiment ON clusters(sentiment_label)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_domain)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analytics_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def save_clusters(clusters):
    init_db()
    now = utc_now()
    with get_connection() as conn:
        for cluster in clusters:
            main = cluster.get("main_article", {})
            sentiment = cluster.get("sentiment", {})
            fake_news = cluster.get("fake_news", {})
            cluster_id = int(cluster.get("cluster_id", 0))
            conn.execute("""
                INSERT OR REPLACE INTO clusters (
                    cluster_id, title, category, sentiment_label, sentiment_score,
                    credibility_score, fake_news_label, fake_news_score, source_count,
                    published, main_link, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cluster_id,
                main.get("title", ""),
                cluster.get("category", "General"),
                sentiment.get("label", "Neutral"),
                sentiment.get("score", 0),
                cluster.get("credibility_score", 0),
                fake_news.get("label", "Needs verification"),
                fake_news.get("score", 0),
                len(cluster.get("sources", [])),
                main.get("published", ""),
                main.get("link", ""),
                json.dumps(cluster, ensure_ascii=False),
                now,
            ))

            for article in cluster.get("articles", []):
                link = article.get("link")
                if not link:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO articles (
                        link, cluster_id, title, source_domain, feed_url, published,
                        summary_html, payload_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    link,
                    cluster_id,
                    article.get("title", ""),
                    article.get("source_domain", ""),
                    article.get("feed_url", ""),
                    article.get("published", ""),
                    article.get("summary_html", ""),
                    json.dumps(article, ensure_ascii=False),
                    now,
                ))
    save_analytics_snapshot(clusters)
    sync_mongodb(clusters)


def replace_clusters(clusters):
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM articles")
        conn.execute("DELETE FROM clusters")
    save_clusters(clusters)


def build_analytics(clusters):
    categories = Counter(cluster.get("category", "General") for cluster in clusters)
    sentiments = Counter((cluster.get("sentiment") or {}).get("label", "Neutral") for cluster in clusters)
    avg_credibility = 0
    if clusters:
        avg_credibility = round(sum(cluster.get("credibility_score", 0) for cluster in clusters) / len(clusters))
    return {
        "total_clusters": len(clusters),
        "category_counts": dict(categories),
        "sentiment_counts": dict(sentiments),
        "average_credibility": avg_credibility,
        "generated_at": utc_now(),
    }


def save_analytics_snapshot(clusters):
    analytics = build_analytics(clusters)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO analytics_snapshots (payload_json, created_at) VALUES (?, ?)",
            (json.dumps(analytics, ensure_ascii=False), analytics["generated_at"]),
        )


def load_latest_analytics():
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT payload_json FROM analytics_snapshots ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return json.loads(row["payload_json"]) if row else build_analytics(load_clusters())


def save_user_event(user_id, event_type, payload):
    init_db()
    now = utc_now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_events (user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (user_id, event_type, json.dumps(payload, ensure_ascii=False), now),
        )
    return {"user_id": user_id, "event_type": event_type, "created_at": now}


def load_user_events(user_id, limit=100):
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT event_type, payload_json, created_at FROM user_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        {
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def sync_mongodb(clusters):
    if not MONGODB_URI or not importlib.util.find_spec("pymongo"):
        return
    try:
        from pymongo import MongoClient

        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=1500)
        db = client[MONGODB_DB]
        now = utc_now()
        for cluster in clusters:
            main = cluster.get("main_article", {})
            db.news_clusters.update_one(
                {"cluster_id": cluster.get("cluster_id")},
                {"$set": {**cluster, "updated_at": now}},
                upsert=True,
            )
            for article in cluster.get("articles", []):
                if article.get("link"):
                    db.news_articles.update_one(
                        {"link": article["link"]},
                        {"$set": {**article, "cluster_id": cluster.get("cluster_id"), "updated_at": now}},
                        upsert=True,
                    )
        db.analytics_snapshots.insert_one(build_analytics(clusters))
    except Exception:
        return


def load_clusters(limit=100, category=None, sentiment=None):
    init_db()
    query = "SELECT payload_json FROM clusters"
    params = []
    filters = []
    if category:
        filters.append("category = ?")
        params.append(category)
    if sentiment:
        filters.append("sentiment_label = ?")
        params.append(sentiment)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += " ORDER BY updated_at DESC, published DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def database_stats():
    init_db()
    with get_connection() as conn:
        cluster_count = conn.execute("SELECT COUNT(*) AS count FROM clusters").fetchone()["count"]
        article_count = conn.execute("SELECT COUNT(*) AS count FROM articles").fetchone()["count"]
        latest = conn.execute("SELECT MAX(updated_at) AS latest FROM clusters").fetchone()["latest"]
        analytics_count = conn.execute("SELECT COUNT(*) AS count FROM analytics_snapshots").fetchone()["count"]
        user_events = conn.execute("SELECT COUNT(*) AS count FROM user_events").fetchone()["count"]
    return {
        "database": DB_FILE,
        "clusters": cluster_count,
        "articles": article_count,
        "analytics_snapshots": analytics_count,
        "user_events": user_events,
        "last_updated": latest,
        "features": database_features(),
    }


def database_features():
    mongo_driver = importlib.util.find_spec("pymongo") is not None
    postgres_driver = importlib.util.find_spec("psycopg2") is not None or importlib.util.find_spec("psycopg") is not None
    mysql_driver = importlib.util.find_spec("mysql") is not None or importlib.util.find_spec("pymysql") is not None
    return {
        "store_news_articles": {
            "suggested_tech": "MongoDB",
            "status": "active" if MONGODB_URI and mongo_driver else "sqlite_fallback",
            "fallback": "SQLite articles table",
            "env": "MONGODB_URI",
        },
        "save_user_data": {
            "suggested_tech": "PostgreSQL/MySQL",
            "status": "configured" if (POSTGRES_URL and postgres_driver) or (MYSQL_URL and mysql_driver) else "sqlite_fallback",
            "fallback": "SQLite user_events table",
            "env": "POSTGRES_URL or MYSQL_URL",
        },
        "save_analytics": {
            "suggested_tech": "MongoDB",
            "status": "active" if MONGODB_URI and mongo_driver else "sqlite_fallback",
            "fallback": "SQLite analytics_snapshots table",
            "env": "MONGODB_URI",
        },
    }
