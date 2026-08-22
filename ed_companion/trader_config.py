"""Central configuration for material-trader type discovery."""

from pathlib import Path
import os


SPANSH_STATION_SEARCH_URL = "https://www.spansh.co.uk/api/stations/search"
SPANSH_TIMEOUT_SECONDS = 12
SPANSH_MINIMUM_AGE_HOURS = 24
TRADER_TYPE_STALE_DAYS = {
    "confirmed": None,
    "external": 30,
    "heuristic": 7,
}
HEURISTIC_TRADER_WARNING = "Ungeprüft – vor Ort bestätigen"


def trader_type_cache_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "EDEngineeringCompanion" / "trader_type_cache.json"
