# Stateverge 美国财经日报 v1

Daily 3–5 minute Chinese video on US markets with an AI presenter.

## Run
Add `{"status": "pending"}` to `data/finance/requests.json` and push. The
`Finance daily render` workflow:

1. pulls closes for S&P 500, Nasdaq, Dow, 10Y yield, WTI, gold, BTC, USD/CNY (Yahoo → Stooq fallback)
2. pulls the last 24h of headlines (CNBC, MarketWatch, Yahoo Finance RSS)
3. writes the script with OpenAI (3 anchor lines ≤ ~5 s + voice-over segments); a guard rejects any number
   not in the data and any advice wording, with one rewrite
4. voices every segment with OpenAI TTS
5. lip-syncs the 3 anchor lines with Runway (`POST /v1/avatar_videos`, model `gwm1_avatars`)
6. draws title / market board / charts / headline cards, burns subtitles + watermark, cuts with ffmpeg
7. attaches the mp4 to a GitHub pre-release; the entry gets `video_url`, `title`, `problems`

## Secrets
- `OPENAI_API_KEY` (already set)
- `RUNWAYML_API_SECRET` — from https://dev.runwayml.com (API credits are bought separately from the web app).
  Without it the presenter appears as a still portrait.

## Presenter
First run generates a **fictional** presenter portrait (`data/finance/anchor_portrait.jpg`) and a Runway
custom avatar (`data/finance/avatar.json`). Delete both to recreate. Every episode shows
「AI 虚拟主播 · 非真人」 on the presenter and an AI/no-advice notice at the end.

## Uploading to YouTube
Tick **"Altered or synthetic content → Yes"** when uploading — YouTube requires it for a realistic AI presenter.
