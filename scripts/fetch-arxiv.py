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

CACHE_FILE = ""  # Use in production
# CACHE_FILE = "arxiv_cache.xml"  # For local testing

KEYWORDS = [
    {"term": "open cluster", "weight": 1.5},
    {"term": "star cluster", "weight": 1},
    {"term": "stellar cluster", "weight": 0.5},
]
# Terms to effectively nullify the score (Extragalactic/Cosmological context)
EXCLUSION_TERMS = [
    "galaxy cluster",
    "cluster galaxies",
    "cluster galaxy",
    "cluster of galaxies",
    "clusters of galaxies",
    "dwarf galaxy",
    "dwarf galaxies",
    "radio galaxy",
    "radio galaxies",
    "spheroidal galaxy",
    "spheroidal galaxies",
    "starburst galaxy",
    "starburst galaxies",
    "legus galaxy",
    "legus galaxies",
    "red galaxy",
    "red galaxies",
    "galaxy survey",
    "survey of galaxies",
    "survey galaxies",
    "cluster redshift",
    "spiral galaxies",
    "abell",
    "m82",
    "m51",
    "m33",
    "m31",
    "ngc 1275",
    "ngc 628",
]

# Numeric pattern refined with negative lookbehind to ignore catalog prefixes
# This prevents "Ruprecht 147" or "NGC 2516" from triggering the "count > N" logic.
# catalogs to exclude
catalogs = (
    "NGC",
    "IC",
    "Berkeley",
    "Ruprecht",
    "Trumpler",
    "Melotte",
    "HD",
)

# build the negative lookbehind block
neg_lookbehinds = "".join(f"(?<!{name}\\s)" for name in catalogs)
#
number_pattern = r"\d{2,3}(?:,\d{3})+|\d{2,}"
# Final pattern
numeric_pattern = (
    rf"{neg_lookbehinds}"
    r"(?<!\d)"
    rf"\b({number_pattern})\b\s+"
    r"(?:(?:new\s+)?(?:open|star)\s+)?"
    r"clusters?\b"
    r"(?!\s+members\b|\s+stars\b)"
)


N_DAYS_BACK = 30
SCORE_DECAY_PER_DAY = 0.25  # Points subtracted per day of article age
FILE_NAME = "arxiv.json"


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
    all_fetched_entries = []
    for i in range(CHUNKS):
        start_index = i * RESULTS_PER_CHUNK
        print(
            f"Requesting results {start_index} to {start_index + RESULTS_PER_CHUNK}..."
        )
        query = f"search_query={SEARCH_QUERY}&sortBy=submittedDate&sortOrder=descending&start={start_index}&max_results={RESULTS_PER_CHUNK}"
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
            if CACHE_FILE != "":
                # For caching all batches
                all_fetched_entries.extend(batch_entries)

        except Exception as e:
            print(f"Error during chunk {i}: {e}")
            break

        if i < CHUNKS - 1:
            print(f"Sleeping for {WAIT_TIME} seconds to respect API limits...")
            time.sleep(WAIT_TIME)

    if CACHE_FILE != "":
        # Save ALL entries to the cache file
        if all_fetched_entries:
            entries_raw = all_fetched_entries
            # We wrap the entries in a synthetic root to maintain a valid XML-like
            # structure in the cache
            cache_data = {"feed": {"entry": all_fetched_entries}}
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                # Using xmltodict.unparse to create a single valid XML file containing
                # all chunks
                f.write(xmltodict.unparse(cache_data, pretty=True))

    return entries_raw


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

        # Exclusion Check: If galaxy cluster terms are present, exclude
        if any(ex in title or ex in summary for ex in EXCLUSION_TERMS):
            # print(title)
            continue

        score = 0.0
        # Keyword scoring
        for kw in KEYWORDS:
            term = kw["term"]
            weight = kw["weight"]

            # s? allows for 0 or 1 's' at the end of the term
            regex_pattern = rf"\b{re.escape(term)}s?\b"

            title_count = len(re.findall(regex_pattern, title))
            summary_count = len(re.findall(regex_pattern, summary))

            score += (title_count * weight * 3) + (summary_count * weight)

        # Numeric pattern detection (e.g., "500 open clusters")
        for txt in (title, summary):
            # The lookbehind ensures numbers preceded by catalog identifiers are ignored
            matches = re.findall(numeric_pattern, txt, flags=re.IGNORECASE)
            for match in matches:
                count = int(match)
                if count > 1000:
                    score += 50
                elif count > 100:
                    score += 10
                elif count > 10:
                    score += 5

        if score > 0:
            # Apply age-based decay: subtract points for each day old
            published_date = datetime.fromisoformat(entry.get("published", "").replace("Z", "+00:00"))
            age_days = (datetime.now(published_date.tzinfo) - published_date).days
            score = max(0, score - (age_days * SCORE_DECAY_PER_DAY))
            
            # Only include if score is still positive after decay
            if score > 0:
                entry["score"] = score
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
    filtered_entries = sorted(
        unique_map.values(), key=lambda x: x.get("published", ""), reverse=True
    )

    # Prepare Save Data
    if not filtered_entries:
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
        entries_to_save = filtered_entries

    fetch_timestamp = datetime.now().isoformat()
    output_data = {"fetched_at": fetch_timestamp, "entries": entries_to_save}

    # Write to file
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
