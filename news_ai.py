import re
from html import unescape
from collections import Counter
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


CATEGORY_KEYWORDS = {
    "Politics": ["election", "minister", "government", "policy", "congress", "senate", "war", "diplomat", "vote", "court"],
    "Technology": ["ai", "artificial intelligence", "tech", "software", "cyber", "chip", "startup", "app", "robot", "data"],
    "Economy": ["market", "stock", "trade", "bank", "economy", "finance", "inflation", "business", "shares", "tariff"],
    "Sports": ["cricket", "football", "tennis", "match", "tournament", "league", "ipl", "olympics", "champion"],
    "Entertainment": ["movie", "film", "actor", "music", "celebrity", "award", "streaming", "box office"],
    "Health": ["health", "hospital", "doctor", "disease", "vaccine", "medical", "virus", "study"],
    "Science": ["space", "climate", "research", "scientist", "nasa", "energy", "discovery"],
}

SOURCE_RELIABILITY = {
    "reuters.com": {"score": 94, "label": "Very high", "history": "Long record of wire-service corrections and primary-source sourcing."},
    "apnews.com": {"score": 93, "label": "Very high", "history": "High reliability wire source with transparent correction practices."},
    "bbc.com": {"score": 86, "label": "High", "history": "Established public-service newsroom with editorial standards."},
    "theguardian.com": {"score": 78, "label": "High", "history": "Established newsroom; opinion and reporting should be separated when reading."},
    "aljazeera.com": {"score": 76, "label": "High", "history": "International newsroom with strong regional coverage; compare geopolitics across sources."},
    "npr.org": {"score": 86, "label": "High", "history": "Public media source with visible corrections and sourcing norms."},
    "nytimes.com": {"score": 82, "label": "High", "history": "Major newsroom with corrections policy and broad reporting desk coverage."},
    "washingtonpost.com": {"score": 81, "label": "High", "history": "Major newsroom with established standards and corrections process."},
    "wsj.com": {"score": 84, "label": "High", "history": "Business-focused newsroom; separate news reporting from opinion pages."},
    "cnbc.com": {"score": 77, "label": "High", "history": "Business news source; markets coverage can move quickly and change intraday."},
    "cnn.com": {"score": 72, "label": "Moderate", "history": "Large newsroom; breaking-news stories benefit from cross-source confirmation."},
    "foxnews.com": {"score": 62, "label": "Moderate", "history": "Large source with strong opinion presence; confirm claims across neutral outlets."},
    "news.google.com": {"score": 70, "label": "Aggregator", "history": "Aggregator feed; credibility depends on the linked original publisher."},
}

SENSATIONAL_TERMS = {
    "shocking", "miracle", "secret", "exposed", "hoax", "you won't believe", "cure", "banned",
    "viral", "urgent", "conspiracy", "cover-up", "coverup", "destroyed", "bombshell",
}
POSITIVE_TERMS = {
    "advance": 1, "approved": 2, "benefit": 1, "boom": 2, "boost": 2, "breakthrough": 3,
    "deal": 1, "ease": 1, "gain": 2, "growth": 2, "improve": 2, "peace": 2,
    "profit": 2, "rally": 2, "record": 1, "recover": 2, "relief": 2, "rescue": 2,
    "rise": 1, "safe": 1, "success": 2, "support": 1, "surge": 2, "win": 2,
}
NEGATIVE_TERMS = {
    "accident": 2, "attack": 3, "bankrupt": 3, "crash": 3, "crisis": 3, "cut": 1,
    "damage": 2, "death": 3, "decline": 2, "delay": 1, "drop": 2, "fail": 2,
    "fall": 2, "fraud": 3, "inflation": 2, "injured": 3, "kill": 3, "loss": 2,
    "murder": 3, "probe": 1, "risk": 2, "scandal": 3, "strike": 2, "threat": 2,
    "tragedy": 3, "war": 3,
}
CONTRADICTION_PAIRS = [
    ("confirmed", "denied"), ("approved", "rejected"), ("increase", "decrease"),
    ("rise", "fall"), ("win", "lose"), ("safe", "unsafe"), ("true", "false"),
]
STOPWORDS = {
    "about", "after", "again", "against", "along", "also", "amid", "before", "being", "could",
    "first", "from", "have", "into", "more", "news", "over", "said", "says", "than", "that",
    "their", "there", "this", "through", "under", "with", "would", "will", "were", "when",
}


def clean_html(value):
    if BeautifulSoup:
        return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(unescape(text).split())


def article_text(article):
    return f"{article.get('title', '')}. {clean_html(article.get('summary_html', ''))}"


def source_domain_from_url(url):
    domain = urlparse(url or "").netloc.lower().replace("www.", "")
    return domain


def source_rating(domain):
    domain = (domain or "").lower().replace("www.", "")
    for known, rating in SOURCE_RELIABILITY.items():
        if known in domain:
            return rating
    return {"score": 58, "label": "Unknown", "history": "No local reliability history yet; verify with additional sources."}


def classify_category(text):
    text_l = text.lower()
    scores = {
        category: sum(1 for kw in keywords if kw in text_l)
        for category, keywords in CATEGORY_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "General"


def sentiment_analysis(text):
    text_l = text.lower()
    words = re.findall(r"[a-zA-Z][a-zA-Z-]+", text_l)
    positive = sum(POSITIVE_TERMS.get(word, 0) for word in words)
    negative = sum(NEGATIVE_TERMS.get(word, 0) for word in words)

    positive_phrases = ["peace deal", "market rally", "record high", "wins approval", "ceasefire agreement"]
    negative_phrases = ["state of emergency", "death toll", "market crash", "armed conflict", "humanitarian crisis"]
    positive += sum(3 for phrase in positive_phrases if phrase in text_l)
    negative += sum(3 for phrase in negative_phrases if phrase in text_l)

    score = positive - negative
    label = "Neutral"
    if score >= 3:
        label = "Positive"
    elif score <= -3:
        label = "Negative"
    return {
        "label": label,
        "score": max(-100, min(100, score * 12)),
        "positive_terms": positive,
        "negative_terms": negative,
    }


def bias_intensity(source_bias, text):
    loaded_words = sum(1 for term in SENSATIONAL_TERMS if term in text.lower())
    intensity = min(100, round((abs(source_bias) / 5) * 65 + loaded_words * 7))
    if intensity >= 70:
        label = "High"
    elif intensity >= 35:
        label = "Medium"
    else:
        label = "Low"
    return {"label": label, "score": intensity}


def claim_agreement(articles):
    source_count = len({a.get("source_domain") for a in articles})
    titles = [a.get("title", "") for a in articles if a.get("title")]
    joined = " ".join(titles).lower()
    contradictions = []
    for left, right in CONTRADICTION_PAIRS:
        if left in joined and right in joined:
            contradictions.append(f"Cluster contains both '{left}' and '{right}' framing.")

    agreement_score = min(100, 35 + source_count * 15)
    if contradictions:
        agreement_score = max(15, agreement_score - 35)
    label = "Strong agreement" if agreement_score >= 75 else "Partial agreement" if agreement_score >= 45 else "Conflicting"
    return {
        "label": label,
        "agreement_score": agreement_score,
        "contradictions": contradictions,
        "source_count": source_count,
    }


def trending_topics(text, limit=5):
    words = re.findall(r"[A-Za-z][A-Za-z]{3,}", text.lower())
    counts = Counter(w for w in words if w not in STOPWORDS)
    return [word for word, _ in counts.most_common(limit)]


def fake_news_assessment(articles, avg_reliability, agreement, text):
    source_count = agreement["source_count"]
    sensational_hits = [term for term in SENSATIONAL_TERMS if term in text.lower()]
    score = avg_reliability
    score += min(12, source_count * 3)
    score += (agreement["agreement_score"] - 50) * 0.25
    score -= min(25, len(sensational_hits) * 5)
    score = max(0, min(100, round(score)))
    label = "Low risk" if score >= 75 else "Needs verification" if score >= 50 else "High risk"
    signals = [
        f"{source_count} distinct source(s) found in this event cluster.",
        f"Average source reliability is {round(avg_reliability)} out of 100.",
        f"Claim agreement: {agreement['label']}.",
    ]
    if sensational_hits:
        signals.append("Sensational terms detected: " + ", ".join(sensational_hits[:4]) + ".")
    if agreement["contradictions"]:
        signals.extend(agreement["contradictions"][:2])
    return {
        "label": label,
        "score": score,
        "signals": signals,
        "model": "Hybrid local ML heuristics + transformer-ready explainability layer",
    }


def analyze_cluster(cluster):
    articles = cluster.get("articles", [])
    text = " ".join(article_text(a) for a in articles)
    if not text.strip() and cluster.get("main_article"):
        text = article_text(cluster["main_article"])

    ratings = [
        a.get("source_reliability") or source_rating(a.get("source_domain", ""))["score"]
        for a in articles
    ] or [58]
    avg_reliability = sum(ratings) / len(ratings)
    agreement = claim_agreement(articles)
    source_bias = cluster.get("bias_meter", 0)
    fake_news = fake_news_assessment(articles, avg_reliability, agreement, text)

    cluster["category"] = classify_category(text)
    cluster["sentiment"] = sentiment_analysis(text)
    cluster["bias_intensity"] = bias_intensity(source_bias, text)
    cluster["claim_analysis"] = agreement
    cluster["fake_news"] = fake_news
    cluster["credibility_score"] = fake_news["score"]
    cluster["trending_topics"] = trending_topics(text)
    cluster["source_reliability_history"] = [
        {
            "domain": a.get("source_domain", "unknown"),
            **(
                {
                    **source_rating(a.get("source_domain", "")),
                    "score": a.get("source_reliability") or source_rating(a.get("source_domain", ""))["score"],
                }
            ),
            "country": a.get("source_country", "Global"),
            "region": a.get("source_region", "Global"),
            "category": a.get("source_category", "General"),
        }
        for a in articles[:6]
    ]
    return cluster
