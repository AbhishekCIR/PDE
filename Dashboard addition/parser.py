import os
import io
import logging
import pandas as pd
from pathlib import Path
import config

logger = logging.getLogger("miso_pipeline.parser")

class MisoParser:
    def __init__(self, required_mcps: list = config.REQUIRED_MCPS):
        self.required_mcps = required_mcps

    def clean_dataframe(self, filepath: Path) -> pd.DataFrame:
        """
        Reads a MISO CSV market report, skips metadata headers dynamically by finding the header row, 
        and returns a cleaned DataFrame.
        """
        try:
            # Check if file exists and is not empty
            if not filepath.exists() or filepath.stat().st_size == 0:
                raise FileNotFoundError(f"File {filepath} does not exist or is empty.")
                
            # Read raw content and split into non-blank stripped lines
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                raw_content = f.read()
                
            lines = [line.strip() for line in raw_content.split('\n') if line.strip()]
            
            # Find the header row dynamically
            header_idx = 0
            for idx, line in enumerate(lines):
                if 'HE 1' in line or 'HE_1' in line:
                    header_idx = idx
                    break
                    
            # Reconstruct the file content from the header row onwards
            cleaned_content = '\n'.join(lines[header_idx:])
            
            # Read from StringIO buffer
            df = pd.read_csv(io.StringIO(cleaned_content))
            
            # Clean column names (strip whitespace)
            df.columns = [str(col).strip() for col in df.columns]
            
            # Find and map the target columns
            # Column 0 is the Pnode name (could be unnamed or Pnode)
            # Column 1 is the Zone (could be unnamed or Zone)
            # Column 2 is the MCP Type (could be MCP Type)
            col_mapping = {}
            if df.columns[0].startswith('Unnamed') or df.columns[0] == '':
                col_mapping[df.columns[0]] = 'Pnode'
            else:
                col_mapping[df.columns[0]] = 'Pnode'
                
            if df.columns[1].startswith('Unnamed') or df.columns[1] == '':
                col_mapping[df.columns[1]] = 'Zone'
            else:
                col_mapping[df.columns[1]] = 'Zone'
                
            if 'MCP Type' in df.columns:
                col_mapping['MCP Type'] = 'MCP_Type'
            elif 'MCPType' in df.columns:
                col_mapping['MCPType'] = 'MCP_Type'
                
            df = df.rename(columns=col_mapping)
            
            # Double check names
            df = df.rename(columns={
                df.columns[0]: 'Pnode',
                df.columns[1]: 'Zone',
                df.columns[2]: 'MCP_Type'
            })
            
            # Clean string values (remove leading/trailing spaces)
            df['Pnode'] = df['Pnode'].astype(str).str.strip()
            df['Zone'] = df['Zone'].astype(str).str.strip()
            df['MCP_Type'] = df['MCP_Type'].astype(str).str.strip()
            
            # Remove repeated header rows that occur inside files (from multiple combined sections)
            df = df[df['Pnode'] != 'Pnode']
            df = df[df['MCP_Type'] != 'MCP Type']
            
            # Filter out empty rows or lines with nan values in MCP_Type
            df = df[df['MCP_Type'].notna() & (df['MCP_Type'] != 'nan') & (df['MCP_Type'] != '')]
            
            return df
            
        except Exception as e:
            logger.error(f"Error reading and cleaning {filepath}: {e}")
            raise

    def parse_daily_mcp(self, filepath: Path, target_pnode: str) -> pd.DataFrame:
        """
        Parses a single day's MCP file and returns a wide-format DataFrame with 
        columns: Pnode, MCP_Type, HE 1, ..., HE 24.
        
        It joins the Pnode-level prices (GENREGMCP, GENSPINMCP, GENSUPPMCP)
        with the market-wide price for REGMILEAGEMCP.
        """
        try:
            df = self.clean_dataframe(filepath)
            
            # Canonicalize target Pnode
            pnode_lower = target_pnode.strip().lower()
            is_miso_wide = pnode_lower in ('miso-wide', 'miso wide')
            
            # Helper to check if a row is a Miso-Wide row
            def is_miso_wide_row(val):
                return str(val).strip().lower() in ('miso-wide', 'miso wide')

            if is_miso_wide:
                # Extract all required MCPs directly from Miso-Wide
                df_filtered = df[df['Pnode'].apply(is_miso_wide_row) & df['MCP_Type'].isin(self.required_mcps)]
                if df_filtered.empty:
                    logger.warning(f"No Miso-Wide pricing rows found in {filepath.name}")
                return df_filtered
            
            # Extraction for specific Pnode
            # 1. Pnode level prices (GENREGMCP, GENSPINMCP, GENSUPPMCP)
            pnode_products = ["GENREGMCP", "GENSPINMCP", "GENSUPPMCP"]
            df_pnode = df[(df['Pnode'].str.lower() == pnode_lower) & df['MCP_Type'].isin(pnode_products)]
            
            if df_pnode.empty:
                logger.warning(f"Target Pnode '{target_pnode}' not found in {filepath.name}. Generating empty row placeholders.")
                # We will construct a dummy dataframe with NaNs for the Pnode
                dummy_rows = []
                for prod in pnode_products:
                    row_dict = {'Pnode': target_pnode, 'Zone': 'Unknown', 'MCP_Type': prod}
                    for he in range(1, 25):
                        row_dict[f"HE {he}"] = float('nan')
                    dummy_rows.append(row_dict)
                df_pnode = pd.DataFrame(dummy_rows)
            
            # 2. Market-wide mileage prices (REGMILEAGEMCP)
            df_mileage = df[df['Pnode'].apply(is_miso_wide_row) & (df['MCP_Type'] == 'REGMILEAGEMCP')]
            
            if df_mileage.empty:
                logger.warning(f"REGMILEAGEMCP not found in Miso-Wide rows in {filepath.name}. Generating placeholder.")
                row_dict = {'Pnode': 'Miso-Wide', 'Zone': '-', 'MCP_Type': 'REGMILEAGEMCP'}
                for he in range(1, 25):
                    row_dict[f"HE {he}"] = float('nan')
                df_mileage = pd.DataFrame([row_dict])
            else:
                # Force the Pnode name for alignment when merging
                df_mileage = df_mileage.copy()
                df_mileage['Pnode'] = target_pnode

            # Combine Pnode data and mileage data
            df_combined = pd.concat([df_pnode, df_mileage], ignore_index=True)
            return df_combined

        except Exception as e:
            logger.error(f"Error parsing daily file {filepath} for node {target_pnode}: {e}")
            # Return empty skeleton with NaNs to prevent system halt
            skeleton = []
            for mcp in self.required_mcps:
                row = {'Pnode': target_pnode, 'Zone': '-', 'MCP_Type': mcp}
                for he in range(1, 25):
                    row[f"HE {he}"] = float('nan')
                skeleton.append(row)
            return pd.DataFrame(skeleton)

if __name__ == "__main__":
    # Test execution
    logging.basicConfig(level=logging.INFO)
    parser = MisoParser()
    test_file = config.RAW_DIR / "20240101_rtmcp.csv"
    if test_file.exists():
        print(f"Testing parser on existing file: {test_file}")
        res = parser.parse_daily_mcp(test_file, "ALTW.AMESWIND")
        print("Parsed rows:")
        print(res[['Pnode', 'MCP_Type', 'HE 1', 'HE 24']])
    else:
        print("Run test_download.py first to cache reports.")
