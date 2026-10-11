"""Stateverge 美国财经日报 — daily 3–5 min Chinese video about US markets.

Pipeline (one episode, runs in GitHub Actions):

    market.py   prices from Yahoo Finance chart API (no key), Stooq fallback
    news.py     last-24h headlines from public finance RSS feeds
    script.py   OpenAI writes the Chinese script as JSON segments; a guard
                checks every quoted % / price against the fetched data
    tts.py      OpenAI text-to-speech, one mp3 per segment
    runway.py   Runway avatar video (gwm1_avatars) lip-syncs the 3 short
                anchor segments to that audio
    visuals.py  Pillow frames: title, market board, charts, headline cards
    assemble.py ffmpeg cut: frames + audio + anchor clips + AI-label + watermark
    runner.py   queue in data/finance/requests.json → video as a GitHub release asset

The anchor is a fictional AI presenter and every episode is labelled as such.
"""
