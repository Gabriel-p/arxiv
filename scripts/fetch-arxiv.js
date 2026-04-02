import { promises as fs } from 'fs';
import fetch from 'node-fetch';
import { parseStringPromise } from 'xml2js';

// Configuration
const BASE_URL = 'https://export.arxiv.org/api/query?';
const SEARCH_QUERY = 'cat:astro-ph.GA*';
const CHUNKS = 3;
const RESULTS_PER_CHUNK = 200;
const WAIT_TIME = 3000; // 3 seconds in milliseconds
const FILE_NAME = 'arxiv.json';
const N_DAYS_BACK = 90;

const KEYWORDS = [
  { term: 'open cluster', weight: 1.5 },
  { term: 'star cluster', weight: 1 },
  { term: 'stellar cluster', weight: 0.5 },
];

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function main() {
  let entriesRaw = [];

  // 1. Fetch data from arXiv with Chunking
  console.log(`Fetching ${CHUNKS * RESULTS_PER_CHUNK} total articles in ${CHUNKS} chunks...`);

  for (let i = 0; i < CHUNKS; i++) {
    const startIndex = i * RESULTS_PER_CHUNK;
    console.log(`Requesting results ${startIndex} to ${startIndex + RESULTS_PER_CHUNK}...`);

    const query = `search_query=${SEARCH_QUERY}&sortBy=submittedDate&sortOrder=descending&start=${startIndex}&max_results=${RESULTS_PER_CHUNK}`;

    try {
      const res = await fetch(BASE_URL + query);
      if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
      
      const xml = await res.text();
      const obj = await parseStringPromise(xml, { explicitArray: false });
      
      // Ensure entries is an array even if 0 or 1 result is returned
      const batchEntries = obj.feed?.entry ? (Array.isArray(obj.feed.entry) ? obj.feed.entry : [obj.feed.entry]) : [];
      entriesRaw.push(...batchEntries);

    } catch (e) {
      console.error(`Error during chunk ${i}: ${e.message}`);
      break;
    }

    if (i < CHUNKS - 1) {
      console.log(`Sleeping for ${WAIT_TIME / 1000} seconds to respect API limits...`);
      await sleep(WAIT_TIME);
    }
  }

  // 2. Load existing processed entries
  let existingEntries = [];
  try {
    const data = await fs.readFile(FILE_NAME, 'utf-8');
    const parsed = JSON.parse(data);
    existingEntries = parsed.entries || [];
  } catch (err) {
    existingEntries = [];
  }

  // 3. Calculate date N days back
  const dateNDaysBack = new Date();
  dateNDaysBack.setDate(dateNDaysBack.getDate() - N_DAYS_BACK);
  const dateThresholdStr = dateNDaysBack.toISOString().split('T')[0];

  // 4. Filter out old existing entries
  existingEntries = existingEntries.filter(e => (e.published || "") >= dateThresholdStr);

  // 5. Filter and score new entries
  const newEntries = entriesRaw
    .filter(entry => (entry.published || "") >= dateThresholdStr)
    .map(entry => {
      let title = (entry.title || "").toLowerCase().replace(/\n/g, ' ');
      let summary = (entry.summary || "").toLowerCase().replace(/\n/g, ' ');

      let score = 0;
      // Existing Keyword logic
      KEYWORDS.forEach(({ term, weight }) => {
        const titleCount = (title.match(new RegExp(term, 'gi')) || []).length;
        const summaryCount = (summary.match(new RegExp(term, 'gi')) || []).length;
        score += (titleCount * weight * 3) + (summaryCount * weight);
      });

      // Updated Numeric pattern: handles "new", "star", and singular "cluster"
      const numericPattern = /(\d{2,})\s+(?:(?:new\s+)?(?:open|star)\s+)?clusters?/gi;
      
      const targets = [
        { text: title, multiplier: 1.5 }, // Title hits weighted higher
        { text: summary, multiplier: 1 }
      ];
      targets.forEach(({ text, multiplier }) => {
        if (!text) return;
        let match;
        // Reset lastIndex because of the 'g' flag if reusing the regex
        numericPattern.lastIndex = 0; 
        while ((match = numericPattern.exec(text)) !== null) {
          const count = parseInt(match[1], 10);
          let points = 0;
          if (count > 100) points = 10;
          else if (count > 10) points = 5;
          score += points * multiplier;
        }
      });


      return { ...entry, score };
    })
    .filter(entry => entry.score > 0);

  console.log(`Identified ${newEntries.length} new relevant entries after scoring.`);

  // 6. Merge and Deduplicate
  const allEntriesMap = new Map();
  existingEntries.forEach(e => allEntriesMap.set(e.id, e));
  newEntries.forEach(e => allEntriesMap.set(e.id, e));

  // 7. Sort and Format
  let filteredEntries = Array.from(allEntriesMap.values())
    .filter(e => (e.title || "").toLowerCase() !== 'no articles found');

  filteredEntries.sort((a, b) => (b.published || "").localeCompare(a.published || ""));

  const entriesToSave = filteredEntries.length > 0 ? filteredEntries : [{
    title: 'No articles found',
    id: '#',
    author: [{ name: ' ' }],
    updated: new Date().toISOString().split('T')[0],
    score: 0,
    summary: 'No articles matching the filters were found.',
  }];

  const outputData = {
    fetched_at: new Date().toISOString(),
    entries: entriesToSave,
  };

  // 8. Write to file
  await fs.writeFile(FILE_NAME, JSON.stringify(outputData, null, 2), 'utf-8');
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
