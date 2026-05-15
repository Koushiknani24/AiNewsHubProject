import json
import os
from concurrent.futures import ThreadPoolExecutor

from cluster import official_media_for_articles
from database import save_clusters

DATA_FILES = ["clusteredNews.json", "summarizedNews.json"]


def article_image(article):
    return (
        article.get("image_url")
        or article.get("image")
        or article.get("thumbnail")
        or article.get("urlToImage")
    )


def first_saved_image(cluster):
    media = cluster.get("media") or {}
    if media.get("image"):
        return media["image"]
    for article in [cluster.get("main_article"), *cluster.get("articles", [])]:
        if article and article_image(article):
            return article_image(article)
    return None


def enrich_cluster(cluster):
    if first_saved_image(cluster):
        return cluster

    articles = [cluster.get("main_article"), *cluster.get("articles", [])]
    media = official_media_for_articles([article for article in articles if article])
    cluster["media"] = media
    return cluster


def enrich_file(path):
    if not os.path.exists(path):
        return 0, 0

    with open(path, "r", encoding="utf-8") as f:
        clusters = json.load(f)

    before = sum(1 for cluster in clusters if first_saved_image(cluster))
    with ThreadPoolExecutor(max_workers=8) as executor:
        clusters = list(executor.map(enrich_cluster, clusters))
    after = sum(1 for cluster in clusters if first_saved_image(cluster))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(clusters, f, indent=4)

    if path == "summarizedNews.json":
        save_clusters(clusters)

    return before, after


def main():
    for path in DATA_FILES:
        before, after = enrich_file(path)
        print(f"{path}: images {before} -> {after}")


if __name__ == "__main__":
    main()
