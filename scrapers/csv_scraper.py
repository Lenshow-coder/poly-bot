"""CSV scraper — reads normalized odds from a CSV file."""
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

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

    def __init__(
        self,
        name: str,
        interval: int,
        path: str,
        poll_mode: str = "interval",
        poll_csv_seconds: int | None = None,
        tail_state_path: str | None = None,
    ):
        super().__init__(name, interval)
        self.path = path
        self.poll_mode = (poll_mode or "interval").strip().lower()
        if self.poll_mode == "metadata":
            self.poll_mode = "csv_change"
        if self.poll_mode not in {"interval", "csv_change"}:
            logger.warning(
                "Unknown poll_mode '%s' for CsvScraper; falling back to 'interval'",
                self.poll_mode,
            )
            self.poll_mode = "interval"
        self.poll_csv_seconds = poll_csv_seconds
        self.tail_state_path = tail_state_path
        self._poll_last_size: int = -1
        self._poll_last_mtime_ns: int = -1
        self._load_csv_poll_state_from_disk()
        self._latest_by_market: dict[str, datetime] = {}
        self._events: dict[str, dict[str, list[BookOdds]]] = {}
        self._fieldnames: list[str] | None = None
        self._file_position: int = 0
        self._next_row_number: int = 2
        self._initialized: bool = False

    def _load_csv_poll_state_from_disk(self) -> None:
        if not self.tail_state_path:
            return
        path = Path(self.tail_state_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._poll_last_size = int(data.get("last_file_size", -1))
            # Keep backward compatibility with old state files that only stored timestamp.
            self._poll_last_mtime_ns = int(data.get("last_mtime_ns", -1))
        except (OSError, ValueError, TypeError) as e:
            logger.warning("Could not load CSV poll state %s: %s", path, e)

    def use_csv_change_polling(self) -> bool:
        """Whether this scraper should trigger from CSV metadata changes."""
        return self.poll_mode == "csv_change" and self.poll_csv_seconds is not None

    def has_new_csv_data(self) -> bool:
        """True when CSV metadata changes (cheap polling trigger)."""
        p = Path(self.path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        stat = p.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        if self._poll_last_size < 0 or self._poll_last_mtime_ns < 0:
            return True
        if size != self._poll_last_size:
            return True
        if mtime_ns != self._poll_last_mtime_ns:
            return True
        return False

    def persist_csv_poll_state(self) -> None:
        p = Path(self.path)
        if p.exists():
            stat = p.stat()
            size = stat.st_size
            mtime_ns = stat.st_mtime_ns
        else:
            size = 0
            mtime_ns = -1
        self._poll_last_size = size
        self._poll_last_mtime_ns = mtime_ns
        if not self.tail_state_path:
            return
        out = Path(self.tail_state_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "last_file_size": self._poll_last_size,
                    "last_mtime_ns": self._poll_last_mtime_ns,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

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
            row_timestamp = self._parse_timestamp(row_ts_raw)
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

    @staticmethod
    def _parse_timestamp(timestamp_raw: str) -> datetime:
        # New rows are emitted as "YYYY-MM-DD HH:MM:SS"; keep legacy fallback
        # for older rows that used "M/D/YYYY HH:MM".
        for timestamp_format in ("%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"):
            try:
                return datetime.strptime(timestamp_raw, timestamp_format).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
        raise ValueError(f"Unsupported timestamp format: {timestamp_raw!r}")

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
