from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
import feedparser
from pydantic import BaseModel

from .config import Settings
from .db import db_session

logger = logging.getLogger("zuyu.brief")

AEST = timezone(timedelta(hours=10))

# ── Pydantic schema for structured AI output ──────────────────────────────────

class DailyBriefContent(BaseModel):
    executive_summary: str
    markets_macro: str
    tech_ai: str
    networking_telecom: str
    career_jobs: str
    opportunities: str
    risks: str
    internet_signal: str
    personal_impact: str
    noise_filter: str
    worth_reading: str
    actionable_today: str


# ── RSS sources ───────────────────────────────────────────────────────────────

RSS_SOURCES: list[tuple[str, str, list[str]]] = [
    ("Hacker News",        "https://news.ycombinator.com/rss",                     ["tech", "ai", "startup"]),
    ("TechCrunch",         "https://techcrunch.com/feed/",                          ["tech", "startup", "ai"]),
    ("Ars Technica",       "https://feeds.arstechnica.com/arstechnica/index",       ["tech", "science"]),
    ("The Verge",          "https://www.theverge.com/rss/index.xml",                ["tech", "consumer"]),
    ("The Register",       "https://www.theregister.com/headlines.atom",            ["tech", "enterprise", "infra"]),
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews",        ["markets", "macro"]),
    ("Reuters Technology", "https://feeds.reuters.com/reuters/technologyNews",      ["tech"]),
    ("Light Reading",      "https://www.lightreading.com/rss_simple.asp",           ["telecom", "ran", "5g"]),
    ("RCR Wireless",       "https://www.rcrwireless.com/feed",                      ["telecom", "5g", "ran"]),
    ("Fierce Network",     "https://www.fiercenetwork.com/rss.xml",                 ["telecom", "infra"]),
    ("MIT Tech Review",    "https://www.technologyreview.com/feed/",                ["ai", "tech"]),
    ("ZDNet",              "https://www.zdnet.com/rss.xml",                         ["tech", "enterprise"]),
]

# ── Market symbols ────────────────────────────────────────────────────────────

MARKET_SYMBOLS = [
    "^AXJO", "^GSPC", "^IXIC", "^DJI",
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA",
    "BTC-USD", "ETH-USD",
    "AUDUSD=X",
]

MARKET_NAMES: dict[str, str] = {
    "^AXJO":    "ASX 200",
    "^GSPC":    "S&P 500",
    "^IXIC":    "NASDAQ",
    "^DJI":     "Dow Jones",
    "AAPL":     "Apple",
    "MSFT":     "Microsoft",
    "NVDA":     "NVIDIA",
    "GOOGL":    "Google",
    "META":     "Meta",
    "AMZN":     "Amazon",
    "TSLA":     "Tesla",
    "BTC-USD":  "Bitcoin",
    "ETH-USD":  "Ethereum",
    "AUDUSD=X": "AUD/USD",
}


# ── RSS fetching ──────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_single_feed(source_name: str, url: str, tags: list[str], max_items: int = 7) -> list[dict[str, Any]]:
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "ZuyuIntel/1.0"})
        articles: list[dict[str, Any]] = []
        for entry in feed.entries[:max_items]:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            summary = _strip_html(summary)[:280]
            if title:
                articles.append({
                    "source": source_name,
                    "tags": tags,
                    "title": title,
                    "link": link,
                    "summary": summary,
                })
        logger.debug("RSS %s: %d articles", source_name, len(articles))
        return articles
    except Exception as exc:
        logger.debug("RSS fetch failed %s: %s", source_name, exc)
        return []


def _fetch_all_rss() -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    for source_name, url, tags in RSS_SOURCES:
        articles.extend(_fetch_single_feed(source_name, url, tags))
    logger.info("RSS total: %d articles from %d sources", len(articles), len(RSS_SOURCES))
    return articles


# ── Market data ───────────────────────────────────────────────────────────────

def _fetch_market_data() -> list[dict[str, Any]]:
    import urllib.request
    import json as _json

    symbols_str = ",".join(MARKET_SYMBOLS)
    fields = "symbol,shortName,regularMarketPrice,regularMarketChangePercent,regularMarketChange"
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_str}&fields={fields}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = _json.loads(resp.read())
        results = data.get("quoteResponse", {}).get("result", [])
        quotes: list[dict[str, Any]] = []
        for r in results:
            symbol = r.get("symbol", "")
            name = MARKET_NAMES.get(symbol, r.get("shortName", symbol))
            price = r.get("regularMarketPrice", 0) or 0
            change_pct = r.get("regularMarketChangePercent", 0) or 0
            change_abs = r.get("regularMarketChange", 0) or 0
            quotes.append({
                "symbol": symbol,
                "name": name,
                "price": round(float(price), 4),
                "change_pct": round(float(change_pct), 2),
                "change_abs": round(float(change_abs), 4),
            })
        logger.info("Market data: %d quotes", len(quotes))
        return quotes
    except Exception as exc:
        logger.warning("Market data fetch failed: %s", exc)
        return []


# ── Context assembly ──────────────────────────────────────────────────────────

def _build_context(articles: list[dict[str, Any]], market_data: list[dict[str, Any]], date: str) -> str:
    lines: list[str] = [f"=== MORNING INTELLIGENCE DATA — {date} ===\n"]

    if market_data:
        lines.append("--- MARKET DATA (overnight / latest) ---")
        for q in market_data:
            sign = "+" if q["change_pct"] >= 0 else ""
            lines.append(f"  {q['name']} ({q['symbol']}): {q['price']:,.2f}  {sign}{q['change_pct']}%")
        lines.append("")

    by_source: dict[str, list[dict[str, Any]]] = {}
    for a in articles:
        by_source.setdefault(a["source"], []).append(a)

    lines.append(f"--- NEWS ARTICLES ({len(articles)} total) ---")
    for source, source_articles in by_source.items():
        lines.append(f"\n[{source}]")
        for a in source_articles:
            lines.append(f"  • {a['title']}")
            if a.get("summary"):
                lines.append(f"    {a['summary']}")

    return "\n".join(lines)


# ── AI prompts ────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a personal intelligence analyst generating a morning briefing for Tommy.

Tommy's profile:
- 23-year-old network engineer in Melbourne, Australia
- Works at Ericsson/Telstra on RAN regression and automation (5G, LTE, Python automation, network testing)
- Core interests: AI/ML, cloud infrastructure, networking, telecom/5G/RAN, SRE and platform engineering, big tech (NVIDIA, Google, Microsoft, Apple, Meta, Amazon, Tesla), investing, self-improvement, running/gym performance
- Career goal: move into a senior cloud/infra/SRE/platform engineering role at a top-tier tech company, significantly higher pay
- Invests in Australian and US equities/ETFs, watches AUD/USD
- Building a Personal OS and home-lab ecosystem
- Interested in Sydney and Australian tech market opportunities

Your task: Generate a high-signal, low-noise morning intelligence briefing from the provided data.

Formatting rules:
- Each section is clean markdown: bullet points, **bold** for key names/companies/numbers/percentages
- 100-200 words per section, tight and sharp
- No corporate jargon, no generic self-help, no filler
- Be direct, intelligent, specific. Name companies, numbers, percentages wherever possible
- Distinguish signal from hype: if something is overhyped, say so explicitly

Priority lens:
- Telecom/networking/RAN is Tommy's core field — highest priority
- Genuinely important AI/ML developments — not another "AI chatbot launches"
- Career/hiring signals in cloud, SRE, platform, infra, networking
- Australian market and job market signals
- Practical investment-relevant moves (not generic finance commentary)
- Aggressively filter: no celebrity news, no politics without direct market/tech impact, no duplicate stories\
"""

_USER_PROMPT_TEMPLATE = """\
{context}

---
Generate the full morning briefing for {date}. Populate every field with high-signal, personalized intelligence grounded in the data above.

Field instructions:

executive_summary — Top 3-5 things that matter most TODAY. Each as: **Entity/Topic**: one sentence summary. Then "→ Why it matters for Tommy:" one line. Ranked by relevance to Tommy. No padding.

markets_macro — Top moves from the market data. What moved, by how much, why. Which moves Tommy should care about (tech exposure, AUD). What is noise. Include AUD/USD if data available.

tech_ai — Most important tech/AI/software/big tech developments. For each: what happened, who it impacts, signal vs hype verdict. Focus on genuine shifts, not press releases.

networking_telecom — High-signal telecom/5G/RAN/cloud networking/infra developments. Practical relevance for a network automation engineer. Vendor shifts, standards, automation trends, major outages/incidents.

career_jobs — Hiring and job market signals in cloud/SRE/platform/infra/networking. What skills appear in demand. Companies expanding or contracting. Australia/Sydney signals if present. What the market is rewarding right now.

opportunities — Explicit opportunities for Tommy. Concrete, grounded in today's data. Examples: a company expanding in his target area, a skill trend he could capitalize on, a new tool worth adding to his stack, a sector worth watching.

risks — Meaningful risks only: economic, tech market, job market, career trajectory, or investment risks visible in today's data. Concise, no speculative doom.

internet_signal — Top 3-5 genuine online discussions or themes gaining traction in tech/AI/engineering/infra circles. What smart people are paying attention to. Signal vs hype verdict for each.

personal_impact — News that could realistically affect Tommy's actual life: his work, his investment portfolio, his career move, tools and platforms he uses, Melbourne/Australia costs, anything with direct practical consequence.

noise_filter — 3-5 topics currently hyped but low-value for Tommy. Brief reason each is noise. Protect his attention from these today.

worth_reading — Top 1-3 genuinely high-value pieces from the data worth reading deeply. Title or description + source + one sentence on why it's worth his time. Only include if genuinely exceptional.

actionable_today — 3-5 concrete actions Tommy could take today. Must be grounded in today's data, not generic advice. Examples: look into a specific company, add a technology to his learning queue, track a market theme, turn a trend into a project idea.\
"""


# ── AI generation ─────────────────────────────────────────────────────────────

def _generate_brief_content(settings: Settings, date: str, context: str) -> DailyBriefContent:
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic API key is not configured — set ANTHROPIC_API_KEY to generate briefs")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    user_prompt = _USER_PROMPT_TEMPLATE.format(context=context, date=date)

    tool_schema = {
        "name": "generate_brief",
        "description": "Output the fully structured morning briefing",
        "input_schema": {
            "type": "object",
            "properties": {f: {"type": "string"} for f in DailyBriefContent.model_fields},
            "required": list(DailyBriefContent.model_fields.keys()),
        },
    }

    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=6000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[tool_schema],
        tool_choice={"type": "tool", "name": "generate_brief"},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "generate_brief":
            return DailyBriefContent(**block.input)

    raise RuntimeError("Anthropic returned no structured result for brief generation")


# ── Main sync entry point ─────────────────────────────────────────────────────

def generate_brief_sync(settings: Settings, date: str) -> tuple[DailyBriefContent, int]:
    """Fetch all sources and generate briefing. Designed to run in asyncio.to_thread."""
    logger.info("Brief generation started for %s", date)
    articles = _fetch_all_rss()
    market_data = _fetch_market_data()
    sources_count = len(articles) + len(market_data)
    logger.info("Brief sources: %d articles, %d market quotes", len(articles), len(market_data))
    context = _build_context(articles, market_data, date)
    content = _generate_brief_content(settings, date, context)
    logger.info("Brief generation complete for %s", date)
    return content, sources_count


# ── Async task runner ─────────────────────────────────────────────────────────

async def run_brief_generation(settings: Settings, db_path: str, brief_id: str, date: str) -> None:
    """Async task: run generation in thread pool, persist result to DB."""
    try:
        content, sources_count = await asyncio.to_thread(generate_brief_sync, settings, date)
        now = datetime.now(timezone.utc).isoformat()
        with db_session(db_path) as conn:
            conn.execute(
                "UPDATE briefings SET status='done', content=?, sources_fetched=?, generated_at=?, updated_at=? WHERE id=?",
                (content.model_dump_json(), sources_count, now, now, brief_id),
            )
        logger.info("Brief saved: date=%s id=%s sources=%d", date, brief_id, sources_count)
    except Exception as exc:
        logger.error("Brief generation failed for %s: %s", date, exc)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with db_session(db_path) as conn:
                conn.execute(
                    "UPDATE briefings SET status='failed', error=?, updated_at=? WHERE id=?",
                    (str(exc)[:1000], now, brief_id),
                )
        except Exception:
            pass
