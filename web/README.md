# Mobile front-end draft

This folder contains the first phone-first UI draft for Personal Newsreader.

Design direction: **Concept 3** — a minimalist newspaper aesthetic with warm paper tones, serif headlines, restrained olive accents, and thin print-style rules.

## Preview locally

From the repository root:

```bash
python -m http.server 8080 -d web
```

Then open `http://localhost:8080` on a desktop or phone browser.

## Current state

- Static demo data in `app.js`
- Responsive Galaxy-friendly layout
- Sticky masthead and horizontal section navigation
- Lead story, morning brief, section grid, and mobile bottom navigation
- Dark-mode fallback included
- No production API connection yet

The next step is to replace the demo object with the latest edition payload from Supabase through a small read-only API.
