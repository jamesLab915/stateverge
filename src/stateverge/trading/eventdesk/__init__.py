"""Event desk — phase 1 of the event-driven options playbook (signals only).

    events.py    catalog of tradable US macro releases (tier, FRED series, surprise size)
    fred.py      release dates + actual values from FRED (free API key)
    market.py    futures / VIX / premarket quotes (Yahoo chart API)
    report.py    war report: surprise → direction → underlyings → strike, sizing
    confirm.py   open confirmation: futures, premarket move, VIX all agree
    journal.py   trade journal + win rate / payoff ratio
    runner.py    queue in data/trading/requests.json (GitHub Actions)

Nothing in this package can place or cancel an order.
"""
