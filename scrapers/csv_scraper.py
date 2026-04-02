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
    """

    def __init__(self, name: str, interval: int, path: str):
        super().__init__(name, interval)
        self.path = path

    async def scrape(self) -> ScrapedOdds:
        events: dict[str, dict[str, list[BookOdds]]] = {}
        timestamp = None
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
                    market = row["market"]
                    team = row["team"]
                    sportsbook = row["sportsbook"].lower()
                    odds = float(row["odds"])
                except (KeyError, ValueError, TypeError) as e:
                    skipped += 1
                    logger.warning(f"Skipping CSV row {i}: {e}")
                    continue

                if timestamp is None and row.get("timestamp"):
                    try:
                        timestamp = datetime.strptime(
                            row["timestamp"], "%m/%d/%Y %H:%M"
                        ).replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass

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
            )

        return ScrapedOdds(
            timestamp=timestamp or datetime.now(timezone.utc),
            events=scraped_events,
        )
