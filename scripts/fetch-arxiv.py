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
MAX_RETRIES = 3  # Number of retries for failed API calls
RETRY_WAIT = 10 * WAIT_TIME  # Wait time between retries

CACHE_FILE = ""  # Use in production
# CACHE_FILE = "arxiv_cache.xml"  # For local testing

MINIMUM_SCORE = 0.0
N_DAYS_BACK = 30
# Points subtracted per day of article age
SCORE_DECAY_PER_DAY = 0.25
FILE_NAME = "arxiv_new.json"
# Multiplier applied separately in score_keywords()
title_weight = 3.0

# ── Positive-signal patterns ──────────────────────────────────────────────────
# Each entry: (compiled pattern, weight)
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
_EXCLUSIONS_FILE = os.path.join(os.path.dirname(__file__), "terms.json")
with open(_EXCLUSIONS_FILE, encoding="utf-8") as _f:
    _excl = json.load(_f)

# HARD: checked against TITLE only — unambiguous non-OC papers
HARD_EXCLUSIONS: list[str] = [s.lower() for s in _excl["hard_exclusions"]]
# SOFT: checked against full text (title + summary); each hit subtracts a penalty
# Covers cases where the paper is likely extragalactic but could legitimately discuss OCs
SOFT_EXCLUSION_TERMS: list[tuple[str, float]] = [
    (p[0].lower(), p[1]) for p in _excl["soft_exclusion_terms"]
]
# Negative lookbehinds prevent matching catalog identifiers like "NGC 2516"
_CATALOGS: tuple[str, ...] = tuple(s.lower() for s in _excl["catalogs"])


# ── Numeric pattern ───────────────────────────────────────────────────────────
# Detects large OC sample papers (e.g. "500 open clusters", "1200 clusters")
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
            obj = xmltodict.parse(xml_content)
            entries_raw = obj.get("feed", {}).get("entry", [])
        return entries_raw

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

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.get(BASE_URL + query)
                response.raise_for_status()
                batch_xml = response.text

                batch_obj = xmltodict.parse(batch_xml)
                batch_entries = batch_obj.get("feed", {}).get("entry", [])

                if isinstance(batch_entries, dict):
                    batch_entries = [batch_entries]

                entries_raw.extend(batch_entries)
                break  # success

            except Exception as e:
                print(
                    f"Error during chunk {i} (attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                )
                if attempt < MAX_RETRIES - 1:
                    print(f"Retrying in {RETRY_WAIT} seconds...")
                    time.sleep(RETRY_WAIT)
                else:
                    print(f"Chunk {i} failed after {MAX_RETRIES} attempts. Aborting.")
                    return entries_raw

        if i < CHUNKS - 1:
            print(f"Sleeping for {WAIT_TIME} seconds to respect API limits...")
            time.sleep(WAIT_TIME)

    if CACHE_FILE:
        cache_data = {"feed": {"entry": entries_raw}}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(xmltodict.unparse(cache_data, pretty=True))

    return entries_raw


def score_keywords(
    title: str, summary: str, title_weight: float = title_weight
) -> float:
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
        if score > MINIMUM_SCORE:
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

    if filtered:
        fetch_timestamp = datetime.now().isoformat()
        output_data = {"fetched_at": fetch_timestamp, "entries": filtered}
        # Write to file
        with open(FILE_NAME, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
