import logging
from pathlib import Path
import pandas as pd
import numpy as np
import config
from parser import MisoParser
from transformer import MisoTransformer

logger = logging.getLogger("miso_pipeline.merge_lmp")

class MisoLmpProcessor:
    def __init__(self):
        self.parser = MisoParser()
        self.transformer = MisoTransformer()

    def parse_daily_lmp(self, filepath: Path, target_node: str) -> pd.DataFrame:
        """
        Parses a single day's LMP CSV file and returns a long DataFrame with timestamps and LMP values.
        
        LMP files have headers: Node, Type, Value, HE 1, ..., HE 24.
        We filter Node == target_node and Value == 'LMP'.
        """
        try:
            # Re-use the clean_dataframe helper from parser.py
            # Since LMP files have a similar structure (skip 4 rows)
            df = self.parser.clean_dataframe(filepath)
            
            # Canonicalize target Pnode name
            target_node_lower = target_node.strip().lower()
            
            # Filter for the target node and the price value (we ignore MCC and MCL components)
            df_filtered = df[
                (df['Pnode'].str.lower() == target_node_lower) & 
                (df['MCP_Type'].astype(str).str.strip().str.upper() == 'LMP')
            ]
            
            if df_filtered.empty:
                logger.warning(f"No LMP values found for node '{target_node}' in file {filepath.name}.")
                # Generate placeholder rows with NaNs
                dummy_row = {'Pnode': target_node, 'Zone': 'Unknown', 'MCP_Type': 'LMP'}
                for he in range(1, 25):
                    dummy_row[f"HE {he}"] = float('nan')
                df_filtered = pd.DataFrame([dummy_row])
                
            # Melt HE 1 - HE 24 into long format
            date_str = filepath.name.split('_')[0] # Filename starts with YYYYMMDD
            df_long = self.transformer.melt_daily_data(df_filtered, date_str)
            
            # Compute EPT and UTC timestamps
            df_timestamped = self.transformer.compute_timestamps(df_long)
            
            # Prepare final layout
            df_final = df_timestamped[["timestamp", "timestamp_utc", "timestamp_est", "Price"]].copy()
            df_final = df_final.rename(columns={"Price": "LMP"})
            df_final["LMP"] = pd.to_numeric(df_final["LMP"], errors="coerce")
            
            return df_final
            
        except Exception as e:
            logger.error(f"Error parsing daily LMP file {filepath}: {e}")
            # Return empty skeleton with NaNs to prevent pipeline crash
            date_str = filepath.name.split('_')[0]
            skeleton = []
            for he in range(1, 25):
                skeleton.append({
                    "Date_Str": date_str,
                    "HE_Num": he
                })
            df_sk = pd.DataFrame(skeleton)
            df_sk = self.transformer.compute_timestamps(df_sk)
            df_sk["LMP"] = np.nan
            return df_sk[["timestamp", "timestamp_utc", "timestamp_est", "LMP"]]

    def merge_lmp_with_mcp(self, df_mcp: pd.DataFrame, lmp_filepaths: list, target_node: str) -> pd.DataFrame:
        """
        Processes a list of daily LMP files, aggregates them, and merges with the main MCP DataFrame.
        """
        logger.info(f"Processing and merging LMP data for node '{target_node}'...")
        
        lmp_dfs = []
        for filepath in lmp_filepaths:
            if filepath.exists():
                df_day_lmp = self.parse_daily_lmp(filepath, target_node)
                lmp_dfs.append(df_day_lmp)
            else:
                logger.error(f"LMP file {filepath} not found for merging.")
                
        if not lmp_dfs:
            logger.error("No LMP files were successfully processed. Merging LMP column with NaNs.")
            df_mcp = df_mcp.copy()
            df_mcp["LMP"] = np.nan
            return df_mcp
            
        # Combine all daily LMP rows
        df_lmp_all = pd.concat(lmp_dfs, ignore_index=True)
        
        # Deduplicate to ensure exactly one value per timestamp (if any duplicate dates were processed)
        df_lmp_all = df_lmp_all.drop_duplicates(subset=["timestamp_utc"])
        
        # Merge with main MCP dataset
        df_merged = pd.merge(
            df_mcp,
            df_lmp_all[["timestamp", "timestamp_utc", "timestamp_est", "LMP"]],
            on=["timestamp", "timestamp_utc", "timestamp_est"],
            how="left"
        )
        
        # Reorder columns to place LMP first after timestamps
        cols = ["timestamp", "timestamp_utc", "timestamp_est", "LMP", "REG_CAP", "REG_MIL", "SPIN", "SUPP"]
        df_merged = df_merged[cols]
        
        logger.info("Successfully merged LMP with Ancillary Prices.")
        return df_merged

if __name__ == "__main__":
    # Test execution
    logging.basicConfig(level=logging.INFO)
    processor = MisoLmpProcessor()
    print("MisoLmpProcessor initialized successfully.")
