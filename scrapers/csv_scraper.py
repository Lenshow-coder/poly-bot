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
    """

    def __init__(self, name: str, interval: int, path: str):
        super().__init__(name, interval)
        self.path = path

    async def scrape(self) -> ScrapedOdds:
        events: dict[str, dict[str, list[BookOdds]]] = {}
        latest_by_market: dict[str, datetime] = {}
        skipped = 0

        try:
            f = open(self.path, newline="")
        except FileNotFoundError:
            logger.error(f"CSV file not found: {self.path}")
            return ScrapedOdds(
                timestamp=datetime.now(timezone.utc), events={}
            )

        with f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=2):
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
                    skipped += 1
                    logger.warning(f"Skipping CSV row {i}: {e}")
                    continue

                market_latest = latest_by_market.get(market)
                if market_latest is None or row_timestamp > market_latest:
                    latest_by_market[market] = row_timestamp
                    events[market] = {}
                elif row_timestamp < market_latest:
                    # Ignore stale rows so each scrape evaluates only the newest set per market.
                    continue

                events.setdefault(market, {}).setdefault(team, []).append(
                    BookOdds(sportsbook=sportsbook, decimal_odds=odds)
                )

        if skipped:
            logger.warning(f"Skipped {skipped} malformed rows in {self.path}")

        scraped_events = {}
        for market, outcomes in events.items():
            scraped_events[market] = EventOdds(
                event_name=market,
                outcomes=outcomes,
                timestamp=latest_by_market.get(market),
            )

        return ScrapedOdds(
            timestamp=(
                max(latest_by_market.values())
                if latest_by_market
                else datetime.now(timezone.utc)
            ),
            events=scraped_events,
        )
