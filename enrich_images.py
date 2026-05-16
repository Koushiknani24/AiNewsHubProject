import json
import os
from concurrent.futures import ThreadPoolExecutor

from database import replace_clusters
from refresh_pipeline import resolve_cluster_media, save_image_cache, write_news_data_script

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

    cluster["media"] = resolve_cluster_media(cluster, allow_network=True)
    return cluster


def enrich_file(path):
    if not os.path.exists(path):
        return 0, 0

    with open(path, "r", encoding="utf-8") as f:
        clusters = json.load(f)

    before = sum(1 for cluster in clusters if first_saved_image(cluster))
    with ThreadPoolExecutor(max_workers=8) as executor:
        clusters = list(executor.map(enrich_cluster, clusters))
    save_image_cache()
    after = sum(1 for cluster in clusters if first_saved_image(cluster))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(clusters, f, indent=4)

    if path == "summarizedNews.json":
        replace_clusters(clusters)
        write_news_data_script(clusters)

    return before, after


def main():
    for path in DATA_FILES:
        before, after = enrich_file(path)
        print(f"{path}: images {before} -> {after}")


if __name__ == "__main__":
    main()
