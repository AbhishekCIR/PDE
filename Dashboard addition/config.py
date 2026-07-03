import os
from pathlib import Path

# ==========================================
# 1. User Inputs & Coordinates
# ==========================================
# Choose location by setting either coordinates or a specific Pnode name.
# To use coordinates, set USE_COORDINATES = True. To use a Pnode directly, set USE_COORDINATES = False.
USE_COORDINATES = True

# Project coordinates
LATITUDE = 39.534248
LONGITUDE = -87.976314

# Manual Pnode override name (used if USE_COORDINATES is False or as an override)
PNODE_NAME = "ALTW.AMESWIND"

# Date Range for compilation (standard date format: YYYY-MM-DD)
START_DATE = "2025-01-01"
END_DATE = "2025-12-31"

# Flag to download and compile Day-Ahead MCP data along with Real-Time MCP data
DOWNLOAD_DAY_AHEAD = True

# Flag to merge LMP prices into final dataset
MERGE_LMP = True

# ==========================================
# 2. File & Directory System
# ==========================================
BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
LOG_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"

# Local pricing node coordinate database path
PNODE_DB_PATH = DATA_DIR / "miso_pnodes.csv"

# Output filenames
OPTIMIZER_XLSX_OUT = OUTPUT_DIR / "optimizer_input.xlsx"
OPTIMIZER_CSV_OUT = OUTPUT_DIR / "optimizer_input.csv"
DOWNLOAD_LOG_OUT = OUTPUT_DIR / "download_log.xlsx"
VALIDATION_REPORT_OUT = OUTPUT_DIR / "validation_report.xlsx"

# Ensure all directories exist
for directory in [RAW_DIR, PROCESSED_DIR, LOG_DIR, OUTPUT_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

# ==========================================
# 3. Downloader Settings
# ==========================================
MISO_BASE_URL = "https://docs.misoenergy.org/marketreports/"
MAX_WORKERS = 10         # Number of concurrent download threads
DOWNLOAD_TIMEOUT = 15    # Connection timeout in seconds
MAX_RETRIES = 5          # Number of retries for failed downloads
BACKOFF_FACTOR = 1.5     # Backoff delay multiplier: time = backoff * (2 ** retry)

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==========================================
# 4. Processing & Extraction Schema
# ==========================================
# Ancillary services products to extract from files
REQUIRED_MCPS = ["GENREGMCP", "REGMILEAGEMCP", "GENSPINMCP", "GENSUPPMCP"]

# Column rename mapping
RENAME_SCHEMA = {
    "GENREGMCP": "REG_CAP",
    "REGMILEAGEMCP": "REG_MIL",
    "GENSPINMCP": "SPIN",
    "GENSUPPMCP": "SUPP"
}

# Timezones
TIMEZONE_EST = "Etc/GMT+5"            # EST is UTC-5 all year (no DST shifts)
TIMEZONE_EPT = "America/New_York"     # Eastern Prevailing Time (handles standard/daylight transition)
