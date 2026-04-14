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

def _write_csv(tmp_dir: str, content: str, filename: str = "odds.csv") -> str:
    path = os.path.join(tmp_dir, filename)
    with open(path, "w", newline="") as f:
        f.write(content)
    return path


def _append_csv(path: str, content: str) -> None:
    with open(path, "a", newline="") as f:
        f.write(content)


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
        result = asyncio.run(scraper.scrape())

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
        result = asyncio.run(scraper.scrape())

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
        result = asyncio.run(scraper.scrape())

    leafs = result.events["Stanley Cup Winner"].outcomes["Toronto Maple Leafs"]
    dk = next(bo for bo in leafs if bo.sportsbook == "draftkings")
    assert dk.decimal_odds == 4.50


def test_scrape_timestamp_parsed():
    """Timestamp from first row is parsed correctly."""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = asyncio.run(scraper.scrape())

    assert result.timestamp.year == 2026
    assert result.timestamp.month == 4
    assert result.timestamp.day == 1
    assert result.timestamp.tzinfo == timezone.utc


def test_scrape_prefers_iso_timestamp_and_falls_back_to_legacy():
    """Supports both timestamp formats with ISO as default parser."""
    csv_data = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Team A,4.00
2026-04-01 12:01:02,NHL,FanDuel,Stanley Cup Winner,Team B,5.00
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = asyncio.run(scraper.scrape())

    assert result.events["Stanley Cup Winner"].timestamp == datetime(
        2026, 4, 1, 12, 1, 2, tzinfo=timezone.utc
    )
    assert "Team B" in result.events["Stanley Cup Winner"].outcomes


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
        result = asyncio.run(scraper.scrape())

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
        result = asyncio.run(scraper.scrape())

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


def test_scrape_incremental_appends_only_new_rows():
    """Second scrape consumes appended rows and preserves prior market state."""
    initial = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Team A,2.50
04/01/2026 12:00,NHL,FanDuel,Stanley Cup Winner,Team B,3.50
04/01/2026 11:59,NBA,DraftKings,NBA Champion,Team C,5.00
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, initial)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        first = asyncio.run(scraper.scrape())

        _append_csv(
            path,
            "04/01/2026 11:58,NBA,FanDuel,NBA Champion,Team C,5.20\n"
            "04/01/2026 12:01,NBA,FanDuel,NBA Champion,Team C,4.80\n",
        )
        second = asyncio.run(scraper.scrape())

    assert first.events["Stanley Cup Winner"].timestamp == datetime(
        2026, 4, 1, 12, 0, tzinfo=timezone.utc
    )
    assert first.events["NBA Champion"].timestamp == datetime(
        2026, 4, 1, 11, 59, tzinfo=timezone.utc
    )

    # NHL unchanged because no NHL append.
    assert second.events["Stanley Cup Winner"].timestamp == datetime(
        2026, 4, 1, 12, 0, tzinfo=timezone.utc
    )
    # NBA advances to appended latest.
    assert second.events["NBA Champion"].timestamp == datetime(
        2026, 4, 1, 12, 1, tzinfo=timezone.utc
    )
    assert second.events["NBA Champion"].outcomes["Team C"][0].decimal_odds == 4.80


def test_scrape_resets_after_file_truncation():
    """If file is replaced/truncated, scraper resets incremental state."""
    initial = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:00,NHL,DraftKings,Stanley Cup Winner,Team A,2.50
"""
    replacement = """\
timestamp,sport,sportsbook,market,team,odds
04/01/2026 12:05,NBA,DraftKings,NBA Champion,Team C,4.20
"""
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, initial)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        asyncio.run(scraper.scrape())
        _write_csv(d, replacement)
        result = asyncio.run(scraper.scrape())

    assert "Stanley Cup Winner" not in result.events
    assert "NBA Champion" in result.events
    assert result.events["NBA Champion"].timestamp == datetime(
        2026, 4, 1, 12, 5, tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# Poll / tail trigger
# ---------------------------------------------------------------------------

def test_csv_poll_state_detects_append():
    with tempfile.TemporaryDirectory() as d:
        state_path = os.path.join(d, "state.json")
        path = _write_csv(d, VALID_CSV)
        scraper = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_csv_seconds=5,
            tail_state_path=state_path,
        )
        assert scraper.has_new_csv_data() is True
        scraper.persist_csv_poll_state()
        assert scraper.has_new_csv_data() is False
        _append_csv(
            path,
            "04/02/2026 10:00,NHL,DraftKings,Stanley Cup Winner,Boston Bruins,6.00\n",
        )
        assert scraper.has_new_csv_data() is True


def test_csv_poll_state_survives_restart():
    with tempfile.TemporaryDirectory() as d:
        state_path = os.path.join(d, "state.json")
        path = _write_csv(d, VALID_CSV)
        s1 = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_csv_seconds=5,
            tail_state_path=state_path,
        )
        s1.persist_csv_poll_state()
        s2 = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_csv_seconds=5,
            tail_state_path=state_path,
        )
        assert s2.has_new_csv_data() is False


def test_csv_poll_state_back_compat_old_timestamp_state():
    with tempfile.TemporaryDirectory() as d:
        state_path = os.path.join(d, "state.json")
        path = _write_csv(d, VALID_CSV)
        size = os.path.getsize(path)
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(
                '{"last_file_size": %d, "last_timestamp_raw": "04/01/2026 12:00"}'
                % size
            )
        s = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_csv_seconds=5,
            tail_state_path=state_path,
        )
        # Old state lacks mtime metadata, so first check should resync.
        assert s.has_new_csv_data() is True
        s.persist_csv_poll_state()
        assert s.has_new_csv_data() is False


def test_csv_poll_mode_interval_ignores_csv_change_trigger_path():
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        s = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_mode="interval",
            poll_csv_seconds=5,
        )
        assert s.use_csv_change_polling() is False


def test_csv_poll_mode_csv_change_requires_poll_csv_seconds():
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        s = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_mode="csv_change",
            poll_csv_seconds=None,
        )
        assert s.use_csv_change_polling() is False


def test_csv_poll_mode_csv_change_enabled_with_poll_csv_seconds():
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, VALID_CSV)
        s = CsvScraper(
            name="csv",
            interval=60,
            path=path,
            poll_mode="csv_change",
            poll_csv_seconds=5,
        )
        assert s.use_csv_change_polling() is True


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_scrape_file_not_found():
    """Missing file returns empty ScrapedOdds, not an exception."""
    scraper = CsvScraper(name="csv", interval=60, path="/nonexistent/odds.csv")
    result = asyncio.run(scraper.scrape())
    assert len(result.events) == 0
    assert result.timestamp is not None


def test_scrape_empty_file():
    """Empty CSV (headers only) returns empty events."""
    csv_data = "timestamp,sport,sportsbook,market,team,odds\n"
    with tempfile.TemporaryDirectory() as d:
        path = _write_csv(d, csv_data)
        scraper = CsvScraper(name="csv", interval=60, path=path)
        result = asyncio.run(scraper.scrape())

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
        result = asyncio.run(scraper.scrape())

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
        result = asyncio.run(scraper.scrape())

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
        result = asyncio.run(scraper.scrape())

    assert len(result.events) == 0
