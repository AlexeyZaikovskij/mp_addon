"""Settings: config.env next to the project root, overridden by environment variables."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


@dataclass(frozen=True)
class Settings:
    mp_api_url: str
    main_server_name: str
    default_time_zone: str
    host: str
    port: int
    session_idle_minutes: int
    default_lang: str
    csv_delimiter: str
    outage_min_priority: int
    degraded_min_priority: int
    data_dir: Path
    pdf_max_rows: int


def load_settings() -> Settings:
    file_values = _read_env_file(ROOT / "config.env")

    def get(key: str, default: str) -> str:
        return os.environ.get(key) or file_values.get(key) or default

    return Settings(
        mp_api_url=get("MP_API_URL", "http://localhost:40000").rstrip("/"),
        main_server_name=get("MAIN_SERVER_NAME", "Main"),
        default_time_zone=get("DEFAULT_TIME_ZONE", "UTC"),
        host=get("HOST", "0.0.0.0"),
        port=int(get("PORT", "8090")),
        session_idle_minutes=int(get("SESSION_IDLE_MINUTES", "480")),
        default_lang=get("DEFAULT_LANG", "en"),
        outage_min_priority=int(get("OUTAGE_MIN_PRIORITY", "400")),
        degraded_min_priority=int(get("DEGRADED_MIN_PRIORITY", "200")),
        pdf_max_rows=int(get("PDF_MAX_ROWS", "2000")),
        data_dir=Path(get("DATA_DIR", str(ROOT / "data"))),
        csv_delimiter={"tab": "\t", "comma": ","}.get(get("CSV_DELIMITER", ";"), get("CSV_DELIMITER", ";")),
    )


settings = load_settings()
