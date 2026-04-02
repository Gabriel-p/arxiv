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
EXCLUSION_TERMS = ["galaxy cluster", "cluster of galaxies", "cluster galaxies"]

# Numeric pattern refined with negative lookbehind to ignore catalog prefixes
# This prevents "Ruprecht 147" or "NGC 2516" from triggering the "count > N" logic.
numeric_pattern = (
    r"(?<!NGC\s)(?<!IC\s)(?<!Berkeley\s)(?<!Ruprecht\s)(?<!Trumpler\s)(?<!Melotte\s)"
    r"(?<!\d)\b(\d{2,})\b\s+(?:(?:new\s+)?(?:open|star)\s+)?clusters?"
)


N_DAYS_BACK = 30
FILE_NAME = "arxiv.json"


def main():
    """ """
    # Fetch data from arXiv with Chunking and Caching
    entries_raw = fetch_arxiv()

    # Print the oldest entry from the total pool
    if entries_raw:
        dates = [e.get("published") for e in entries_raw if e.get("published")]
        if dates:
            print(f"Total entries fetched: {len(entries_raw)}")
            print(f"Oldest entry in this pool: {min(dates)}")

    # Filter and score new entries
    new_entries = filter_score(entries_raw)

    # Merge and Deduplicate
    all_entries_map = {}
    for e in new_entries:
        all_entries_map[e["id"]] = e
    unique_entries = list(all_entries_map.values())

    # Filter out placeholder and Sort
    filtered_entries = [
        e for e in unique_entries if e.get("title", "").lower() != "no articles found"
    ]
    filtered_entries.sort(key=lambda x: x.get("published", ""), reverse=True)

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

        # Exclusion Check: If galaxy cluster terms are present, weight 0
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
                if count > 100:
                    score += 10
                elif count > 10:
                    score += 5

        if score > 0:
            entry["score"] = score
            new_entries.append(entry)

    print(f"Identified {len(new_entries)} new relevant entries after keyword scoring.")
    return new_entries


if __name__ == "__main__":
    main()
