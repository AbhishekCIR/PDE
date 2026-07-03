import os
import time
import logging
import requests
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from tqdm import tqdm
import config

logger = logging.getLogger("miso_pipeline.downloader")

class MisoDownloader:
    def __init__(self, raw_dir: Path = config.RAW_DIR):
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(config.HTTP_HEADERS)
        self.download_records = []

    def _get_target_path(self, date_str: str, file_type: str) -> Path:
        """Returns the local file path for a downloaded report."""
        return self.raw_dir / f"{date_str}_{file_type}.csv"

    def _get_miso_url(self, date_str: str, file_type: str) -> str:
        """Constructs the MISO document store URL."""
        # Mapping file_type to the exact MISO report filename format
        filename_map = {
            "rtmcp": f"{date_str}_asm_rtmcp_final.csv",
            "damcp": f"{date_str}_asm_expost_damcp.csv",
            "rtlmp": f"{date_str}_rt_lmp_final.csv",
            "dalmp": f"{date_str}_da_expost_lmp.csv"
        }
        return f"{config.MISO_BASE_URL}{filename_map[file_type]}"

    def download_file(self, date_str: str, file_type: str) -> dict:
        """
        Downloads a single file from MISO with retry logic.
        
        Returns:
            dict: Download status containing 'status', 'size', and 'attempts'.
        """
        target_path = self._get_target_path(date_str, file_type)
        url = self._get_miso_url(date_str, file_type)
        
        # 1. Resume Check (Cache Check)
        if target_path.exists() and target_path.stat().st_size > 0:
            logger.debug(f"Skipped: {date_str} {file_type} (Already Exists)")
            return {
                "date": date_str,
                "file_type": file_type,
                "status": "Skipped: Already Exists",
                "size": target_path.stat().st_size,
                "attempts": 0
            }

        # 2. Download Execution with Retry Logic
        attempts = 0
        while attempts < config.MAX_RETRIES:
            attempts += 1
            try:
                logger.debug(f"Attempt {attempts}: Downloading {file_type} for {date_str} from {url}")
                response = self.session.get(url, timeout=config.DOWNLOAD_TIMEOUT)
                
                # Check status
                if response.status_code == 200:
                    # Write to file
                    target_path.write_text(response.text, encoding="utf-8")
                    logger.info(f"Downloaded: {date_str} {file_type}")
                    return {
                        "date": date_str,
                        "file_type": file_type,
                        "status": "Downloaded",
                        "size": len(response.text),
                        "attempts": attempts
                    }
                elif response.status_code == 404:
                    # 404 indicates holiday or unavailable file - skip retrying
                    logger.warning(f"Failed: 404 Not Found for {date_str} {file_type}")
                    return {
                        "date": date_str,
                        "file_type": file_type,
                        "status": "Failed: 404 Not Found",
                        "size": 0,
                        "attempts": attempts
                    }
                else:
                    logger.warning(f"HTTP {response.status_code} received for {date_str} {file_type}")
                    
            except requests.RequestException as e:
                logger.warning(f"Network error on attempt {attempts} for {date_str} {file_type}: {e}")
            
            # Apply backoff delay for retries (except on the last attempt)
            if attempts < config.MAX_RETRIES:
                sleep_time = config.BACKOFF_FACTOR * (2 ** (attempts - 1))
                logger.debug(f"Retrying in {sleep_time} seconds...")
                time.sleep(sleep_time)

        # Max retries exceeded
        logger.error(f"Failed: {date_str} {file_type} after {config.MAX_RETRIES} attempts.")
        return {
            "date": date_str,
            "file_type": file_type,
            "status": f"Failed: Max Retries Exceeded",
            "size": 0,
            "attempts": attempts
        }

    def execute_downloads(self, start_date: str, end_date: str, include_da_mcp: bool = True, include_lmp: bool = True) -> list:
        """
        Executes concurrent downloads for all reports in the specified date range.
        """
        # Generate list of dates
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        delta = datetime.timedelta(days=1)
        
        current = start
        dates = []
        while current <= end:
            dates.append(current.strftime("%Y%m%d"))
            current += delta

        tasks = []
        for date_str in dates:
            tasks.append((date_str, "rtmcp"))
            if include_da_mcp:
                tasks.append((date_str, "damcp"))
            if include_lmp:
                tasks.append((date_str, "rtlmp"))
                tasks.append((date_str, "dalmp"))

        total_tasks = len(tasks)
        logger.info(f"Preparing to download {total_tasks} files concurrently with {config.MAX_WORKERS} threads.")

        results = []
        with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
            # Submit all download tasks
            future_to_task = {
                executor.submit(self.download_file, date_str, file_type): (date_str, file_type)
                for date_str, file_type in tasks
            }
            
            # Use tqdm to show execution progress
            for future in tqdm(as_completed(future_to_task), total=total_tasks, desc="Downloading MISO Reports"):
                task = future_to_task[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"Task failed: {task[0]} {task[1]} due to exception: {e}")
                    results.append({
                        "date": task[0],
                        "file_type": task[1],
                        "status": f"Critical Error: {e}",
                        "size": 0,
                        "attempts": 1
                    })

        self.download_records = results
        self.save_download_log()
        return results

    def save_download_log(self, output_path: Path = config.DOWNLOAD_LOG_OUT):
        """
        Generates and saves a detailed download log spreadsheet.
        """
        if not self.download_records:
            logger.warning("No download records to save to log.")
            return

        # Structure download records into date rows
        df_records = pd.DataFrame(self.download_records)
        
        # Pivot the records to make it readable (one row per date)
        # Columns: Date, rtmcp_status, rtmcp_size, damcp_status, damcp_size, etc.
        try:
            pivot_status = df_records.pivot(index="date", columns="file_type", values="status")
            pivot_status.columns = [f"{col}_status" for col in pivot_status.columns]
            
            pivot_size = df_records.pivot(index="date", columns="file_type", values="size")
            pivot_size.columns = [f"{col}_size_bytes" for col in pivot_size.columns]
            
            df_log = pd.concat([pivot_status, pivot_size], axis=1).reset_index()
            
            # Sort by date
            df_log = df_log.sort_values(by="date")
            
            # Ensure output folder exists
            output_path.parent.mkdir(parents=True, exist_ok=True)
            df_log.to_excel(output_path, index=False)
            logger.info(f"Download log spreadsheet generated successfully at: {output_path}")
        except Exception as e:
            logger.error(f"Failed to generate download log spreadsheet: {e}")

if __name__ == "__main__":
    # Test download run for 3 days in 2024
    logging.basicConfig(level=logging.INFO)
    downloader = MisoDownloader()
    print("Testing MisoDownloader for 3 days...")
    downloader.execute_downloads(start_date="2024-01-01", end_date="2024-01-03", include_da_mcp=True, include_lmp=True)
