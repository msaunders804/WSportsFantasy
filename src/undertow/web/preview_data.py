"""Sample data for the /preview mockups. Invented: the player names are placeholders, the points
and records are made up, and the pages say so. Replaced by real data in later phases."""

LEAGUE = {"name": "League One", "week": 4, "weeks": 18, "range": "Dec 28 to Jan 3"}


def _p(name, pos, team, status="yet", line="", pts=None, games=2, next_game="Sat 7:00 PM"):
    return {
        "name": name,
        "pos": pos,
        "team": team,
        "status": status,  # yet | live | final
        "line": line,
        "pts": pts,
        "games": games,
        "next": next_game,
    }


MY_TEAM = {"name": "Tidebreakers", "owner": "You", "record": "2-1", "score": 38.4}
OPP_TEAM = {"name": "Rink Rats", "owner": "Jordan", "record": "1-2", "score": 31.7}

MY_LINEUP = [
    ("F", _p("Avery Lindqvist", "F", "BOS", "final", "2G 1A 4 SOG", 11.3, 1, "Sat 7:00 PM")),
    ("F", _p("Marnie Castellan", "F", "TOR", "live", "1A 2 SOG · 2nd 8:14", 4.1, 2, "Live now")),
    ("F", _p("Priya Okafor", "F", "MTL", "yet", "Today 7:00 PM", None, 1, "Today 7:00 PM")),
    ("D", _p("Sasha Brandt", "D", "MIN", "final", "1A 3 BLK 2 SOG", 6.9, 2, "Tue 7:00 PM")),
    ("D", _p("Noor Halvorsen", "D", "SEA", "yet", "Tomorrow 3:00 PM", None, 2, "Tomorrow 3:00 PM")),
    ("UTIL", _p("Delaney Ruiz", "F", "NY", "final", "1G 5 SOG", 7.0, 2, "Wed 7:00 PM")),
    ("G", _p("Tove Marchetti", "G", "OTT", "final", "W · 28 SV · 1 GA", 9.1, 1, "Sun 1:00 PM")),
]
OPP_LINEUP = [
    ("F", _p("Imogen Valcourt", "F", "MIN", "final", "1G 1A 3 SOG", 8.6)),
    ("F", _p("Keiko Draper", "F", "VAN", "yet", "Today 9:00 PM", None, 1)),
    ("F", _p("Bianca Ferreira", "F", "SJ", "live", "2 SOG · 2nd 8:14", 1.2)),
    ("D", _p("Lena Tikkanen", "D", "DET", "final", "2A 1 BLK", 5.7)),
    ("D", _p("Rosalind Achebe", "D", "HAM", "yet", "Sun 1:00 PM", None, 1)),
    ("UTIL", _p("Odette Marsh", "F", "VGS", "final", "1A 2 SOG", 3.9)),
    ("G", _p("Kiera Lindahl", "G", "BOS", "final", "L · 24 SV · 3 GA", 2.2, 1)),
]

BENCH = [
    _p("Hana Whitlock", "F", "OTT", "yet", "", None, 2, "Sun 1:00 PM"),
    _p("Greta Solheim", "D", "TOR", "yet", "", None, 1, "Tue 7:00 PM"),
    _p("Mireille Dufort", "F", "MTL", "yet", "", None, 1, "Today 7:00 PM"),
    _p("Esme Navarro", "G", "NY", "yet", "", None, 1, "Wed 7:00 PM"),
]
IR = [_p("Colette Aubin", "D", "BOS", "yet", "Injured", None, 0, "")]

PLAYERS = [
    # name, pos, team, fp_per_game, season_fp, owned %, available
    ("Avery Lindqvist", "F", "BOS", 8.4, 92.4, 100, False),
    ("Imogen Valcourt", "F", "MIN", 7.9, 86.9, 100, False),
    ("Tove Marchetti", "G", "OTT", 7.6, 83.6, 100, False),
    ("Marnie Castellan", "F", "TOR", 7.1, 78.1, 100, False),
    ("Sasha Brandt", "D", "MIN", 6.4, 70.4, 90, False),
    ("Juno Bellamy", "F", "HAM", 6.2, 68.2, 40, True),
    ("Rosalind Achebe", "D", "HAM", 5.8, 63.8, 70, False),
    ("Wren Castellanos", "G", "SEA", 5.6, 61.6, 30, True),
    ("Delaney Ruiz", "F", "NY", 5.5, 60.5, 80, False),
    ("Ingrid Sorensen", "D", "VAN", 5.1, 56.1, 20, True),
    ("Odette Marsh", "F", "VGS", 4.9, 53.9, 60, False),
    ("Thea Rinaldi", "F", "SJ", 4.6, 50.6, 10, True),
]

STANDINGS = [
    ("Rink Rats", "Jordan", "3-0", 118.2),
    ("Tidebreakers", "You", "2-1", 109.4),
    ("Blue Line Club", "Sam", "2-1", 104.8),
    ("Neutral Zone", "Alex", "2-1", 101.3),
    ("Power Play Pals", "Riley", "2-1", 99.0),
    ("Five Hole", "Casey", "1-2", 96.5),
    ("Top Shelf", "Morgan", "1-2", 93.1),
    ("Icing Illusions", "Taylor", "1-2", 90.7),
    ("Zamboni Dreams", "Quinn", "0-3", 84.2),
    ("Backcheckers", "Drew", "0-3", 80.6),
]
SCHEDULE = [
    ("Tidebreakers", 38.4, "Rink Rats", 31.7, "live"),
    ("Blue Line Club", 29.0, "Neutral Zone", 33.5, "live"),
    ("Power Play Pals", 22.8, "Five Hole", 19.4, "live"),
    ("Top Shelf", 17.3, "Icing Illusions", 20.1, "live"),
    ("Zamboni Dreams", 12.0, "Backcheckers", 14.6, "live"),
]
ACTIVITY = [
    ("Today 9:12 AM", "Jordan added Keiko Draper (F) and dropped Wynn Teague (F)."),
    ("Today 8:40 AM", "Sam claimed Ingrid Sorensen (D) off waivers."),
    ("Yesterday", "Taylor and Casey proposed a trade. Commissioner review open."),
    ("Yesterday", "You moved Priya Okafor (F) into your lineup."),
]
