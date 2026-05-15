import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from database import save_clusters

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.summarizers.text_rank import TextRankSummarizer
    from sumy.nlp.stemmers import Stemmer
    from sumy.utils import get_stop_words
except ImportError:
    PlaintextParser = None
    Tokenizer = None
    TextRankSummarizer = None
    Stemmer = None
    get_stop_words = None

try:
    from transformers import pipeline
except ImportError:
    pipeline = None
from news_ai import analyze_cluster, clean_html

CLUSTERED_NEWS_FILE = 'clusteredNews.json'
SUMMARIZED_NEWS_FILE = 'summarizedNews.json'
LANGUAGE = "english"
SENTENCES_COUNT = 3 # Number of bullet points

print("Initializing Local Extractive Summarizer...")
transformer_summarizer = None
if pipeline and os.getenv("USE_TRANSFORMER_SUMMARY") == "1":
    try:
        transformer_summarizer = pipeline(
            "summarization",
            model=os.getenv("TRANSFORMER_SUMMARY_MODEL", "sshleifer/distilbart-cnn-12-6"),
            local_files_only=True,
        )
        print("Local transformer summarizer loaded.")
    except Exception as exc:
        print(f"Transformer summarizer unavailable locally: {exc}")

if TextRankSummarizer:
    stemmer = Stemmer(LANGUAGE)
    summarizer = TextRankSummarizer(stemmer)
    summarizer.stop_words = get_stop_words(LANGUAGE)
    print("TextRank model loaded.")
else:
    summarizer = None
    print("Sumy not installed. Using lightweight sentence-rank fallback.")

def extract_full_text(url):
    """Attempt to extract the full text from the article URL for better summarization."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read()
            if not BeautifulSoup:
                return ""
            soup = BeautifulSoup(html, 'html.parser')
            
            # Simple heuristic: get all paragraph tags
            paragraphs = soup.find_all('p')
            text = ' '.join([p.get_text() for p in paragraphs])
            
            # Clean up extra whitespace
            return ' '.join(text.split())
    except Exception as e:
        return ""

def generate_bullet_points(text):
    """
    Takes article text and uses the local NLP model to summarize it,
    then formats the output into bullet points.
    """
    if not text or len(text.split()) < 30:
        return ["Not enough text to summarize."]

    try:
        if transformer_summarizer:
            output = transformer_summarizer(text[:3500], max_length=150, min_length=45, do_sample=False)
            summary_text = output[0].get("summary_text", "") if output else ""
            return fallback_summary(summary_text) if summary_text else fallback_summary(text)

        if not summarizer:
            return fallback_summary(text)

        parser = PlaintextParser.from_string(text, Tokenizer(LANGUAGE))
        summary = summarizer(parser.document, SENTENCES_COUNT)
        
        bullets = []
        for sentence in summary:
            # sumy Sentence object needs to be cast to string
            sentence_str = str(sentence).strip()
            if len(sentence_str) > 10:
                bullets.append(sentence_str)
                
        if not bullets:
            return ["Summary unavailable for this text."]
            
        return bullets

    except Exception as e:
        print(f"Summarization error: {e}")
        return fallback_summary(text)


def fallback_summary(text):
    sentences = [sentence.strip() for sentence in text.replace("?", ".").replace("!", ".").split(".") if len(sentence.split()) > 6]
    scored = sorted(sentences, key=lambda sentence: len(set(sentence.lower().split())), reverse=True)
    return [sentence + "." for sentence in scored[:SENTENCES_COUNT]] or ["Summary unavailable for this text."]


def process_cluster(cluster):
    main_article = cluster['main_article']
    print(f"Summarizing: {main_article['title']}")
    
    # 1. First, try to get the full article text
    full_text = extract_full_text(main_article['link'])
    
    # 2. Fallback to RSS summary if full text extraction fails
    if not full_text or len(full_text.split()) < 50:
        if BeautifulSoup:
            soup = BeautifulSoup(main_article['summary_html'] or "", "html.parser")
            anchors = soup.find_all('a')
            if anchors:
                full_text = ". ".join([a.get_text(strip=True) for a in anchors]) + "."
            else:
                full_text = soup.get_text(separator=" ", strip=True)
        else:
            full_text = clean_html(main_article.get('summary_html', ''))
        
    # 3. Add title context
    full_text = f"{main_article['title']}. {full_text}"
        
    # 4. Use local model to create summary bullets
    bullets = generate_bullet_points(full_text)
    
    cluster['ai_summary_bullets'] = bullets
    source_texts = [
        f"{article.get('title', '')}. {clean_html(article.get('summary_html', ''))}"
        for article in cluster.get('articles', [])
    ]
    neutral_text = " ".join(source_texts) or full_text
    neutral_bullets = generate_bullet_points(neutral_text)
    cluster['neutral_summary'] = " ".join(neutral_bullets[:2]) if neutral_bullets else "Neutral summary unavailable."
    return analyze_cluster(cluster)

def main():
    if not os.path.exists(CLUSTERED_NEWS_FILE):
        print(f"Error: {CLUSTERED_NEWS_FILE} not found. Run cluster.py first.")
        return
        
    with open(CLUSTERED_NEWS_FILE, 'r', encoding='utf-8') as f:
        clusters = json.load(f)
        
    print(f"Loaded {len(clusters)} clustered stories for AI summarization.")
    
    # We can use more workers for Sumy since it's just pure python/numpy and fast
    with ThreadPoolExecutor(max_workers=5) as executor:
        summarized_clusters = list(executor.map(process_cluster, clusters))
        
    with open(SUMMARIZED_NEWS_FILE, 'w', encoding='utf-8') as f:
        json.dump(summarized_clusters, f, indent=4)

    save_clusters(summarized_clusters)
        
    print(f"\nDone! AI Summarized news saved to {SUMMARIZED_NEWS_FILE} and news_hub.db")

if __name__ == "__main__":
    main()
