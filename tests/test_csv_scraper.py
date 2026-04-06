"""Tests for CsvScraper."""
import asyncio
import os
import tempfile
from datetime import datetime, timezone

import pytest

from scrapers.csv_scraper import CsvScraper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Synchronously run an async coroutine."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _write_csv(tmp_dir: str, content: str, filename: str = "odds.csv") -> str:
    path = os.path.join(tmp_dir, filename)
    with open(path, "w", newline="") as f:
        f.write(content)
    return path


VALID_CSV = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Toronto Maple Leafs,4.50
04/01/2026 12:00,NHL,FanDuel,Stanley Cup Winner,Toronto Maple Leafs,5.00
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Montreal Canadiens,8.00
"""


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------

def test_scrape_basic():
    """Parses a well-formed CSV into correct ScrapedOdds structure."""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    assert "Stanley Cup Winner" in result.events
    event = result.events["Stanley Cup Winner"]
    assert "Toronto Maple Leafs" in event.outcomes
    assert "Montreal Canadiens" in event.outcomes
    assert len(event.outcomes["Toronto Maple Leafs"]) == 2
    assert len(event.outcomes["Montreal Canadiens"]) == 1


def test_scrape_sportsbook_lowercased():
    """Sportsbook names are lowercased for config key matching."""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    books = [
        bo.sportsbook
        for bo in result.events["Stanley Cup Winner"].outcomes["Toronto Maple Leafs"]
    ]
    assert all(b == b.lower() for b in books)
    assert "draftkings" in books
    assert "fanduel" in books


def test_scrape_odds_values():
    """Decimal odds are correctly parsed as floats."""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    leafs = result.events["Stanley Cup Winner"].outcomes["Toronto Maple Leafs"]
    dk = next(bo for bo in leafs if bo.sportsbook == "draftkings")
    assert dk.decimal_odds == 4.50


def test_scrape_timestamp_parsed():
    """Timestamp from first row is parsed correctly."""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    assert result.timestamp.year == 2026
    assert result.timestamp.month == 4
    assert result.timestamp.day == 1
    assert result.timestamp.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Multiple events
# ---------------------------------------------------------------------------

def test_scrape_multiple_events():
    """Different market values produce separate events."""
    csv_data = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Team A,3.00
04/01/2026 12:00,NBA,DraftKings,NBA Champion,Team B,2.50
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    assert len(result.events) == 2
    assert "Stanley Cup Winner" in result.events
    assert "NBA Champion" in result.events


def test_scrape_latest_timestamp_batch_per_market():
    """Only rows from each market's most recent timestamp are returned."""
    csv_data = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 11:59,NHL,DraftKings,Stanley Cup Winner,Team A,3.00
04/01/2026 11:59,NHL,FanDuel,Stanley Cup Winner,Team B,4.00
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Team A,2.50
04/01/2026 12:00,NHL,FanDuel,Stanley Cup Winner,Team B,3.50
04/01/2026 11:58,NBA,DraftKings,NBA Champion,Team C,5.50
04/01/2026 11:59,NBA,DraftKings,NBA Champion,Team C,5.00
04/01/2026 11:59,NBA,FanDuel,NBA Champion,Team D,6.00
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    assert result.timestamp.year == 2026
    assert result.timestamp.month == 4
    assert result.timestamp.day == 1
    assert result.timestamp.hour == 12
    assert result.timestamp.minute == 0
    assert len(result.events) == 2
    assert "Stanley Cup Winner" in result.events
    assert "NBA Champion" in result.events
    # Older Stanley Cup lines should not survive.
    team_a_rows = result.events["Stanley Cup Winner"].outcomes["Team A"]
    assert len(team_a_rows) == 1
    assert team_a_rows[0].decimal_odds == 2.50
    assert (
        result.events["Stanley Cup Winner"].timestamp
        == datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    )
    # NBA keeps 11:59 rows because that's NBA's latest market timestamp.
    nba_team_c_rows = result.events["NBA Champion"].outcomes["Team C"]
    assert len(nba_team_c_rows) == 1
    assert nba_team_c_rows[0].decimal_odds == 5.00
    assert (
        result.events["NBA Champion"].timestamp
        == datetime(2026, 4, 1, 11, 59, tzinfo=timezone.utc)
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_scrape_file_not_found():
    """Missing file returns empty ScrapedOdds, not an exception."""
    scraper = CsvScraper(name="csv", interval=60, path="/nonexistent/odds.csv")
    result = _run(scraper.scrape())
    assert len(result.events) == 0
    assert result.timestamp is not None


def test_scrape_empty_file():
    """Empty CSV (headers only) returns empty events."""
    csv_data = "timestamp,sport,sportsbook,market,team,odds\n"
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    assert len(result.events) == 0


def test_scrape_malformed_rows_skipped():
    """Rows with missing columns or bad data are skipped; valid rows still parsed."""
    csv_data = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Toronto Maple Leafs,4.50
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,,BAD
04/01/2026 12:00,NHL,FanDuel,Stanley Cup Winner,Montreal Canadiens,6.00
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    event = result.events["Stanley Cup Winner"]
    assert "Toronto Maple Leafs" in event.outcomes
    assert "Montreal Canadiens" in event.outcomes


def test_scrape_non_numeric_odds_skipped():
    """Non-numeric odds value causes that row to be skipped."""
    csv_data = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Team A,abc
04/01/2026 12:00,NHL,FanDuel,Stanley Cup Winner,Team B,3.00
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    event = result.events["Stanley Cup Winner"]
    assert "Team A" not in event.outcomes
    assert "Team B" in event.outcomes


def test_scrape_missing_columns():
    """CSV missing required columns skips all rows, returns empty."""
    csv_data = """\
timestamp,sport,book_name,event,player,price
04/01/2026 12:00,NHL,DraftKings,Stanley Cup,Leafs,4.50
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = _run(scraper.scrape())

    assert len(result.events) == 0
