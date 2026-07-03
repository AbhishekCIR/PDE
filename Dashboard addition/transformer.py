import logging
import datetime
import pandas as pd
import numpy as np
import zoneinfo
import config

logger = logging.getLogger("miso_pipeline.transformer")

class MisoTransformer:
    def __init__(self, rename_schema: dict = config.RENAME_SCHEMA):
        self.rename_schema = rename_schema
        self.tz_est = zoneinfo.ZoneInfo(config.TIMEZONE_EST)
        self.tz_ept = zoneinfo.ZoneInfo(config.TIMEZONE_EPT)

    def melt_daily_data(self, df_wide: pd.DataFrame, date_str: str) -> pd.DataFrame:
        """
        Melts a daily wide DataFrame (HE 1 to HE 24) into a long format DataFrame.
        """
        if df_wide.empty:
            return pd.DataFrame()

        he_cols = [f"HE {i}" for i in range(1, 25)]
        # Filter columns to only what exists in the df (to avoid errors)
        he_cols = [c for c in he_cols if c in df_wide.columns]
        
        # Melt HE columns
        df_long = pd.melt(
            df_wide,
            id_vars=["Pnode", "Zone", "MCP_Type"],
            value_vars=he_cols,
            var_name="Hour_Ending",
            value_name="Price"
        )
        
        # Parse HE number
        df_long["HE_Num"] = df_long["Hour_Ending"].str.extract(r"(\d+)").astype(int)
        df_long["Date_Str"] = date_str
        
        return df_long

    def compute_timestamps(self, df_long: pd.DataFrame) -> pd.DataFrame:
        """
        Computes EST, EPT (local prevailing time), and UTC timestamps for each hour.
        
        MISO files are Hour-Ending in EST (UTC-5). 
        HE 1 = 00:00 to 01:00 EST. The start of the hour is 00:00 EST.
        We map HE 1 -> 00:00, ..., HE 24 -> 23:00.
        """
        if df_long.empty:
            return df_long

        df = df_long.copy()
        
        # Create timezone-naive EST datetime objects for the start of the hour
        def make_est_dt(row):
            date_obj = datetime.datetime.strptime(row["Date_Str"], "%Y%m%d").date()
            hour = row["HE_Num"] - 1  # 0 to 23
            dt = datetime.datetime.combine(date_obj, datetime.time(hour=hour))
            return dt.replace(tzinfo=self.tz_est)

        df["dt_est"] = df.apply(make_est_dt, axis=1)
        
        # Convert to local Prevailing Time (EPT) and UTC
        df["dt_ept"] = df["dt_est"].dt.tz_convert(self.tz_ept)
        df["dt_utc"] = df["dt_est"].dt.tz_convert(datetime.timezone.utc)
        
        # Format timestamps as strings for standard outputs
        # Keep offset to distinguish the duplicate hour in November (Autumn DST Fall Back)
        df["timestamp"] = df["dt_ept"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S%z"))
        df["timestamp_utc"] = df["dt_utc"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S"))
        df["timestamp_est"] = df["dt_est"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S"))
        
        return df

    def transform_dataset(self, df_long_all: pd.DataFrame) -> pd.DataFrame:
        """
        Pivots the melted and timestamped dataset into a wide format suitable for BESS optimizers.
        
        Columns: timestamp, timestamp_utc, timestamp_est, REG_CAP, REG_MIL, SPIN, SUPP.
        Sorted chronologically by UTC.
        """
        if df_long_all.empty:
            logger.warning("Empty dataset passed to transformer.")
            return pd.DataFrame()

        # Step 1: Clean and rename price products
        df = df_long_all.copy()
        df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
        df["MCP_Type"] = df["MCP_Type"].map(self.rename_schema)
        
        # Drop any rows that failed mapping (e.g. DEM* products not needed)
        df = df[df["MCP_Type"].notna()]
        
        # Step 2: Pivot the DataFrame
        # We group by timestamps and pivot the MCP Types into columns
        try:
            df_pivot = df.pivot_table(
                index=["timestamp", "timestamp_utc", "timestamp_est"],
                columns="MCP_Type",
                values="Price",
                aggfunc="first"
            ).reset_index()
            
            # Ensure all output columns exist (in case some were missing in raw files)
            for col in self.rename_schema.values():
                if col not in df_pivot.columns:
                    df_pivot[col] = np.nan
                    
            # Reorder columns
            cols_order = ["timestamp", "timestamp_utc", "timestamp_est", "REG_CAP", "REG_MIL", "SPIN", "SUPP"]
            df_pivot = df_pivot[cols_order]
            
            # Step 3: Chronological Sorting by UTC
            df_pivot = df_pivot.sort_values(by="timestamp_utc").reset_index(drop=True)
            
            return df_pivot
            
        except Exception as e:
            logger.error(f"Error pivoting and sorting final dataset: {e}")
            raise

    def fill_missing_timestamps(self, df_pivoted: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fills any gaps in the timeseries range to ensure BESS optimizer receives a continuous sequence.
        
        Generates a complete UTC hourly series for the range, reindexes the pivoted data,
        and logs any gaps filled with NaN.
        """
        if df_pivoted.empty:
            return df_pivoted
            
        # Parse dates
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Generate complete date range in EST
        # Since the downloader downloads YYYYMMDD and we map HE 1 -> HE 24 to EST,
        # the complete series goes from start_date 00:00 EST to end_date 23:00 EST.
        tz_est = zoneinfo.ZoneInfo(config.TIMEZONE_EST)
        tz_ept = zoneinfo.ZoneInfo(config.TIMEZONE_EPT)
        
        est_range = pd.date_range(
            start=datetime.datetime.combine(start, datetime.time(0, 0)),
            end=datetime.datetime.combine(end, datetime.time(23, 0)),
            freq="h",
            tz=tz_est
        )
        
        # Create a reference mapping DataFrame
        ref_df = pd.DataFrame()
        ref_df["dt_est"] = est_range
        ref_df["dt_utc"] = ref_df["dt_est"].dt.tz_convert(datetime.timezone.utc)
        ref_df["dt_ept"] = ref_df["dt_est"].dt.tz_convert(tz_ept)
        
        ref_df["timestamp"] = ref_df["dt_ept"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S%z"))
        ref_df["timestamp_utc"] = ref_df["dt_utc"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S"))
        ref_df["timestamp_est"] = ref_df["dt_est"].apply(lambda x: x.strftime("%Y-%m-%d %H:%M:%S"))
        
        ref_df = ref_df[["timestamp", "timestamp_utc", "timestamp_est"]]
        
        # Merge pivoted dataframe with reference dataframe to identify missing rows
        df_complete = pd.merge(ref_df, df_pivoted, on=["timestamp", "timestamp_utc", "timestamp_est"], how="left")
        
        missing_count = df_complete["REG_CAP"].isna().sum()
        if missing_count > 0:
            logger.warning(f"Timeseries gap detected: {missing_count} hours are missing in the raw reports. Filled with NaN.")
            
        return df_complete

if __name__ == "__main__":
    # Quick execution test
    logging.basicConfig(level=logging.INFO)
    transformer = MisoTransformer()
    print("Transformer initialized successfully.")
