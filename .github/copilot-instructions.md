# Copilot Instructions for arXiv Open Cluster Browser

## Project Overview

This is a serverless web application that fetches recent arXiv papers on open clusters and presents them via a static GitHub Pages site. The project has two main components:

1. **Backend**: A Python script that fetches and filters arXiv papers
2. **Frontend**: A static HTML/CSS/JS site that displays filtered papers

## Architecture

### Data Flow

1. **`scripts/fetch-arxiv.py`** (CI/CD triggered every 12 hours via `main.yml`)
   - Fetches latest astro-ph.GA (galactic astronomy) papers from arXiv API
   - Fetches in 3 chunks of 200 results each with 3-second delays to respect API limits
   - Filters papers to past 30 days only
   - Scores papers based on keyword presence (open cluster, star cluster, stellar cluster) with adjustable weights
   - Excludes papers matching extragalactic terms (galaxy cluster, dwarf galaxy, etc.)
   - Detects numeric patterns (e.g., "500 open clusters") to boost relevance scores
   - Outputs to `arxiv.json` with `fetched_at` timestamp and entries array
   - Commits and pushes changes only if JSON content actually changed

2. **`scripts/parse-arxiv.js`** (runs in browser on page load)
   - Fetches `arxiv.json` from GitHub raw content URL
   - Renders papers with title, authors, date, truncated abstract (200 chars)
   - Provides sorting (by date or score) and filtering (by minimum score)
   - Abstract expand/collapse toggle functionality

3. **Static site** (`index.html`, `style.css`)
   - GitHub Pages deployment via `static.yml` workflow
   - Responsive design with CSS custom properties for theming

## Development Commands

### Local Testing

**Serve locally for testing:**
```bash
uv run python -m http.server 8000
```
Then visit `http://localhost:8000/`

**Test the fetch script (with local cache):**
- Uncomment `CACHE_FILE = "arxiv_cache.xml"` in `scripts/fetch-arxiv.py` to use cached data
- Run: `python scripts/fetch-arxiv.py`
- This outputs to `arxiv.json` without hitting the arXiv API

### CI/CD

- **Main workflow** (`main.yml`): Runs on schedule (every 12 hours) and on manual dispatch
  - Validates JSON output before committing
  - Only commits if content changed and validation passed
  - Uses `GITHUB_TOKEN` for authentication

- **Pages workflow** (`static.yml`): Manual dispatch deployment
  - Uploads entire repository to GitHub Pages

## Key Implementation Details

### Scoring Algorithm

Papers are scored based on:
- **Keyword matches** (case-insensitive, singular/plural handling):
  - "open cluster" or "open clusters": weight 1.5 (×3 multiplier if in title)
  - "star cluster" / "stellar cluster": lower weights
- **Numeric patterns**: Numbers followed by "clusters" (e.g., "100 open clusters")
  - Score +50 if count > 1000
  - Score +10 if count > 100
  - Score +5 if count > 10
- **Exclusion check**: Papers with terms like "galaxy cluster", "dwarf galaxy", etc. are filtered out regardless of score
- **Negative lookbehind** in numeric pattern: Prevents catalog prefixes (NGC, IC, Berkeley, etc.) from triggering false positives

### JSON Structure

```json
{
  "fetched_at": "ISO 8601 timestamp",
  "entries": [
    {
      "id": "arXiv URL",
      "title": "Paper title",
      "summary": "Abstract",
      "updated": "ISO 8601 publication date",
      "author": [{"name": "Author Name"}, ...],
      "score": 2.5,
      ...
    }
  ]
}
```

### Caching Mechanism

- Optional local caching for development: Set `CACHE_FILE` to enable
- Cached data stored as synthetic XML structure with all chunks combined
- In production, `CACHE_FILE = ""` (disabled) to always fetch fresh data

## Conventions

- **Configuration via module-level constants**: `BASE_URL`, `SEARCH_QUERY`, `KEYWORDS`, `EXCLUSION_TERMS`, `N_DAYS_BACK` are all easy to adjust
- **Chunked API fetching**: Respects arXiv API rate limits with configurable `WAIT_TIME`
- **Deduplication**: Papers are deduplicated by `id` before saving
- **HTML Safety**: No XSS protection needed (arXiv data is sanitized server-side), but abstracts are truncated in frontend
