-- Event labels the news scorer gives each (article, ticker) pair (vn-portfolio I-034, news.yaml llm_labels).
-- NULL = scored before 058 (vn-portfolio falls back to title patterns); '{}' = scored, no event.
ALTER TABLE news_impact_scores ADD COLUMN IF NOT EXISTS labels TEXT[];
