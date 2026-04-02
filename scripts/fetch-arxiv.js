import { promises as fs } from 'fs';
import fetch from 'node-fetch';
import { parseStringPromise } from 'xml2js';

const ARXIV_URL = 'https://export.arxiv.org/api/query?search_query=cat:astro-ph.GA*&sortBy=submittedDate&sortOrder=descending&max_results=200';

// Keywords to filter and score entries
const keywords = [
  { term: 'open cluster', weight: 1.5 },
  { term: 'star cluster', weight: 1 },
  { term: 'stellar cluster', weight: 0.5 },
];

const NDaysBack = 60;


async function main() {
  // Load existing entries from arxiv.json, assume it exists
  let existingEntries = [];
  const data = await fs.readFile('arxiv.json', 'utf-8');
  existingEntries = JSON.parse(data);

  // Calculate the date N days back
  const dateNDaysBack = new Date();
  dateNDaysBack.setDate(dateNDaysBack.getDate() - NDaysBack);
  const dateNDaysBackStr = dateNDaysBack.toISOString().split('T')[0];

  // Filter out entries older than N days from the existing data
  existingEntries = existingEntries.entries.filter(entry => entry.published >= dateNDaysBackStr);

  // Fetch XML data from arXiv
  const fetchTimestamp = new Date().toISOString(); // Capture the fetch timestamp
  const res = await fetch(ARXIV_URL);
  const xml = await res.text();
  const obj = await parseStringPromise(xml, { explicitArray: false });
  const entries = Array.isArray(obj.feed.entry) ? obj.feed.entry : [obj.feed.entry];

  // Filter and score new entries
  const newEntries = entries
    .filter(entry => entry.published >= dateNDaysBackStr) // Include only recent entries
    .map(entry => {
      let title = entry.title.toLowerCase().replace(/\n/g, ' ');
      let summary = entry.summary.toLowerCase().replace(/\n/g, ' ');

      // Calculate the score
      let score = 0;
      keywords.forEach(({ term, weight }) => {
        const titleCount = (title.match(new RegExp(term, 'g')) || []).length;
        const summaryCount = (summary.match(new RegExp(term, 'g')) || []).length;
        score += titleCount * weight * 3 + summaryCount * weight;
      });

      // Detect patterns like "sample of 500 open clusters" or "120 clusters"
      const numericPattern = /(\d{2,})\s+(open\s+)?clusters/g;
      let match;
      while ((match = numericPattern.exec(summary)) !== null) {
          const count = parseInt(match[1]);
          if (count > 10) score += 5;  // Flat bonus for multi-object studies
          if (count > 100) score += 10; // Extra bonus for large catalogs
      }

      return { ...entry, score };
    })
    .filter(entry => entry.score > 0); // Exclude entries with score=0

  // Merge and deduplicate entries
  const allEntries = [...existingEntries, ...newEntries];
  const uniqueEntries = Array.from(new Map(allEntries.map(entry => [entry.id, entry])).values());

  // Remove entries with the title 'No articles found'
  const filteredEntries = uniqueEntries.filter(entry => entry.title.toLowerCase() !== 'no articles found');

  // Sort entries by published date (descending)
  filteredEntries.sort((a, b) => new Date(b.published) - new Date(a.published));

  // If no entries are to be saved, save a placeholder entry
  const entriesToSave = filteredEntries.length > 0
    ? filteredEntries
    : [{
        title: 'No articles found',
        id: '#',
        author: [{ name: ' ' }],
        updated: new Date().toISOString().split('T')[0],
        score: 0,
        summary: 'No articles matching the filters were found in the current submissions.',
      }];

  // Add the fetch timestamp to the JSON object
  const outputData = {
    fetched_at: fetchTimestamp,
    entries: entriesToSave,
  };

  // Save the updated entries to arxiv.json
  await fs.writeFile(
    'arxiv.json',
    JSON.stringify(outputData, null, 2),
    'utf-8'
  );
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
