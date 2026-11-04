-- A taxonomy line the sheet gained AFTER the ads were tagged reads 0 ads by construction, not "untested". The Tags pages show it as
-- "added after tagging". etl.load_taxonomy sets the flag on any line it inserts for a product that already has current line-map tags;
-- the three Beef Liver lines of the 'GLP-1 Hair Shedder' ICP (added 2026-11-04) are flagged here.
ALTER TABLE taxonomy_lines ADD COLUMN IF NOT EXISTS added_after_tagging boolean NOT NULL DEFAULT false;
UPDATE taxonomy_lines SET added_after_tagging = true
WHERE line_code IN ('B64', 'B65', 'B66') AND icp = 'GLP-1 Hair Shedder' AND product_id = (SELECT id FROM products WHERE slug = 'beef-liver');
