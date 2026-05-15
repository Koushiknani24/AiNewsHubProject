import importlib.util
import os


AI_CONCEPTS = [
    {
        "feature": "TF-IDF Clustering",
        "concept": "NLP + Vectorization",
        "implementation": "TfidfVectorizer groups articles by title and summary text.",
        "status": "active" if importlib.util.find_spec("sklearn") else "fallback",
    },
    {
        "feature": "Cosine Similarity",
        "concept": "Similarity Analysis",
        "implementation": "Cosine similarity compares vectorized articles inside the clustering pipeline.",
        "status": "active" if importlib.util.find_spec("sklearn") else "fallback",
    },
    {
        "feature": "BERT Embeddings",
        "concept": "Deep NLP",
        "implementation": "Optional local BERT embedding mode can be enabled with USE_BERT_EMBEDDINGS=1.",
        "status": "available" if importlib.util.find_spec("transformers") and os.getenv("USE_BERT_EMBEDDINGS") == "1" else "optional",
    },
    {
        "feature": "Transformer Summarization",
        "concept": "BART / T5",
        "implementation": "Optional local transformer summarizer is used when a local model is available.",
        "status": "available" if importlib.util.find_spec("transformers") and os.getenv("USE_TRANSFORMER_SUMMARY") == "1" else "optional",
    },
    {
        "feature": "Sentiment Detection",
        "concept": "NLP Classification",
        "implementation": "Weighted news-domain sentiment classifier labels positive, negative, and neutral stories.",
        "status": "active",
    },
    {
        "feature": "Fake News Prediction",
        "concept": "ML / DL Models",
        "implementation": "Hybrid scoring combines source reliability, agreement, sensational language, and source count.",
        "status": "active",
    },
    {
        "feature": "Explainable AI",
        "concept": "AI Interpretation",
        "implementation": "Each fake-news score includes readable signals explaining the prediction.",
        "status": "active",
    },
]


def get_ai_concepts():
    return AI_CONCEPTS
