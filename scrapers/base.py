from abc import ABC, abstractmethod

from scrapers.models import ScrapedOdds


class BaseScraper(ABC):
    def __init__(self, name: str, interval: int):
        self.name = name
        self.interval = interval  # seconds between runs

    @abstractmethod
    async def scrape(self) -> ScrapedOdds:
        """Run the scraper. Returns ScrapedOdds with all events this scraper covers."""

    def get_name(self) -> str:
        return self.name
