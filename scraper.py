"""
Reddit Pain Point Scraper
Scrapes a subreddit daily and uses Gemini Flash 2.5 to extract
quotes where people express pain points, frustrations, wishes, or goals.
"""

import os
import json
import time
import hashlib
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

import requests
import google.generativeai as genai

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
GEMINI_MODEL   = "gemini-2.5-flash-preview-05-20"
POSTS_LIMIT    = 100        # Reddit posts to pull per run
COMMENT_DEPTH  = 2          # Comment tree levels to include
BATCH_SIZE     = 15         # Posts per Gemini call  (tune for cost vs speed)
MIN_QUOTE_LEN  = 40         # Characters — drop fragments shorter than this
REQUEST_DELAY  = 1.5        # Seconds between Reddit requests (be polite)
OUTPUT_DIR     = Path("output")

REDDIT_HEADERS = {
    "User-Agent": "RedditPainPointScraper/1.0 (research tool; contact via script config)"
}

# ── Gemini Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a qualitative research assistant that extracts authentic voice-of-customer quotes from Reddit.

Your job: read the posts and comments below and return ONLY quotes where someone expresses:
- A specific frustration or pain point they are experiencing
- A struggle they are going through
- A wish or desire for something that doesn't exist or isn't working
- A goal they are trying to achieve but finding hard
- A problem they want solved

KEEP quotes that are:
✓ Specific and concrete ("I spend 3 hours every week manually copying data between...")
✓ Emotionally genuine ("I'm so tired of having to...")
✓ Action-oriented desires ("I just want a tool that...")
✓ Real struggles with named obstacles

SKIP quotes that are:
✗ Generic or vague ("this is hard", "I wish it was easier", "it's frustrating")
✗ Jokes, sarcasm, or clearly ironic
✗ Meta-discussion about Reddit or the subreddit itself
✗ Questions without expressed pain ("does anyone know how to...")
✗ Pure opinions or recommendations with no pain expressed
✗ Very short fragments under 40 characters
✗ Off-topic to the subreddit's domain

Return a JSON array. Each item must have exactly these fields:
{
  "quote": "the exact words from the post or comment, verbatim",
  "source_type": "post_title" | "post_body" | "comment",
  "pain_category": "frustration" | "struggle" | "wish" | "goal",
  "post_url": "the reddit post URL",
  "post_title": "title of the post this came from",
  "post_score": <integer upvotes>
}

If there are no qualifying quotes in the batch, return an empty array: []
Return ONLY the JSON array — no preamble, no explanation, no markdown fences."""


# ── Reddit Fetching ───────────────────────────────────────────────────────────
def fetch_posts(subreddit: str, limit: int = POSTS_LIMIT) -> list[dict]:
    """Pull recent posts from a subreddit via the public JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    params = {"limit": min(limit, 100)}
    posts = []
    after = None

    while len(posts) < limit:
        if after:
            params["after"] = after
        try:
            resp = requests.get(url, headers=REDDIT_HEADERS, params=params, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("Reddit request failed: %s", e)
            break

        data = resp.json().get("data", {})
        children = data.get("children", [])
        if not children:
            break

        for child in children:
            post = child.get("data", {})
            posts.append({
                "id":        post.get("id"),
                "title":     post.get("title", ""),
                "selftext":  post.get("selftext", ""),
                "url":       f"https://reddit.com{post.get('permalink', '')}",
                "score":     post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "created_utc": post.get("created_utc", 0),
            })

        after = data.get("after")
        if not after or len(posts) >= limit:
            break
        time.sleep(REQUEST_DELAY)

    log.info("Fetched %d posts from r/%s", len(posts), subreddit)
    return posts[:limit]


def fetch_comments(post_url: str, depth: int = COMMENT_DEPTH) -> list[str]:
    """Fetch top-level and nested comments for a post."""
    json_url = post_url.rstrip("/") + ".json"
    try:
        resp = requests.get(json_url, headers=REDDIT_HEADERS,
                            params={"depth": depth, "limit": 50}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.warning("Could not fetch comments for %s: %s", post_url, e)
        return []

    comments = []
    if len(data) < 2:
        return comments

    def extract(node):
        if not isinstance(node, dict):
            return
        kind = node.get("kind")
        if kind == "Listing":
            for child in node.get("data", {}).get("children", []):
                extract(child)
        elif kind == "t1":  # comment
            body = node.get("data", {}).get("body", "").strip()
            if body and body != "[deleted]" and body != "[removed]":
                comments.append(body)
            replies = node.get("data", {}).get("replies", {})
            if isinstance(replies, dict):
                extract(replies)

    extract(data[1])
    return comments


# ── Gemini Extraction ─────────────────────────────────────────────────────────
def build_batch_text(posts: list[dict]) -> str:
    """Format a batch of posts+comments into a text block for Gemini."""
    parts = []
    for i, post in enumerate(posts, 1):
        section = [f"--- POST {i} ---"]
        section.append(f"URL: {post['url']}")
        section.append(f"TITLE: {post['title']}")
        section.append(f"SCORE: {post['score']}")
        if post.get("selftext") and post["selftext"] not in ("[deleted]", "[removed]", ""):
            section.append(f"BODY:\n{post['selftext'][:1500]}")
        if post.get("comments"):
            section.append("COMMENTS:")
            for j, c in enumerate(post["comments"][:15], 1):
                section.append(f"  [{j}] {c[:500]}")
        parts.append("\n".join(section))
    return "\n\n".join(parts)


def extract_quotes_from_batch(model, posts: list[dict]) -> list[dict]:
    """Send a batch to Gemini and parse the returned JSON."""
    batch_text = build_batch_text(posts)
    prompt = f"{SYSTEM_PROMPT}\n\n=== REDDIT CONTENT ===\n\n{batch_text}"

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
    except Exception as e:
        log.error("Gemini API error: %s", e)
        return []

    # Strip markdown fences if the model added them anyway
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        quotes = json.loads(raw)
        if not isinstance(quotes, list):
            log.warning("Gemini returned non-list JSON; skipping batch")
            return []
    except json.JSONDecodeError as e:
        log.warning("JSON parse error (%s) — raw response:\n%s", e, raw[:400])
        return []

    # Validate and filter
    valid = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        quote_text = q.get("quote", "").strip()
        if len(quote_text) < MIN_QUOTE_LEN:
            continue
        q["quote"] = quote_text
        valid.append(q)

    return valid


# ── Deduplication ─────────────────────────────────────────────────────────────
def dedup_quotes(quotes: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for q in quotes:
        h = hashlib.md5(q["quote"].encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            unique.append(q)
    return unique


# ── Output Writers ────────────────────────────────────────────────────────────
def write_csv(quotes: list[dict], path: Path):
    import csv
    fields = ["quote", "pain_category", "source_type", "post_title",
              "post_score", "post_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(quotes)
    log.info("CSV written: %s (%d rows)", path, len(quotes))


def write_markdown(quotes: list[dict], path: Path, subreddit: str, date_str: str):
    category_order = ["frustration", "struggle", "wish", "goal"]
    grouped: dict[str, list] = {c: [] for c in category_order}
    for q in quotes:
        cat = q.get("pain_category", "frustration")
        grouped.setdefault(cat, []).append(q)

    lines = [
        f"# r/{subreddit} — Pain Points & Desires",
        f"**Date:** {date_str}  |  **Total quotes:** {len(quotes)}",
        "",
    ]

    for cat in category_order:
        items = grouped.get(cat, [])
        if not items:
            continue
        emoji = {"frustration": "😤", "struggle": "💢", "wish": "✨", "goal": "🎯"}.get(cat, "•")
        lines.append(f"## {emoji} {cat.title()}s  ({len(items)})")
        lines.append("")
        for q in items:
            lines.append(f"> {q['quote']}")
            lines.append(f"*— {q.get('source_type','?')} | score {q.get('post_score','?')} | [{q.get('post_title','?')[:60]}]({q.get('post_url','')})*")
            lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info("Markdown written: %s", path)


# ── Main ──────────────────────────────────────────────────────────────────────
def run(subreddit: str, gemini_api_key: str, output_dir: Path = OUTPUT_DIR,
        fetch_comments_flag: bool = True):

    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = output_dir / subreddit
    out.mkdir(parents=True, exist_ok=True)

    # ── 1. Fetch posts ────────────────────────────────────────────────────────
    posts = fetch_posts(subreddit)

    # ── 2. Optionally enrich with comments ───────────────────────────────────
    if fetch_comments_flag:
        log.info("Fetching comments for %d posts…", len(posts))
        for i, post in enumerate(posts):
            if post["num_comments"] > 0:
                post["comments"] = fetch_comments(post["url"])
                time.sleep(REQUEST_DELAY)
            else:
                post["comments"] = []
            if (i + 1) % 10 == 0:
                log.info("  Comments: %d/%d posts done", i + 1, len(posts))
    else:
        for post in posts:
            post["comments"] = []

    # ── 3. Extract quotes in batches ─────────────────────────────────────────
    all_quotes = []
    batches = [posts[i:i+BATCH_SIZE] for i in range(0, len(posts), BATCH_SIZE)]
    log.info("Sending %d batches to Gemini…", len(batches))

    for idx, batch in enumerate(batches, 1):
        log.info("  Batch %d/%d (%d posts)…", idx, len(batches), len(batch))
        quotes = extract_quotes_from_batch(model, batch)
        log.info("    → %d quotes extracted", len(quotes))
        all_quotes.extend(quotes)
        time.sleep(1)   # brief pause between Gemini calls

    # ── 4. Dedup ──────────────────────────────────────────────────────────────
    all_quotes = dedup_quotes(all_quotes)
    log.info("Total unique quotes: %d", len(all_quotes))

    # ── 5. Write output ───────────────────────────────────────────────────────
    csv_path = out / f"{today}.csv"
    md_path  = out / f"{today}.md"
    write_csv(all_quotes, csv_path)
    write_markdown(all_quotes, md_path, subreddit, today)

    return csv_path, md_path, len(all_quotes)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reddit pain-point scraper using Gemini Flash 2.5")
    parser.add_argument("subreddit",         help="Subreddit name (without r/)")
    parser.add_argument("--no-comments",     action="store_true",
                        help="Skip comment fetching (faster, fewer API calls)")
    parser.add_argument("--output-dir",      default="output",
                        help="Directory to write output files (default: ./output)")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: Set the GEMINI_API_KEY environment variable.")

    csv_p, md_p, count = run(
        subreddit        = args.subreddit,
        gemini_api_key   = api_key,
        output_dir       = Path(args.output_dir),
        fetch_comments_flag = not args.no_comments,
    )
    print(f"\n✅  Done — {count} quotes extracted")
    print(f"   CSV:      {csv_p}")
    print(f"   Markdown: {md_p}")
