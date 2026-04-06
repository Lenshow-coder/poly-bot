"""CSV scraper — reads normalized odds from a CSV file."""
import csv
import logging
from datetime import datetime, timezone

from scrapers.base import BaseScraper
from scrapers.models import BookOdds, EventOdds, ScrapedOdds

logger = logging.getLogger(__name__)


class CsvScraper(BaseScraper):
    """Reads odds from a CSV with columns: timestamp, sport, sportsbook, market, team, odds.

    Sportsbook names are lowercased on read to match weight config keys.
    The 'market' column value becomes the event key in ScrapedOdds.events.
    Only rows from each market's latest timestamp batch are kept.
    Reads incrementally from the last file offset for append-only CSVs.
    """

    def __init__(self, name: str, interval: int, path: str):
        super().__init__(name, interval)
        self.path = path
        self._latest_by_market: dict[str, datetime] = {}
        self._events: dict[str, dict[str, list[BookOdds]]] = {}
        self._fieldnames: list[str] | None = None
        self._file_position: int = 0
        self._next_row_number: int = 2
        self._initialized: bool = False

    def _reset_state(self) -> None:
        self._latest_by_market = {}
        self._events = {}
        self._fieldnames = None
        self._file_position = 0
        self._next_row_number = 2
        self._initialized = False

    def _parse_and_merge_row(self, row: dict, row_number: int) -> bool:
        try:
            row_ts_raw = row["timestamp"]
            row_timestamp = datetime.strptime(
                row_ts_raw, "%m/%d/%Y %H:%M"
            ).replace(tzinfo=timezone.utc)
            market = row["market"]
            team = row["team"]
            sportsbook = row["sportsbook"].lower()
            odds = float(row["odds"])
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping CSV row {row_number}: {e}")
            return False

        market_latest = self._latest_by_market.get(market)
        if market_latest is None or row_timestamp > market_latest:
            self._latest_by_market[market] = row_timestamp
            self._events[market] = {}
        elif row_timestamp < market_latest:
            # Ignore stale rows so each scrape evaluates only the newest set per market.
            return True

        self._events.setdefault(market, {}).setdefault(team, []).append(
            BookOdds(sportsbook=sportsbook, decimal_odds=odds)
        )
        return True

    def _build_result(self) -> ScrapedOdds:
        scraped_events = {}
        for market, outcomes in self._events.items():
            scraped_events[market] = EventOdds(
                event_name=market,
                outcomes=outcomes,
                timestamp=self._latest_by_market.get(market),
            )

        return ScrapedOdds(
            timestamp=(
                max(self._latest_by_market.values())
                if self._latest_by_market
                else datetime.now(timezone.utc)
            ),
            events=scraped_events,
        )

    async def scrape(self) -> ScrapedOdds:
        skipped = 0

        try:
            f = open(self.path, encoding="utf-8", newline="")
        except FileNotFoundError:
            logger.error(f"CSV file not found: {self.path}")
            self._reset_state()
            return ScrapedOdds(
                timestamp=datetime.now(timezone.utc), events={}
            )

        with f:
            f.seek(0, 2)
            file_size = f.tell()

            full_rescan_needed = (
                (not self._initialized)
                or self._file_position > file_size
                or self._fieldnames is None
            )

            if full_rescan_needed:
                self._reset_state()
                f.seek(0)
                reader = csv.DictReader(f)
                self._fieldnames = reader.fieldnames or []
                for row in reader:
                    if not self._parse_and_merge_row(row, self._next_row_number):
                        skipped += 1
                    self._next_row_number += 1
                self._file_position = f.tell()
                self._initialized = True
            else:
                f.seek(self._file_position)
                reader = csv.DictReader(f, fieldnames=self._fieldnames)
                for row in reader:
                    if not self._parse_and_merge_row(row, self._next_row_number):
                        skipped += 1
                    self._next_row_number += 1
                self._file_position = f.tell()

        if skipped:
            logger.warning(f"Skipped {skipped} malformed rows in {self.path}")

        return self._build_result()
