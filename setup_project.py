# setup_project.py
import os
import shutil

def main():
    print("Setting up directory structure for BESS Optimization Platform...")
    
    # Define directories
    dirs = [
        "config",
        "data/telemetry",
        "data_adapters",
        "market_modules",
        "forecast_engine",
        "optimization",
        "dashboard",
        "tests",
        "logs"
    ]
    
    # Create directories if they don't exist
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        print(f"  Created directory: {d}")
        
    # Copy ERCOT telemetry price file if it exists in root
    src_price_file = "LMP_AS_Combined 2024-2025.csv"
    dst_price_file = "data/telemetry/ercot_2025_hourly.csv"
    
    if os.path.exists(src_price_file):
        shutil.copy(src_price_file, dst_price_file)
        print(f"  Copied {src_price_file} to {dst_price_file}")
    else:
        print(f"  Warning: Source price file '{src_price_file}' not found in root.")

    # Create dummy csv price file for MISO if not exists
    miso_price_file = "data/telemetry/miso_2024_hourly.csv"
    if not os.path.exists(miso_price_file):
        # We can extract the sample data from Dashboard addition/output/optimizer_input_rt.csv
        miso_src = "Dashboard addition/output/optimizer_input_rt.csv"
        if os.path.exists(miso_src):
            shutil.copy(miso_src, miso_price_file)
            print(f"  Copied MISO sample telemetry to {miso_price_file}")
        else:
            # Let's generate a placeholder or copy generic
            print("  MISO raw sample telemetry not found. Will generate dynamically if needed.")
            
    print("Setup completed successfully!")

if __name__ == "__main__":
    main()
