import json
import os
from dataclasses import dataclass, asdict
from typing import Iterable


NEWS_FEEDS_FILE = "newsFeeds.json"


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str
    country: str = "Global"
    region: str = "Global"
    category: str = "General"
    reliability: int = 70
    update_frequency: str = "standard"


def google_topic(name, query, country="Global", region="Global", category="General", reliability=70):
    return FeedSource(
        name=name,
        url=f"https://news.google.com/rss/search?q={query.replace(' ', '%20')}&hl=en-US&gl=US&ceid=US:en",
        country=country,
        region=region,
        category=category,
        reliability=reliability,
        update_frequency="live",
    )


CORE_SOURCES = [
    FeedSource("Google News Top Stories", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "Breaking", 70, "live"),
    FeedSource("Google News World", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "World", 70, "live"),
    FeedSource("Google News Business", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "Business", 70, "live"),
    FeedSource("Google News Technology", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "Technology", 70, "live"),
    FeedSource("Google News Science", "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "Science", 70, "live"),
    FeedSource("Google News Sports", "https://news.google.com/rss/headlines/section/topic/SPORTS?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "Sports", 70, "live"),
    FeedSource("Google News Entertainment", "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en", "Global", "Global", "Entertainment", 70, "live"),
    FeedSource("Reuters World", "https://feeds.reuters.com/reuters/worldNews", "Global", "Global", "World", 94, "fast"),
    FeedSource("Reuters Business", "https://feeds.reuters.com/reuters/businessNews", "Global", "Global", "Business", 94, "fast"),
    FeedSource("Reuters Technology", "https://feeds.reuters.com/reuters/technologyNews", "Global", "Global", "Technology", 94, "fast"),
    FeedSource("AP Top News", "https://apnews.com/hub/ap-top-news?output=rss", "USA", "North America", "Breaking", 93, "fast"),
    FeedSource("AP World", "https://apnews.com/hub/world-news?output=rss", "Global", "Global", "World", 93, "fast"),
    FeedSource("BBC News", "https://feeds.bbci.co.uk/news/rss.xml", "UK", "Europe", "Breaking", 86, "fast"),
    FeedSource("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "UK", "Europe", "World", 86, "fast"),
    FeedSource("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "UK", "Europe", "Technology", 86, "standard"),
    FeedSource("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "UK", "Europe", "Business", 86, "standard"),
    FeedSource("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "UK", "Europe", "Science", 86, "standard"),
    FeedSource("CNN Edition", "http://rss.cnn.com/rss/edition.rss", "USA", "North America", "Breaking", 72, "fast"),
    FeedSource("CNN World", "http://rss.cnn.com/rss/edition_world.rss", "USA", "North America", "World", 72, "fast"),
    FeedSource("CNN Business", "http://rss.cnn.com/rss/money_latest.rss", "USA", "North America", "Business", 72, "standard"),
    FeedSource("Fox World", "https://moxie.foxnews.com/google-publisher/world.xml", "USA", "North America", "World", 62, "standard"),
    FeedSource("NPR News", "https://feeds.npr.org/1001/rss.xml", "USA", "North America", "General", 86, "fast"),
    FeedSource("NPR World", "https://feeds.npr.org/1004/rss.xml", "USA", "North America", "World", 86, "standard"),
    FeedSource("NBC News", "https://feeds.nbcnews.com/nbcnews/public/news", "USA", "North America", "Breaking", 78, "fast"),
    FeedSource("NBC World", "https://feeds.nbcnews.com/nbcnews/public/world", "USA", "North America", "World", 78, "standard"),
    FeedSource("CBS World", "https://www.cbsnews.com/latest/rss/world", "USA", "North America", "World", 76, "standard"),
    FeedSource("ABC International", "https://abcnews.go.com/abcnews/internationalheadlines", "USA", "North America", "World", 76, "standard"),
    FeedSource("New York Times Home", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "USA", "North America", "General", 82, "standard"),
    FeedSource("New York Times World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "USA", "North America", "World", 82, "standard"),
    FeedSource("New York Times Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "USA", "North America", "Business", 82, "standard"),
    FeedSource("Washington Post World", "http://feeds.washingtonpost.com/rss/world", "USA", "North America", "World", 81, "standard"),
    FeedSource("Wall Street Journal World", "https://feeds.a.dj.com/rss/RSSWorldNews.xml", "USA", "North America", "World", 84, "standard"),
    FeedSource("Wall Street Journal Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "USA", "North America", "Business", 84, "standard"),
    FeedSource("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss", "USA", "North America", "Business", 86, "standard"),
    FeedSource("Bloomberg Technology", "https://feeds.bloomberg.com/technology/news.rss", "USA", "North America", "Technology", 86, "standard"),
    FeedSource("CNBC Top News", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "USA", "North America", "Business", 77, "fast"),
    FeedSource("CNBC World", "https://www.cnbc.com/id/100727362/device/rss/rss.html", "USA", "North America", "World", 77, "standard"),
    FeedSource("MarketWatch Top Stories", "https://feeds.marketwatch.com/marketwatch/topstories/", "USA", "North America", "Business", 75, "standard"),
    FeedSource("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "USA", "North America", "Business", 70, "standard"),
    FeedSource("Forbes", "https://www.forbes.com/real-time/feed2/", "USA", "North America", "Business", 70, "standard"),
    FeedSource("The Guardian World", "https://www.theguardian.com/world/rss", "UK", "Europe", "World", 78, "fast"),
    FeedSource("The Guardian UK", "https://www.theguardian.com/uk/rss", "UK", "Europe", "General", 78, "standard"),
    FeedSource("The Guardian Business", "https://www.theguardian.com/business/rss", "UK", "Europe", "Business", 78, "standard"),
    FeedSource("Sky News World", "https://feeds.skynews.com/feeds/rss/world.xml", "UK", "Europe", "World", 76, "fast"),
    FeedSource("Sky News UK", "https://feeds.skynews.com/feeds/rss/uk.xml", "UK", "Europe", "General", 76, "standard"),
    FeedSource("Financial Times", "https://www.ft.com/?format=rss", "UK", "Europe", "Business", 84, "standard"),
    FeedSource("The Independent World", "https://www.independent.co.uk/news/world/rss", "UK", "Europe", "World", 70, "standard"),
    FeedSource("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "Qatar", "Middle East", "World", 76, "fast"),
    FeedSource("Arab News", "https://www.arabnews.com/rss.xml", "Saudi Arabia", "Middle East", "General", 68, "standard"),
    FeedSource("Times of Israel", "https://www.timesofisrael.com/feed/", "Israel", "Middle East", "General", 70, "standard"),
    FeedSource("DW World", "https://rss.dw.com/rdf/rss-en-world", "Germany", "Europe", "World", 80, "fast"),
    FeedSource("DW Business", "https://rss.dw.com/rdf/rss-en-bus", "Germany", "Europe", "Business", 80, "standard"),
    FeedSource("France24", "https://www.france24.com/en/rss", "France", "Europe", "World", 79, "fast"),
    FeedSource("Euronews", "https://www.euronews.com/rss?level=theme&name=news", "Europe", "Europe", "World", 74, "fast"),
    FeedSource("Euronews Business", "https://www.euronews.com/rss?level=theme&name=business", "Europe", "Europe", "Business", 74, "standard"),
    FeedSource("Politico Europe", "https://www.politico.eu/feed/", "Europe", "Europe", "Politics", 76, "standard"),
    FeedSource("The Local Europe", "https://www.thelocal.com/feeds/rss.php", "Europe", "Europe", "General", 65, "standard"),
    FeedSource("The Hindu National", "https://www.thehindu.com/news/national/feeder/default.rss", "India", "Asia", "General", 78, "fast"),
    FeedSource("The Hindu International", "https://www.thehindu.com/news/international/feeder/default.rss", "India", "Asia", "World", 78, "standard"),
    FeedSource("The Hindu Business", "https://www.thehindu.com/business/feeder/default.rss", "India", "Asia", "Business", 78, "standard"),
    FeedSource("The Hindu Sport", "https://www.thehindu.com/sport/feeder/default.rss", "India", "Asia", "Sports", 78, "standard"),
    FeedSource("Times of India Top", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "India", "Asia", "Breaking", 70, "fast"),
    FeedSource("Times of India India", "https://timesofindia.indiatimes.com/rssfeeds/4719148.cms", "India", "Asia", "General", 70, "standard"),
    FeedSource("Times of India World", "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms", "India", "Asia", "World", 70, "standard"),
    FeedSource("Indian Express India", "https://indianexpress.com/section/india/feed/", "India", "Asia", "General", 72, "standard"),
    FeedSource("Indian Express World", "https://indianexpress.com/section/world/feed/", "India", "Asia", "World", 72, "standard"),
    FeedSource("Indian Express Business", "https://indianexpress.com/section/business/feed/", "India", "Asia", "Business", 72, "standard"),
    FeedSource("NDTV Top Stories", "https://feeds.feedburner.com/ndtvnews-top-stories", "India", "Asia", "Breaking", 70, "standard"),
    FeedSource("NDTV India", "https://feeds.feedburner.com/ndtvnews-india-news", "India", "Asia", "General", 70, "standard"),
    FeedSource("NDTV World", "https://feeds.feedburner.com/ndtvnews-world-news", "India", "Asia", "World", 70, "standard"),
    FeedSource("Hindustan Times India", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml", "India", "Asia", "General", 70, "standard"),
    FeedSource("Hindustan Times World", "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml", "India", "Asia", "World", 70, "standard"),
    FeedSource("Economic Times", "https://economictimes.indiatimes.com/rssfeedstopstories.cms", "India", "Asia", "Business", 72, "standard"),
    FeedSource("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "India", "Asia", "Business", 72, "standard"),
    FeedSource("Deccan Herald National", "https://www.deccanherald.com/rss/national", "India", "Asia", "General", 68, "standard"),
    FeedSource("Japan Times", "https://www.japantimes.co.jp/feed/", "Japan", "Asia", "General", 72, "standard"),
    FeedSource("South China Morning Post", "https://www.scmp.com/rss/91/feed", "Hong Kong", "Asia", "World", 72, "standard"),
    FeedSource("Channel News Asia", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "Singapore", "Asia", "General", 74, "fast"),
    FeedSource("Korea Herald", "https://www.koreaherald.com/rss_xml.php", "South Korea", "Asia", "General", 68, "standard"),
    FeedSource("Bangkok Post", "https://www.bangkokpost.com/rss/data/topstories.xml", "Thailand", "Asia", "General", 65, "standard"),
    FeedSource("The Straits Times", "https://www.straitstimes.com/news/world/rss.xml", "Singapore", "Asia", "World", 70, "standard"),
    FeedSource("ABC Australia", "https://www.abc.net.au/news/feed/51120/rss.xml", "Australia", "Oceania", "General", 76, "fast"),
    FeedSource("Sydney Morning Herald", "https://www.smh.com.au/rss/feed.xml", "Australia", "Oceania", "General", 70, "standard"),
    FeedSource("News24 South Africa", "https://www.news24.com/news24/rss", "South Africa", "Africa", "General", 68, "standard"),
    FeedSource("AllAfrica", "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "Africa", "Africa", "General", 68, "standard"),
    FeedSource("Africa News", "https://www.africanews.com/feed/rss", "Africa", "Africa", "General", 68, "standard"),
    FeedSource("CBC Canada", "https://www.cbc.ca/cmlink/rss-topstories", "Canada", "North America", "General", 76, "fast"),
    FeedSource("CBC World", "https://www.cbc.ca/cmlink/rss-world", "Canada", "North America", "World", 76, "standard"),
    FeedSource("Global News Canada", "https://globalnews.ca/feed/", "Canada", "North America", "General", 70, "standard"),
    FeedSource("Mexico News Daily", "https://mexiconewsdaily.com/feed/", "Mexico", "North America", "General", 64, "standard"),
    FeedSource("Buenos Aires Times", "https://www.batimes.com.ar/feed", "Argentina", "South America", "General", 65, "standard"),
    FeedSource("MercoPress", "https://en.mercopress.com/rss", "South America", "South America", "General", 65, "standard"),
    FeedSource("TechCrunch", "https://techcrunch.com/feed/", "USA", "Global", "Technology", 72, "fast"),
    FeedSource("The Verge", "https://www.theverge.com/rss/index.xml", "USA", "Global", "Technology", 72, "fast"),
    FeedSource("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "USA", "Global", "Technology", 78, "standard"),
    FeedSource("Wired", "https://www.wired.com/feed/rss", "USA", "Global", "Technology", 76, "standard"),
    FeedSource("MIT Technology Review", "https://www.technologyreview.com/feed/", "USA", "Global", "Technology", 78, "standard"),
    FeedSource("Engadget", "https://www.engadget.com/rss.xml", "USA", "Global", "Technology", 68, "standard"),
    FeedSource("Nature News", "https://www.nature.com/nature.rss", "Global", "Global", "Science", 88, "standard"),
    FeedSource("ScienceDaily", "https://www.sciencedaily.com/rss/top.xml", "Global", "Global", "Science", 74, "standard"),
    FeedSource("NASA Breaking News", "https://www.nasa.gov/rss/dyn/breaking_news.rss", "USA", "Global", "Science", 86, "standard"),
    FeedSource("ESPN", "https://www.espn.com/espn/rss/news", "USA", "Global", "Sports", 70, "standard"),
    FeedSource("ESPN Cricinfo", "https://www.espncricinfo.com/rss/content/story/feeds/0.xml", "Global", "Global", "Sports", 70, "fast"),
    FeedSource("Sky Sports", "https://www.skysports.com/rss/12040", "UK", "Global", "Sports", 70, "fast"),
    FeedSource("NDTV Sports", "https://sports.ndtv.com/rss/sports", "India", "Global", "Sports", 68, "standard"),
    FeedSource("Variety", "https://variety.com/feed/", "USA", "Global", "Entertainment", 68, "standard"),
    FeedSource("Hollywood Reporter", "https://www.hollywoodreporter.com/feed/", "USA", "Global", "Entertainment", 68, "standard"),
    FeedSource("Rolling Stone", "https://www.rollingstone.com/feed/", "USA", "Global", "Entertainment", 66, "standard"),
    FeedSource("Time", "https://time.com/feed/", "USA", "Global", "General", 72, "standard"),
    FeedSource("Yahoo News World", "https://news.yahoo.com/rss/world", "USA", "Global", "World", 65, "standard"),
]


COUNTRY_QUERIES = {
    "USA": "United States breaking news",
    "UK": "United Kingdom breaking news",
    "India": "India breaking news",
    "Canada": "Canada breaking news",
    "Australia": "Australia breaking news",
    "France": "France breaking news",
    "Germany": "Germany breaking news",
    "Italy": "Italy breaking news",
    "Spain": "Spain breaking news",
    "Russia": "Russia breaking news",
    "Ukraine": "Ukraine war latest",
    "China": "China breaking news",
    "Japan": "Japan breaking news",
    "South Korea": "South Korea breaking news",
    "Singapore": "Singapore breaking news",
    "Pakistan": "Pakistan breaking news",
    "Bangladesh": "Bangladesh breaking news",
    "Sri Lanka": "Sri Lanka breaking news",
    "Israel": "Israel breaking news",
    "Palestine": "Palestine Gaza breaking news",
    "Iran": "Iran breaking news",
    "Saudi Arabia": "Saudi Arabia breaking news",
    "UAE": "UAE breaking news",
    "Turkey": "Turkey breaking news",
    "Egypt": "Egypt breaking news",
    "South Africa": "South Africa breaking news",
    "Nigeria": "Nigeria breaking news",
    "Kenya": "Kenya breaking news",
    "Brazil": "Brazil breaking news",
    "Argentina": "Argentina breaking news",
    "Mexico": "Mexico breaking news",
}


CATEGORY_QUERIES = {
    "Global Politics": "international politics latest",
    "Diplomacy": "global diplomacy latest",
    "War Conflict": "war conflict breaking news",
    "Elections": "election latest world",
    "Technology": "technology AI cybersecurity latest",
    "Artificial Intelligence": "artificial intelligence latest news",
    "Startups": "startup funding latest news",
    "Markets": "global stock markets latest",
    "Economy": "global economy inflation latest",
    "Business": "global business latest",
    "Energy": "oil gas energy latest",
    "Climate": "climate change latest news",
    "Science": "science discovery latest",
    "Health": "health medicine latest news",
    "Sports": "global sports latest",
    "Cricket": "cricket latest news",
    "Football": "football soccer latest news",
    "Entertainment": "entertainment film music latest",
    "Space": "space NASA latest news",
    "Crypto": "crypto bitcoin regulation latest",
    "Cybersecurity": "cybersecurity breach latest news",
    "Trade": "global trade tariffs latest",
}


GENERATED_GOOGLE_SOURCES = [
    google_topic(f"Google {country}", query, country=country, region="Global", category="Country")
    for country, query in COUNTRY_QUERIES.items()
] + [
    google_topic(f"Google {category}", query, category=category)
    for category, query in CATEGORY_QUERIES.items()
]


DEFAULT_SOURCES = CORE_SOURCES + GENERATED_GOOGLE_SOURCES


def load_custom_sources() -> list[FeedSource]:
    if not os.path.exists(NEWS_FEEDS_FILE):
        return []
    try:
        with open(NEWS_FEEDS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []

    sources = []
    for item in raw:
        if isinstance(item, str):
            sources.append(FeedSource(name=item, url=item, reliability=60))
        elif isinstance(item, dict) and item.get("url"):
            sources.append(FeedSource(
                name=item.get("name") or item["url"],
                url=item["url"],
                country=item.get("country", "Global"),
                region=item.get("region", "Global"),
                category=item.get("category", "General"),
                reliability=int(item.get("reliability", 60)),
                update_frequency=item.get("update_frequency", "custom"),
            ))
    return sources


def dedupe_sources(sources: Iterable[FeedSource]) -> list[FeedSource]:
    seen = set()
    deduped = []
    for source in sources:
        url = source.url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(source)
    return deduped


def get_all_sources(include_custom=True) -> list[FeedSource]:
    sources = list(DEFAULT_SOURCES)
    if include_custom:
        sources.extend(load_custom_sources())
    return dedupe_sources(sources)


def get_source_urls(include_custom=True) -> list[str]:
    return [source.url for source in get_all_sources(include_custom)]


def source_count(include_custom=True) -> int:
    return len(get_all_sources(include_custom))


def source_metadata(include_custom=True) -> list[dict]:
    return [asdict(source) for source in get_all_sources(include_custom)]


def source_stats(include_custom=True) -> dict:
    sources = get_all_sources(include_custom)
    by_region = {}
    by_country = {}
    by_category = {}
    for source in sources:
        by_region[source.region] = by_region.get(source.region, 0) + 1
        by_country[source.country] = by_country.get(source.country, 0) + 1
        by_category[source.category] = by_category.get(source.category, 0) + 1
    return {
        "total": len(sources),
        "by_region": dict(sorted(by_region.items())),
        "by_country": dict(sorted(by_country.items())),
        "by_category": dict(sorted(by_category.items())),
    }
