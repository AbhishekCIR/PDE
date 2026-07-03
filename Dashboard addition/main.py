import os
import datetime
import logging
import pandas as pd
from pathlib import Path
import config
from pnode_locator import find_nearest_pnode
from downloader import MisoDownloader
from parser import MisoParser
from transformer import MisoTransformer
from merge_lmp import MisoLmpProcessor
from validator import MisoValidator

def setup_logging():
    """Configures centralized logging system."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / "miso_pipeline.log"
    
    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Root Logger Configuration
    root_logger = logging.getLogger("miso_pipeline")
    root_logger.setLevel(logging.INFO)
    
    # Remove existing handlers if any (to avoid duplicates)
    root_logger.handlers = []
    
    # File Handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    return root_logger

def get_dates_list(start_date: str, end_date: str) -> list:
    """Generates a list of date strings in YYYYMMDD format."""
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
    delta = datetime.timedelta(days=1)
    
    current = start
    dates = []
    while current <= end:
        dates.append(current.strftime("%Y%m%d"))
        current += delta
    return dates

def run_pipeline():
    logger = setup_logging()
    logger.info("=============================================================")
    logger.info("Starting MISO Ancillary Service and LMP Compilation Pipeline")
    logger.info("=============================================================")

    # 1. Pnode Identification Step
    pnode_name = config.PNODE_NAME
    pnode_id = "N/A"
    distance = 0.0
    zone = "Unknown"
    
    if config.USE_COORDINATES:
        logger.info(f"Identifying closest MISO Pnode for coordinates: {config.LATITUDE}, {config.LONGITUDE}")
        try:
            closest = find_nearest_pnode(config.LATITUDE, config.LONGITUDE)
            pnode_name = closest["pnode"]
            pnode_id = closest["node_id"]
            distance = closest["distance"]
            zone = closest["zone"]
            logger.info(f"Locator selected: {pnode_name} (ID: {pnode_id}, Zone: {zone}) - Distance: {distance} miles")
        except Exception as e:
            logger.error(f"Error executing Pnode locator: {e}. Falling back to default Pnode: {config.PNODE_NAME}")
            pnode_name = config.PNODE_NAME
    else:
        logger.info(f"Using manual Pnode override: {pnode_name}")
        
    # 2. Downloading Step
    logger.info(f"Triggering downloads for range: {config.START_DATE} to {config.END_DATE}")
    downloader = MisoDownloader()
    
    # Determine what files to download based on configuration flags
    download_results = downloader.execute_downloads(
        start_date=config.START_DATE,
        end_date=config.END_DATE,
        include_da_mcp=config.DOWNLOAD_DAY_AHEAD,
        include_lmp=config.MERGE_LMP
    )
    
    # 3. Processing and Compiling
    dates_list = get_dates_list(config.START_DATE, config.START_DATE if config.START_DATE == config.END_DATE else config.END_DATE)
    
    parser = MisoParser()
    transformer = MisoTransformer()
    
    # Process Real-Time MCP
    logger.info("Compiling Real-Time MCP dataset...")
    rt_mcp_long_rows = []
    for date_str in dates_list:
        filepath = config.RAW_DIR / f"{date_str}_rtmcp.csv"
        if filepath.exists():
            df_day = parser.parse_daily_mcp(filepath, pnode_name)
            df_day_long = transformer.melt_daily_data(df_day, date_str)
            if not df_day_long.empty:
                rt_mcp_long_rows.append(df_day_long)
                
    if rt_mcp_long_rows:
        df_rt_mcp_all = pd.concat(rt_mcp_long_rows, ignore_index=True)
        df_rt_mcp_ts = transformer.compute_timestamps(df_rt_mcp_all)
        df_rt_mcp_pivot = transformer.transform_dataset(df_rt_mcp_ts)
        df_rt_compiled = transformer.fill_missing_timestamps(df_rt_mcp_pivot, config.START_DATE, config.END_DATE)
    else:
        logger.error("No Real-Time MCP data was successfully compiled.")
        df_rt_compiled = pd.DataFrame()

    # Process Day-Ahead MCP (if enabled)
    df_da_compiled = pd.DataFrame()
    if config.DOWNLOAD_DAY_AHEAD:
        logger.info("Compiling Day-Ahead MCP dataset...")
        da_mcp_long_rows = []
        for date_str in dates_list:
            filepath = config.RAW_DIR / f"{date_str}_damcp.csv"
            if filepath.exists():
                df_day = parser.parse_daily_mcp(filepath, pnode_name)
                df_day_long = transformer.melt_daily_data(df_day, date_str)
                if not df_day_long.empty:
                    da_mcp_long_rows.append(df_day_long)
                    
        if da_mcp_long_rows:
            df_da_mcp_all = pd.concat(da_mcp_long_rows, ignore_index=True)
            # Filter products for DA (regulation mileage doesn't clear in DA, but let's make sure it handles it)
            df_da_mcp_ts = transformer.compute_timestamps(df_da_mcp_all)
            df_da_mcp_pivot = transformer.transform_dataset(df_da_mcp_ts)
            df_da_compiled = transformer.fill_missing_timestamps(df_da_mcp_pivot, config.START_DATE, config.END_DATE)
        else:
            logger.warning("No Day-Ahead MCP data was successfully compiled.")

    # 4. Optional LMP Merging
    if config.MERGE_LMP:
        lmp_processor = MisoLmpProcessor()
        
        # Merge Real-Time LMP
        if not df_rt_compiled.empty:
            logger.info("Merging Real-Time LMPs...")
            rt_lmp_files = [config.RAW_DIR / f"{date_str}_rtlmp.csv" for date_str in dates_list]
            df_rt_compiled = lmp_processor.merge_lmp_with_mcp(df_rt_compiled, rt_lmp_files, pnode_name)
            
        # Merge Day-Ahead LMP
        if config.DOWNLOAD_DAY_AHEAD and not df_da_compiled.empty:
            logger.info("Merging Day-Ahead LMPs...")
            da_lmp_files = [config.RAW_DIR / f"{date_str}_dalmp.csv" for date_str in dates_list]
            df_da_compiled = lmp_processor.merge_lmp_with_mcp(df_da_compiled, da_lmp_files, pnode_name)

    # 5. Data Validation Step
    logger.info("Running validation checks...")
    validator = MisoValidator()
    
    # We combine validation checks. If both markets were run, we validate both.
    # We'll run the validator on Real-Time as the primary dataset, and Day-Ahead as secondary.
    validation_results = validator.validate_dataset(df_rt_compiled, config.START_DATE, config.END_DATE, download_results)
    
    # Append Pnode Locator metadata to validation summary for reporting
    validation_results["summary"]["Pricing Node Selected"] = pnode_name
    validation_results["summary"]["Pricing Node ID"] = pnode_id
    validation_results["summary"]["Pnode Geodesic Distance (mi)"] = distance
    validation_results["summary"]["MISO Nodal Zone"] = zone
    
    # Generate the validation report spreadsheet
    validator.generate_validation_report(validation_results, config.VALIDATION_REPORT_OUT)

    # 6. Writing Output Files
    logger.info("Saving compiled optimizer datasets...")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Export CSVs
    if not df_rt_compiled.empty:
        rt_csv_path = config.OUTPUT_DIR / "optimizer_input_rt.csv"
        df_rt_compiled.to_csv(rt_csv_path, index=False)
        logger.info(f"Saved Real-Time optimizer CSV to: {rt_csv_path}")
        
    if config.DOWNLOAD_DAY_AHEAD and not df_da_compiled.empty:
        da_csv_path = config.OUTPUT_DIR / "optimizer_input_da.csv"
        df_da_compiled.to_csv(da_csv_path, index=False)
        logger.info(f"Saved Day-Ahead optimizer CSV to: {da_csv_path}")

    # Export Excel Workbook (single workbook containing both sheets for premium feel)
    try:
        with pd.ExcelWriter(config.OPTIMIZER_XLSX_OUT, engine="openpyxl") as writer:
            if not df_rt_compiled.empty:
                df_rt_compiled.to_excel(writer, sheet_name="Real-Time MCP", index=False)
            if config.DOWNLOAD_DAY_AHEAD and not df_da_compiled.empty:
                df_da_compiled.to_excel(writer, sheet_name="Day-Ahead MCP", index=False)
                
        # Also copy primary to the requested standard output file names
        if not df_rt_compiled.empty:
            df_rt_compiled.to_csv(config.OPTIMIZER_CSV_OUT, index=False)
            logger.info(f"Saved default optimizer CSV to: {config.OPTIMIZER_CSV_OUT}")
            
        logger.info(f"Saved premium optimizer workbook to: {config.OPTIMIZER_XLSX_OUT}")
        logger.info("=============================================================")
        logger.info("MISO MCP/LMP Pipeline execution completed successfully!")
        logger.info("=============================================================")
        
    except Exception as e:
        logger.error(f"Failed to save output spreadsheets: {e}")
        raise

if __name__ == "__main__":
    run_pipeline()
