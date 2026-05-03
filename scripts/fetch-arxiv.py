import json
import os
import re
import time
from datetime import datetime, timedelta

import requests
import xmltodict

# Configuration
BASE_URL = "https://export.arxiv.org/api/query?"
SEARCH_QUERY = "cat:astro-ph.GA*"
CHUNKS = 3
RESULTS_PER_CHUNK = 200
WAIT_TIME = 3  # Seconds to sleep between API calls

# CACHE_FILE = ""  # Use in production
CACHE_FILE = "arxiv_cache.xml"  # For local testing

N_DAYS_BACK = 30
SCORE_DECAY_PER_DAY = 0.25  # Points subtracted per day of article age
FILE_NAME = "arxiv.json"

# ── Positive-signal patterns ──────────────────────────────────────────────────
# Each entry: (compiled pattern, weight)
# title_weight multiplier applied separately in score_keywords()

_CLUSTER_PATTERNS: list[tuple[re.Pattern, float]] = [
    # Primary OC terms
    (re.compile(r"\bopen\s+clusters?\b", re.IGNORECASE), 2.0),
    (re.compile(r"\bOCs?\b"), 1.5),  # case-sensitive: "OC"/"OCs" abbreviation
    # General stellar clusters (galactic context enforced by exclusions)
    (re.compile(r"\b(?:star|stellar)\s+clusters?\b", re.IGNORECASE), 1.0),
    (re.compile(r"\bembedded\s+clusters?\b", re.IGNORECASE), 1.0),
    # Cluster with galactic/age qualifiers
    (
        re.compile(
            r"\b(?:young|old|ancient|intermediate[-\s]age|nearby|disk|galactic)\s+clusters?\b",
            re.IGNORECASE,
        ),
        1.0,
    ),
    # Cluster science vocabulary
    (
        re.compile(
            r"\bcluster\s+(?:catalog(?:ue)?|census|inventory|sample)\b", re.IGNORECASE
        ),
        1.5,
    ),
    (re.compile(r"\bcluster\s+(?:membership|members)\b", re.IGNORECASE), 1.0),
    (
        re.compile(
            r"\bcluster\s+(?:age|distance|mass|radius|metallicity)\b", re.IGNORECASE
        ),
        0.8,
    ),
    (re.compile(r"\bnew\s+(?:open\s+)?cluster\b", re.IGNORECASE), 1.5),
    (re.compile(r"\bcluster\s+candidates?\b", re.IGNORECASE), 1.2),
    # Dissolution / dynamical evolution
    (
        re.compile(
            r"\bcluster\s+(?:dissolution|disruption|evaporation|dispersion)\b",
            re.IGNORECASE,
        ),
        1.0,
    ),
    (
        re.compile(
            r"\btidal\s+(?:tails?|streams?|radius|stripping|debris)\b", re.IGNORECASE
        ),
        0.8,
    ),
    (re.compile(r"\bcluster\s+(?:corona|halo|escapers?)\b", re.IGNORECASE), 0.8),
    # Related stellar structures
    (re.compile(r"\b(?:OB|stellar)\s+associations?\b", re.IGNORECASE), 0.8),
    (re.compile(r"\bmoving\s+groups?\b", re.IGNORECASE), 0.6),
]

# ── Exclusion terms ───────────────────────────────────────────────────────────
# HARD: checked against TITLE only — unambiguous non-OC papers
HARD_EXCLUSIONS = [
    "galaxy cluster",
    "cluster of galaxies",
    "clusters of galaxies",
    "cluster galaxies",
    "cluster galaxy",
    "globular cluster",
    "globular clusters",
    "nuclear star cluster",
    "super star cluster",
    "large magellanic",
    "small magellanic",
    "lmc cluster",
    "smc cluster",
    "lmc star cluster",
    "smc star cluster",
    "coma cluster",
    "virgo cluster",
    "fornax cluster",
    "computing cluster",
    "data cluster",
    "kubernetes",
    "hadoop",
]

# SOFT: checked against full text (title + summary); each hit subtracts a penalty
# Covers cases where the paper is likely extragalactic but could legitimately discuss OCs
SOFT_EXCLUSION_TERMS: list[tuple[str, float]] = [
    ("dwarf galaxy", 3.0),
    ("dwarf galaxies", 3.0),
    ("starburst galaxy", 3.0),
    ("radio galaxy", 3.0),
    ("ring galaxy", 3.0),
    ("quiescent galaxy", 3.0),
    ("spiral galaxies", 2.0),
    ("galaxy survey", 2.0),
    ("cluster redshift", 4.0),
    (" lmc ", 3.0),
    (" smc ", 3.0),
    ("m31 ", 2.0),
    ("m33 ", 2.0),
    ("m51 ", 2.0),
    ("m82 ", 2.0),
    ("ngc 1275", 3.0),
    ("ngc 628", 2.0),
    # proto-cluster in extragalactic sense — high-z context usually evident
    ("proto-cluster", 2.0),
    ("abell cluster", 4.0),  # narrowed from bare "abell" to avoid author-name FP
    ("intracluster medium", 4.0),
    ("icm ", 2.0),  # intracluster medium abbreviation
]

# ── Numeric pattern ───────────────────────────────────────────────────────────
# Detects large OC sample papers (e.g. "500 open clusters", "1200 clusters")
# Negative lookbehinds prevent matching catalog identifiers like "NGC 2516"
_CATALOGS = ("NGC", "IC", "Berkeley", "Ruprecht", "Trumpler", "Melotte", "HD")
_neg_lookbehinds = "".join(f"(?<!{name}\\s)" for name in _CATALOGS)
_number_pattern = r"\d{2,3}(?:,\d{3})+|\d{2,}"
numeric_pattern = (
    rf"{_neg_lookbehinds}"
    r"(?<!\d)"
    rf"\b({_number_pattern})\b\s+"
    r"(?:(?:new\s+)?(?:open|star)\s+)?"
    r"clusters?\b"
    r"(?!\s+members\b|\s+stars\b)"
)


def main():
    """ """
    # Fetch data from arXiv with Chunking and Caching
    entries_raw = fetch_arxiv()
    if entries_raw:
        dates = [e.get("published") for e in entries_raw if e.get("published")]
        if dates:
            print(f"Total entries fetched: {len(entries_raw)}")
            print(f"Oldest entry in this pool: {min(dates)}")

    # Filter and score new entries
    new_entries = filter_score(entries_raw)

    # Save results to file
    save_to_file(new_entries)


def fetch_arxiv():
    """ """
    # Check for cache first
    if os.path.exists(CACHE_FILE):
        print(f"Loading data from local cache: {CACHE_FILE}")
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            xml_content = f.read()
            # If using cache, we parse the single cached file
            obj = xmltodict.parse(xml_content)
            entries_raw = obj.get("feed", {}).get("entry", [])
        return entries_raw

    #
    print(f"Fetching {CHUNKS * RESULTS_PER_CHUNK} articles in {CHUNKS} chunks...")
    entries_raw = []
    for i in range(CHUNKS):
        start_index = i * RESULTS_PER_CHUNK
        print(
            f"Requesting results {start_index} to {start_index + RESULTS_PER_CHUNK}..."
        )
        query = (
            f"search_query={SEARCH_QUERY}"
            f"&sortBy=submittedDate&sortOrder=descending"
            f"&start={start_index}&max_results={RESULTS_PER_CHUNK}"
        )

        try:
            response = requests.get(BASE_URL + query)
            response.raise_for_status()
            batch_xml = response.text

            # Parse this specific batch
            batch_obj = xmltodict.parse(batch_xml)
            batch_entries = batch_obj.get("feed", {}).get("entry", [])

            if isinstance(batch_entries, dict):
                batch_entries = [batch_entries]

            entries_raw.extend(batch_entries)

        except Exception as e:
            print(f"Error during chunk {i}: {e}")
            break

        if i < CHUNKS - 1:
            print(f"Sleeping for {WAIT_TIME} seconds to respect API limits...")
            time.sleep(WAIT_TIME)

    if CACHE_FILE:
        # We wrap the entries in a synthetic root to maintain a valid XML-like
        # structure in the cache
        cache_data = {"feed": {"entry": entries_raw}}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            # Using xmltodict.unparse to create a single valid XML file containing
            # all chunks
            f.write(xmltodict.unparse(cache_data, pretty=True))

    return entries_raw


def score_keywords(title: str, summary: str, title_weight: float = 3.0) -> float:
    """Aggregate open-cluster keyword score for a title/summary pair."""
    score = 0.0
    for pattern, w in _CLUSTER_PATTERNS:
        score += len(pattern.findall(title)) * w * title_weight
        score += len(pattern.findall(summary)) * w
    return score


def filter_score(entries_raw):
    """ """
    # Calculate date N days back
    date_n_days_back = datetime.now() - timedelta(days=N_DAYS_BACK)
    date_threshold_str = date_n_days_back.strftime("%Y-%m-%d")

    new_entries = []
    for entry in entries_raw:
        published = entry.get("published", "")
        if published < date_threshold_str:
            continue

        title = entry.get("title", "").lower().replace("\n", " ")
        summary = entry.get("summary", "").lower().replace("\n", " ")

        # Hard exclusions: title-only (unambiguous non-OC paper type)
        if any(ex in title for ex in HARD_EXCLUSIONS):
            continue

        score = score_keywords(title, summary)

        # Numeric-sample boost (large cluster samples = high relevance)
        for txt in (title, summary):
            # The lookbehind ensures numbers preceded by catalog identifiers are ignored
            for match in re.findall(numeric_pattern, txt, flags=re.IGNORECASE):
                count = int(match.replace(",", ""))
                if count > 1000:
                    score += 50
                elif count > 100:
                    score += 10
                elif count > 10:
                    score += 5

        if score <= 0:
            continue

        # Soft exclusions: subtract penalty for extragalactic context signals
        full_text = title + " " + summary
        for term, penalty in SOFT_EXCLUSION_TERMS:
            if term in full_text:
                score -= penalty

        if score <= 0:
            continue

        # Apply age-based decay: subtract points for each day old
        published_date = datetime.fromisoformat(
            entry.get("published", "").replace("Z", "+00:00")
        )
        age_days = (datetime.now(published_date.tzinfo) - published_date).days
        score = max(0, score - (age_days * SCORE_DECAY_PER_DAY))

        # Only include if score is still positive after decay
        if score > 0:
            entry["score"] = round(score, 2)
            new_entries.append(entry)

    print(f"Identified {len(new_entries)} new relevant entries after keyword scoring.")
    return new_entries


def save_to_file(new_entries):
    """ """
    # De-duplicate and filter
    unique_map = {
        e["id"]: e
        for e in new_entries
        if e.get("title", "").lower() != "no articles found"
    }

    # Sort the resulting values
    filtered = sorted(
        unique_map.values(), key=lambda x: x.get("published", ""), reverse=True
    )

    if not filtered:
        entries_to_save = [
            {
                "title": "No articles found",
                "id": "#",
                "author": [{"name": " "}],
                "updated": datetime.now().strftime("%Y-%m-%d"),
                "score": 0,
                "summary": "No articles matching the filters were found in the current submissions.",
            }
        ]
    else:
        entries_to_save = filtered

    fetch_timestamp = datetime.now().isoformat()
    output_data = {"fetched_at": fetch_timestamp, "entries": entries_to_save}

    # Write to file
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
