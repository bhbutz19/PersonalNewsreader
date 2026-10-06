-- Public read-only access for the published newspaper edition payloads.
-- Run this in the Supabase SQL Editor once.

ALTER TABLE public.editions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public can read editions" ON public.editions;

CREATE POLICY "Public can read editions"
ON public.editions
FOR SELECT
TO anon
USING (true);

GRANT SELECT ON TABLE public.editions TO anon;
