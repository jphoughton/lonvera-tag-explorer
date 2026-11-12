-- 241: meta_breakdown_daily primary key on a fixed-size hash of dims_key instead of the raw text. A text creative-asset value (body / description asset = long ad copy) made the
-- btree key exceed the 2,704-byte limit (ProgramLimitExceeded) and killed the breakdowns job. dims_key stays as a plain unindexed column holding the full value; the new
-- generated column dims_hash = md5(dims_key) takes its place in the primary key / conflict target. Works on the partitioned table and its existing partitions; partitions
-- the code creates later inherit the column and the key. Idempotent.
ALTER TABLE meta_breakdown_daily ADD COLUMN IF NOT EXISTS dims_hash text GENERATED ALWAYS AS (md5(dims_key)) STORED;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                 WHERE c.conrelid = 'public.meta_breakdown_daily'::regclass AND c.contype = 'p' AND a.attname = 'dims_hash') THEN
    ALTER TABLE meta_breakdown_daily DROP CONSTRAINT IF EXISTS meta_breakdown_daily_pkey;
    ALTER TABLE meta_breakdown_daily ADD CONSTRAINT meta_breakdown_daily_pkey PRIMARY KEY (day, account_id, level, entity_id, breakdown_set, dims_hash);
  END IF;
END $$;

COMMENT ON COLUMN public.meta_breakdown_daily.dims_key IS 'Stable text form of the dimension values (names sorted, name=value joined with a vertical bar, percent-escaped); any length. Not indexed: the primary key uses dims_hash.';
COMMENT ON COLUMN public.meta_breakdown_daily.dims_hash IS 'md5 of dims_key (generated column), the fixed-size stand-in for dims_key in the primary key so very long values (ad copy assets) can be indexed.';
