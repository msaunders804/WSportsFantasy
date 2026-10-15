"""Content work: analytics, stat notes and simulations for social media and the site.

Reads from the same database as the fantasy game (through `undertow.data`) and never calls
the HockeyTech feed directly. That goes through `undertow.ingest`. Exploratory, one-off work
belongs in the top-level `analysis/` folder, which is never deployed.
"""
