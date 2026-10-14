"""Stateverge 国内要闻 — fast single-event posts on China's economy, tech,
policy and livelihood news for the X channel (Chinese).

Scope is deliberately non-political: monetary policy, property, trade data,
companies/AI, regulation affecting daily life, accidents with official
reports. Political topics are excluded by an explicit blocklist and by the
editor prompt, and nothing is published unless at least two independent
outlets report the same event.

    sources.py  Google News RSS (zh-CN) topic queries → headlines with outlet names
    editor.py   OpenAI groups headlines into events and drafts one post each
    guard.py    ≥2 outlets, allowed category, political/sensational blocklist, length
    runner.py   queue in data/cnbrief/requests.json; veto-window scheduling; X posting
"""
