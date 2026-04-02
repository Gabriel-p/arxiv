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
CACHE_FILE = "arxiv_cache.xml"

KEYWORDS = [
    {"term": "open cluster", "weight": 1.5},
    {"term": "star cluster", "weight": 1},
    {"term": "stellar cluster", "weight": 0.5},
]
N_DAYS_BACK = 30
FILE_NAME = "arxiv_py.json"


def main():
    # 1. Fetch data from arXiv with Chunking and Caching
    entries_raw = []
    if os.path.exists(CACHE_FILE):
        print(f"Loading data from local cache: {CACHE_FILE}")
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            xml_content = f.read()
            # If using cache, we parse the single cached file
            obj = xmltodict.parse(xml_content)
            entries_raw = obj.get("feed", {}).get("entry", [])
    else:
        print(
            f"Fetching {CHUNKS * RESULTS_PER_CHUNK} total articles in {CHUNKS} chunks..."
        )
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
                # For caching all batches
                all_fetched_entries.extend(batch_entries)

            except Exception as e:
                print(f"Error during chunk {i}: {e}")
                break

            if i < CHUNKS - 1:
                print(f"Sleeping for {WAIT_TIME} seconds to respect API limits...")
                time.sleep(WAIT_TIME)

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

    # Parse and print the oldest entry from the total pool
    if entries_raw:
        dates = [e.get("published") for e in entries_raw if e.get("published")]
        if dates:
            print(f"Total entries fetched: {len(entries_raw)}")
            print(f"Oldest entry in this pool: {min(dates)}")

    # 2. Load existing entries
    try:
        with open(FILE_NAME, "r", encoding="utf-8") as f:
            data = json.load(f)
            existing_entries = data.get("entries", [])
    except (FileNotFoundError, json.JSONDecodeError):
        existing_entries = []

    # 3. Calculate date N days back
    date_n_days_back = datetime.now() - timedelta(days=N_DAYS_BACK)
    date_threshold_str = date_n_days_back.strftime("%Y-%m-%d")

    # 4. Filter out old existing entries
    existing_entries = [
        e for e in existing_entries if e.get("published", "") >= date_threshold_str
    ]

    # 5. Filter and score new entries
    new_entries = []
    for entry in entries_raw:
        published = entry.get("published", "")
        if published < date_threshold_str:
            continue

        title = entry.get("title", "").lower().replace("\n", " ")
        summary = entry.get("summary", "").lower().replace("\n", " ")

        score = 0.0
        # Keyword scoring
        for kw in KEYWORDS:
            term = kw["term"]
            weight = kw["weight"]

            title_count = len(re.findall(re.escape(term), title))
            summary_count = len(re.findall(re.escape(term), summary))

            score += (title_count * weight * 3) + (summary_count * weight)

        # Numeric pattern detection (e.g., "500 open clusters")
        numeric_pattern = r"(\d{2,})\s+(?:(?:new\s+)?(?:open|star)\s+)?clusters?"
        for txt in (title, summary):
            matches = re.findall(numeric_pattern, txt)
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

    # 6. Merge and Deduplicate
    all_entries_map = {e["id"]: e for e in existing_entries}
    for e in new_entries:
        all_entries_map[e["id"]] = e

    unique_entries = list(all_entries_map.values())

    # 7. Filter out placeholder and Sort
    filtered_entries = [
        e for e in unique_entries if e.get("title", "").lower() != "no articles found"
    ]
    filtered_entries.sort(key=lambda x: x.get("published", ""), reverse=True)

    # 8. Prepare Save Data
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

    # 9. Write to file
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
