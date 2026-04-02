from datetime import datetime, timezone

from scrapers.base import BaseScraper
from scrapers.models import BookOdds, EventOdds, ScrapedOdds


class StubScraper(BaseScraper):
    """Returns hardcoded NHL Stanley Cup odds for pipeline testing.

    Uses canonical team names (matching Polymarket) and lowercase sportsbook
    names (matching weight config keys).
    """

    async def scrape(self) -> ScrapedOdds:
        return ScrapedOdds(
            timestamp=datetime.now(timezone.utc),
            events={
                "Stanley Cup Winner": EventOdds(
                    event_name="Stanley Cup Winner",
                    outcomes={
                        "Toronto Maple Leafs": [
                            BookOdds("bet365", 4.50),
                            BookOdds("fanduel", 4.55),
                            BookOdds("betmgm", 4.35),
                            BookOdds("draftkings", 4.40),
                        ],
                        "Florida Panthers": [
                            BookOdds("bet365", 6.00),
                            BookOdds("fanduel", 6.20),
                            BookOdds("betmgm", 5.90),
                            BookOdds("draftkings", 5.80),
                        ],
                        "Edmonton Oilers": [
                            BookOdds("bet365", 5.00),
                            BookOdds("fanduel", 5.10),
                            BookOdds("betmgm", 4.90),
                            BookOdds("draftkings", 4.95),
                        ],
                        "Winnipeg Jets": [
                            BookOdds("bet365", 8.00),
                            BookOdds("fanduel", 8.50),
                            BookOdds("betmgm", 7.50),
                            BookOdds("draftkings", 8.00),
                        ],
                        "Dallas Stars": [
                            BookOdds("bet365", 9.00),
                            BookOdds("fanduel", 9.50),
                            BookOdds("betmgm", 8.50),
                            BookOdds("draftkings", 9.00),
                        ],
                        "Colorado Avalanche": [
                            BookOdds("bet365", 10.00),
                            BookOdds("fanduel", 11.00),
                            BookOdds("betmgm", 9.50),
                            BookOdds("draftkings", 10.00),
                        ],
                    },
                )
            },
        )
