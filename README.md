# Reddit Pain Point Scraper

Scrapes a subreddit daily and uses **Gemini Flash 2.5** to extract authentic
quotes where people express pain points, frustrations, wishes, and goals.

## Setup (5 minutes)

```bash
# 1. Clone / copy the files
# 2. Create a virtual environment
python -m venv venv && source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Add your Gemini API key
cp .env.example .env
# then edit .env and paste your key
```

## Run manually

```bash
# Basic run — scrapes r/entrepreneur
python scraper.py entrepreneur

# Skip comment fetching (faster, lower API cost)
python scraper.py SaaS --no-comments

# Custom output directory
python scraper.py smallbusiness --output-dir ~/Desktop/output
```

## Schedule with cron (Linux/Mac)

```bash
chmod +x run_daily.sh

# Open crontab
crontab -e

# Add this line to run every day at 7am
0 7 * * * /full/path/to/run_daily.sh entrepreneur >> /full/path/to/logs/scraper.log 2>&1
```

## Schedule with GitHub Actions (no server needed)

1. Push the project to a GitHub repo
2. Go to Settings → Secrets → Actions → New secret
3. Add `GEMINI_API_KEY` with your key
4. Edit `.github/workflows/daily_scrape.yml` and set your `SUBREDDIT`
5. Output files will be committed back to the repo each day

## Output files

Each run produces two files in `output/<subreddit>/YYYY-MM-DD.*`:

| File | Contents |
|------|----------|
| `.csv` | Structured data — quote, category, source type, post title, score, URL |
| `.md`  | Human-readable, grouped by pain category (frustration / struggle / wish / goal) |

## What Gemini keeps vs skips

**Keeps:** Specific and concrete pain, genuine emotional expression, named obstacles, action-oriented desires

**Skips:** Vague complaints, jokes/sarcasm, meta-Reddit discussion, pure questions, generic statements under 40 chars

## Cost estimate

| Subreddit size | Posts/day | Gemini calls | Est. cost/day |
|----------------|-----------|--------------|---------------|
| Small          | 50        | ~25          | ~$0.02        |
| Medium         | 100       | ~50          | ~$0.04        |
| Large          | 100+      | ~50          | ~$0.05        |

*Based on Gemini Flash 2.5 pricing as of mid-2025*
