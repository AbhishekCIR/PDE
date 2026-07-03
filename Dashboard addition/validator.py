import logging
from pathlib import Path
import pandas as pd
import numpy as np
import datetime
import zoneinfo
import config

logger = logging.getLogger("miso_pipeline.validator")

class MisoValidator:
    def __init__(self):
        self.tz_ept = zoneinfo.ZoneInfo(config.TIMEZONE_EPT)
        self.tz_est = zoneinfo.ZoneInfo(config.TIMEZONE_EST)

    def validate_dataset(self, df: pd.DataFrame, start_date: str, end_date: str, download_results: list) -> dict:
        """
        Runs comprehensive data validation checks on the compiled dataset.
        
        Returns:
            dict: Validation results and details for the report.
        """
        results = {
            "summary": {},
            "missing_days": [],
            "duplicate_hours": [],
            "missing_values": [],
            "invalid_prices": [],
            "dst_adjustments": []
        }
        
        if df.empty:
            logger.error("Dataset is empty. Validation failed.")
            results["summary"] = {"Status": "Failed", "Reason": "Compiled dataset is empty."}
            return results

        # Convert date strings
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # 1. Check Date Range
        df_dt_est = pd.to_datetime(df["timestamp_est"])
        min_est = df_dt_est.min().date()
        max_est = df_dt_est.max().date()
        
        date_range_ok = (min_est == start_dt) and (max_est == end_dt)
        logger.info(f"Date range check: expected {start_dt} to {end_dt}. Found {min_est} to {max_est}. Status: {'OK' if date_range_ok else 'Mismatched'}")
        
        # 2. Check for Duplicate Timestamps
        # Using timestamp_utc since UTC represents absolute time without overlaps
        duplicate_mask = df["timestamp_utc"].duplicated(keep=False)
        df_duplicates = df[duplicate_mask]
        duplicate_count = df_duplicates.shape[0]
        if duplicate_count > 0:
            logger.warning(f"Validation: Found {duplicate_count} duplicate hourly timestamps!")
            results["duplicate_hours"] = df_duplicates.to_dict(orient="records")
            
        # 3. Check for Missing Days & Download Failures
        # We parse download_results to find failed/unavailable dates
        df_downloads = pd.DataFrame(download_results)
        if not df_downloads.empty:
            failed_downloads = df_downloads[df_downloads["status"].str.contains("Failed|Error", case=False, na=False)]
            for _, row in failed_downloads.iterrows():
                results["missing_days"].append({
                    "Date": row["date"],
                    "File_Type": row["file_type"],
                    "Error_Status": row["status"]
                })
        
        # 4. Check for Missing (Null) Values in Prices
        price_cols = ["REG_CAP", "REG_MIL", "SPIN", "SUPP"]
        if "LMP" in df.columns:
            price_cols.append("LMP")
            
        for col in price_cols:
            null_mask = df[col].isna()
            df_nulls = df[null_mask]
            null_count = df_nulls.shape[0]
            if null_count > 0:
                logger.warning(f"Validation: Column '{col}' has {null_count} missing values.")
                for _, row in df_nulls.iterrows():
                    results["missing_values"].append({
                        "Timestamp_EST": row["timestamp_est"],
                        "Timestamp_EPT": row["timestamp"],
                        "Column": col,
                        "Value": "NaN"
                    })
                    
        # 5. Check for Invalid Prices (Non-numeric or Negative Ancillary Prices)
        # Ancillary prices must be >= 0. LMPs can be negative in energy markets.
        ancillary_cols = ["REG_CAP", "REG_MIL", "SPIN", "SUPP"]
        for col in price_cols:
            # Check non-numeric values
            if not pd.api.types.is_numeric_dtype(df[col]):
                logger.error(f"Validation: Column '{col}' is not numeric!")
                # Find non-numeric rows
                non_num_mask = pd.to_numeric(df[col], errors="coerce").isna() & df[col].notna()
                df_non_num = df[non_num_mask]
                for _, row in df_non_num.iterrows():
                    results["invalid_prices"].append({
                        "Timestamp_EST": row["timestamp_est"],
                        "Column": col,
                        "Value": row[col],
                        "Error": "Non-numeric value detected"
                    })
            else:
                # Check negative values for ancillary prices
                if col in ancillary_cols:
                    neg_mask = df[col] < 0.0
                    df_negs = df[neg_mask]
                    neg_count = df_negs.shape[0]
                    if neg_count > 0:
                        logger.warning(f"Validation: Found {neg_count} negative clearing prices in '{col}'!")
                        for _, row in df_negs.iterrows():
                            results["invalid_prices"].append({
                                "Timestamp_EST": row["timestamp_est"],
                                "Column": col,
                                "Value": row[col],
                                "Error": "Negative clearing price (MCP must be >= 0)"
                            })

        # 6. Track DST Adjustments
        # In America/New_York:
        # Spring Forward (23h): local offsets shift from -0500 to -0400.
        # Fall Back (25h): local offsets shift from -0400 to -0500.
        # We can scan the dataset for the dates where offset shifts occur.
        df_dt_ept = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(self.tz_ept)
        df_date = df_dt_ept.dt.date
        
        # Count hours per day in local EPT
        hours_per_day = df_date.value_counts().sort_index()
        dst_days = hours_per_day[hours_per_day != 24]
        for day, count in dst_days.items():
            logger.info(f"DST Adjustment detected on {day}: {count} hours instead of 24.")
            results["dst_adjustments"].append({
                "Date": str(day),
                "Hour_Count": int(count),
                "Adjustment_Type": "Spring Forward (23 hours)" if count == 23 else "Fall Back (25 hours)"
            })
            
        # 7. Compile Summary Stats
        total_hours = df.shape[0]
        missing_val_count = len(results["missing_values"])
        invalid_price_count = len(results["invalid_prices"])
        missing_days_count = len(results["missing_days"])
        
        overall_status = "Pass"
        if duplicate_count > 0 or invalid_price_count > 0 or missing_val_count > (total_hours * 0.05):
            # If duplicates exist, invalid prices exist, or >5% of prices are missing, fail validation
            overall_status = "Fail"
            
        results["summary"] = {
            "Overall Validation Status": overall_status,
            "Total Hours Compiled": total_hours,
            "Date Range Expected": f"{start_date} to {end_date}",
            "Date Range Found": f"{min_est} to {max_est}",
            "Date Range Valid": "Yes" if date_range_ok else "No",
            "Duplicate Hours Found": duplicate_count,
            "Missing Days/Reports Count": missing_days_count,
            "Missing Price Values Count": missing_val_count,
            "Invalid Price Values Count": invalid_price_count,
            "DST Transition Days Detected": len(results["dst_adjustments"])
        }
        
        return results

    def generate_validation_report(self, results: dict, output_path: Path = config.VALIDATION_REPORT_OUT):
        """
        Saves the validation findings in a formatted Excel file with separate sheets.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                # 1. Summary sheet
                df_summary = pd.DataFrame(list(results["summary"].items()), columns=["Metric", "Value"])
                df_summary.to_excel(writer, sheet_name="Summary", index=False)
                
                # 2. Missing Days sheet
                df_missing_days = pd.DataFrame(results["missing_days"])
                if df_missing_days.empty:
                    df_missing_days = pd.DataFrame(columns=["Date", "File_Type", "Error_Status", "Message"])
                    df_missing_days.loc[0] = ["No missing reports", "-", "-", "All expected daily reports successfully downloaded"]
                df_missing_days.to_excel(writer, sheet_name="Missing Days", index=False)
                
                # 3. Duplicate Hours sheet
                df_duplicates = pd.DataFrame(results["duplicate_hours"])
                if df_duplicates.empty:
                    df_duplicates = pd.DataFrame(columns=["timestamp", "timestamp_utc", "Message"])
                    df_duplicates.loc[0] = ["-", "-", "No duplicate hours found in dataset"]
                df_duplicates.to_excel(writer, sheet_name="Duplicate Hours", index=False)
                
                # 4. Missing Values sheet
                df_missing_vals = pd.DataFrame(results["missing_values"])
                if df_missing_vals.empty:
                    df_missing_vals = pd.DataFrame(columns=["Timestamp_EST", "Column", "Message"])
                    df_missing_vals.loc[0] = ["-", "-", "No missing price values found"]
                df_missing_vals.to_excel(writer, sheet_name="Missing Values", index=False)
                
                # 5. Invalid Prices sheet
                df_invalid = pd.DataFrame(results["invalid_prices"])
                if df_invalid.empty:
                    df_invalid = pd.DataFrame(columns=["Timestamp_EST", "Column", "Value", "Error", "Message"])
                    df_invalid.loc[0] = ["-", "-", "-", "-", "No invalid or negative prices found"]
                df_invalid.to_excel(writer, sheet_name="Invalid Prices", index=False)
                
                # 6. DST Adjustments sheet
                df_dst = pd.DataFrame(results["dst_adjustments"])
                if df_dst.empty:
                    df_dst = pd.DataFrame(columns=["Date", "Hour_Count", "Adjustment_Type", "Message"])
                    df_dst.loc[0] = ["-", "-", "-", "No DST adjustments occurred in the date range"]
                df_dst.to_excel(writer, sheet_name="DST Adjustments", index=False)
                
            logger.info(f"Validation report spreadsheet generated successfully at: {output_path}")
            
        except Exception as e:
            logger.error(f"Failed to generate validation report spreadsheet: {e}")

if __name__ == "__main__":
    # Test validator
    logging.basicConfig(level=logging.INFO)
    validator = MisoValidator()
    print("MisoValidator initialized successfully.")
