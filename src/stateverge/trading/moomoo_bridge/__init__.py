"""Moomoo bridge — runs on the user's own computer next to Moomoo OpenD.

Daily plan (data/trading/plans/<date>.json) → rule checks → limit orders on the
Moomoo **paper** account (TrdEnv.SIMULATE) → rule-based exits → journal.json.

There is deliberately no real-money path here. Phase 2 (real orders) stays closed
until journal stats pass the gate and the owner explicitly authorizes it.
"""
