# arXiv Open Cluster Browser

A serverless web application that automatically fetches recent
[arXiv](https://arxiv.org) papers on open clusters and presents them via a static
[GitHub Pages](https://pages.github.com/) site.

**[Live site →](https://gabriel-p.github.io/arxiv/)**

---

## Overview

The app monitors the `astro-ph.GA` (Galactic Astrophysics) category on arXiv and
filters submissions to surface only those relevant to open stellar clusters. Papers
are scored using a keyword-matching algorithm and displayed in an interactive,
sortable list.

## Architecture

```
arXiv API ──► fetch-arxiv.py ──► arxiv.json ──► parse-arxiv.js ──► index.html (GitHub Pages)
                (GitHub Actions, every 12h)        (browser, on page load)
```

### Components

| File | Role |
|---|---|
| `scripts/fetch-arxiv.py` | Fetches, scores, and filters papers; writes `arxiv.json` |
| `scripts/parse-arxiv.js` | Loads `arxiv.json` in the browser and renders the paper list |
| `scripts/terms.json` | Exclusion terms and penalty weights for the scoring algorithm |
| `index.html` / `style.css` | Static front-end served via GitHub Pages |
| `.github/workflows/main.yml` | Scheduled CI workflow (every 12 h) that runs the fetch script |
| `.github/workflows/static.yml` | Manual workflow that deploys the site to GitHub Pages |

## Scoring Algorithm

Each paper receives a relevance **score** computed from its title and abstract:

1. **Keyword matches** — patterns are weighted and title matches are multiplied by `3×`:
   - `"open cluster(s)"` → weight 2.0
   - `"OC"` / `"OCs"` (case-sensitive abbreviation) → weight 1.5
   - `"star cluster"`, `"stellar cluster"`, `"embedded cluster"` → weight 1.0
   - Cluster science vocabulary (`catalog`, `membership`, `age`, `distance`, …) → weight 0.8–1.5
   - `"OB/stellar association"`, `"moving group"` → weight 0.6–0.8

2. **Numeric sample boost** — large-sample papers like *"500 open clusters"* get extra points:
   - count > 1 000 → +50 pts
   - count > 100 → +10 pts
   - count > 10 → +5 pts
   - Catalog identifiers (NGC, IC, Berkeley, …) are excluded by negative lookbehind

3. **Exclusion / penalties** — papers are removed or penalised for extragalactic terms:
   - **Hard exclusions** (title match → paper dropped): `"galaxy cluster"`, `"globular cluster"`, `"high redshift"`, etc.
   - **Soft penalties** (subtracted from score): `"dwarf galaxy"` (−3), `"black hole"` (−3), `"intracluster medium"` (−4), etc.

4. **Age decay** — score decreases by `0.5` per day of paper age (papers older than 30 days are dropped entirely).

## Data Flow

1. The `main.yml` workflow runs every 12 hours (or on manual dispatch).
2. `fetch-arxiv.py` queries the arXiv API in **3 chunks of 200 results** with 3-second delays between requests.
3. Each paper is filtered, scored, deduplicated, and serialised to `arxiv_new.json`.
4. If `arxiv_new.json` differs from the current `arxiv.json`, it replaces it and is committed to `main`.
5. The browser fetches `arxiv.json` from the raw GitHub content URL and renders the paper list.

### `arxiv.json` structure

```json
{
  "fetched_at": "2024-01-15T12:00:00.000000",
  "entries": [
    {
      "id": "https://arxiv.org/abs/2401.12345",
      "title": "Paper title",
      "summary": "Abstract text",
      "published": "2024-01-14T00:00:00Z",
      "author": [{"name": "Author Name"}],
      "score": 8.5
    }
  ]
}
```

## Local Development

**Serve the site locally:**

```bash
uv run python -m http.server 8000
# then open http://localhost:8000/
```

**Test the fetch script with a local cache** (avoids hitting the arXiv API):

1. In `scripts/fetch-arxiv.py`, replace `CACHE_FILE = ""` with:
   ```python
   CACHE_FILE = "arxiv_cache.xml"
   ```
2. Run the script once to populate the cache, or provide your own XML file.
3. Subsequent runs will load from the cache instead of the API.

**Install dependencies:**

```bash
pip install -r requirements.txt
```

## Configuration

All tunable parameters live at the top of `scripts/fetch-arxiv.py`:

| Constant | Default | Description |
|---|---|---|
| `CHUNKS` | `3` | Number of API request chunks |
| `RESULTS_PER_CHUNK` | `200` | Results per chunk |
| `WAIT_TIME` | `3` s | Delay between API calls |
| `N_DAYS_BACK` | `30` | Papers older than this are ignored |
| `SCORE_DECAY_PER_DAY` | `0.5` | Score subtracted per day of age |
| `MINIMUM_SCORE` | `0.0` | Papers at or below this score are dropped |
| `title_weight` | `3.0` | Multiplier applied to title keyword matches |

Exclusion terms and penalty weights are in `scripts/terms.json`.

## Front-end Features

- Sort papers by **date** or **relevance score**
- Filter by **minimum score** threshold
- Expand / collapse paper abstracts
- Shows the timestamp of the last data fetch
