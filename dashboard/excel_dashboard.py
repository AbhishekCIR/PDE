# dashboard/excel_dashboard.py
import os
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import numpy as np

def generate_dashboard_charts(df_opt, metrics, market_name, output_dir="logs"):
    """
    Generates three high-quality matplotlib charts for BESS operations, formatted
    with clean, modern corporate presentation styles (dark-slate backgrounds, sharp grids, neon indicators).
    Saves them as PNG images in the output_dir.
    """
    os.makedirs(output_dir, exist_ok=True)
    sns.set_theme(style="darkgrid")
    
    # 1. Stacked Revenue Chart (Monthly)
    # Group df_opt by month and sum the revenue columns
    df_opt['Month_Num'] = df_opt['timestamp'].dt.month
    monthly_sums = df_opt.groupby('Month_Num').sum(numeric_only=True)
    
    # Identify active revenue columns depending on market
    rev_cols = []
    labels = []
    colors = []
    
    if market_name == 'ERCOT':
        rev_cols = ['Energy_Revenue', 'REGUP_Revenue', 'REGDN_Revenue', 'NSPIN_Revenue', 'ECRS_Revenue', 'RRS_Revenue']
        labels = ['Energy Arbitrage', 'Reg Up', 'Reg Down', 'Non-Spin', 'ECRS', 'RRS']
        colors = ['#38BDF8', '#10B981', '#EF4444', '#F59E0B', '#8B5CF6', '#EC4899']
    elif market_name == 'MISO':
        rev_cols = ['Energy_Revenue', 'REG_Revenue', 'SPIN_Revenue', 'SUPP_Revenue']
        labels = ['Energy Arbitrage', 'Regulation', 'Spinning Reserve', 'Supplemental Reserve']
        colors = ['#38BDF8', '#10B981', '#F59E0B', '#8B5CF6']
    elif market_name == 'PJM':
        rev_cols = ['Energy_Revenue', 'RegA_Revenue', 'RegD_Revenue', 'SYNCH_Revenue', 'NONSYNCH_Revenue']
        labels = ['Energy Arbitrage', 'RegA', 'RegD', 'Synchronized', 'Non-Synchronized']
        colors = ['#38BDF8', '#10B981', '#EC4899', '#F59E0B', '#8B5CF6']
    else:
        rev_cols = ['Energy_Revenue', 'Ancillary_Revenue']
        labels = ['Energy Arbitrage', 'Ancillary Services']
        colors = ['#38BDF8', '#10B981']
        
    # Standardize columns (ensure they exist in df_opt, if not fill 0)
    for col in rev_cols:
        if col not in monthly_sums.columns:
            monthly_sums[col] = 0.0
            
    # Map month numbers to short names
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_sums.index = [month_names[m-1] for m in monthly_sums.index]
    
    # Plot Monthly Revenue Stack
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor='#0F172A')
    ax.set_facecolor('#1E293B')
    
    bottom = np.zeros(len(monthly_sums))
    for i, col in enumerate(rev_cols):
        ax.bar(monthly_sums.index, monthly_sums[col] / 1000.0, bottom=bottom / 1000.0, label=labels[i], color=colors[i])
        bottom += monthly_sums[col].values
        
    ax.set_title(f"BESS Monthly Revenue Stack - {market_name} ($k)", color='white', fontsize=12, fontweight='bold', pad=15)
    ax.set_ylabel("Revenue ($k)", color='white', fontsize=10)
    ax.tick_params(colors='white', which='both')
    ax.xaxis.grid(False)
    ax.yaxis.grid(True, color='#334155', linestyle='--')
    
    # Legend formatting
    legend = ax.legend(facecolor='#1E293B', edgecolor='#334155', labelcolor='white', loc='upper right', framealpha=0.9)
    fig.tight_layout()
    chart1_path = os.path.abspath(os.path.join(output_dir, "bess_monthly_revenue.png"))
    fig.savefig(chart1_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    
    # 2. SoC & Price Profile Chart (Sample Summer Week HE 4000 to 4168)
    sample_df = df_opt.iloc[4000:4168].copy()
    
    fig, ax1 = plt.subplots(figsize=(8, 4.5), facecolor='#0F172A')
    ax1.set_facecolor('#1E293B')
    
    # Plot state of charge on primary axis
    color_soc = '#0284C7'
    ax1.plot(sample_df['timestamp'], sample_df['soc_mwh'], color=color_soc, linewidth=2, label="State of Charge (MWh)")
    ax1.set_ylabel("Battery SoC (MWh)", color=color_soc, fontsize=10, fontweight='bold')
    ax1.tick_params(axis='y', labelcolor=color_soc, colors='white')
    ax1.tick_params(axis='x', colors='white')
    ax1.set_xlabel("Time (HE)", color='white', fontsize=10)
    ax1.set_title(f"SoC Dispatch & Market Price Tracker (Summer Week)", color='white', fontsize=12, fontweight='bold', pad=15)
    ax1.xaxis.grid(True, color='#334155', linestyle=':')
    ax1.yaxis.grid(True, color='#334155', linestyle=':')
    
    # Create twin axis for LMP
    ax2 = ax1.twinx()
    color_price = '#EF4444'
    ax2.plot(sample_df['timestamp'], sample_df['LMP'], color=color_price, linewidth=1, linestyle='--', alpha=0.8, label="LMP ($/MWh)")
    ax2.set_ylabel("LMP Price ($/MWh)", color=color_price, fontsize=10, fontweight='bold')
    ax2.tick_params(axis='y', labelcolor=color_price, colors='white')
    ax2.xaxis.grid(False)
    ax2.yaxis.grid(False)
    
    # Adjust layout
    fig.tight_layout()
    chart2_path = os.path.abspath(os.path.join(output_dir, "bess_dispatch_tracker.png"))
    fig.savefig(chart2_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    
    # 3. 20-Year Capacity & Degradation Sizing Timeline
    # Read degradation details
    efc_per_year = metrics.get('Equivalent Full Cycles (EFC)', 300.0)
    if pd.isna(efc_per_year) or efc_per_year == 0:
        efc_per_year = 300.0
        
    bess_life_years = 20
    # Let's project State of Health (SoH) over 20 years
    years = np.arange(0, bess_life_years + 1)
    
    # We model 2% annual degradation, with augmentation bumps of:
    # Year 6: SoH goes back up by 8.53%
    # Year 11: SoH goes back up by 7.7%
    # Year 16: SoH goes back up by 6.0%
    # Capacity is capped at 1.0 (or nameplate capacity)
    soh = []
    current_soh = 1.0
    for y in years:
        if y > 0:
            current_soh -= 0.02 # 2% annual degradation
            # Augmentation bumps at operational years 6, 11, 16 (year indices)
            if y == 6:
                current_soh = min(1.0, current_soh + 0.0853)
            elif y == 11:
                current_soh = min(1.0, current_soh + 0.077)
            elif y == 16:
                current_soh = min(1.0, current_soh + 0.060)
        soh.append(current_soh * 100.0)
        
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor='#0F172A')
    ax.set_facecolor('#1E293B')
    ax.plot(years, soh, color='#10B981', linewidth=2.5, marker='o', label="State of Health (SoH %)")
    
    # Draw horizontal threshold lines
    ax.axhline(80.0, color='#F59E0B', linestyle='--', alpha=0.7, label="Augmentation Threshold (80%)")
    
    # Highlight augmentation points with vertical lines
    ax.axvline(6, color='#8B5CF6', linestyle=':', alpha=0.8, label="Augmentation Events")
    ax.axvline(11, color='#8B5CF6', linestyle=':', alpha=0.8)
    ax.axvline(16, color='#8B5CF6', linestyle=':', alpha=0.8)
    
    ax.set_title("20-Year Battery State of Health (SoH) Timeline", color='white', fontsize=12, fontweight='bold', pad=15)
    ax.set_ylabel("Effective Capacity (State of Health %)", color='white', fontsize=10)
    ax.set_xlabel("Project Operating Year", color='white', fontsize=10)
    ax.tick_params(colors='white', which='both')
    ax.set_ylim(60, 105)
    ax.set_xticks(years)
    ax.xaxis.grid(True, color='#334155', linestyle=':')
    ax.yaxis.grid(True, color='#334155', linestyle=':')
    
    legend = ax.legend(facecolor='#1E293B', edgecolor='#334155', labelcolor='white', loc='lower left')
    fig.tight_layout()
    chart3_path = os.path.abspath(os.path.join(output_dir, "bess_degradation_timeline.png"))
    fig.savefig(chart3_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    
    return {
        'revenue_stack': chart1_path,
        'soc_tracker': chart2_path,
        'degradation': chart3_path
    }

def inject_charts_to_excel(sheet, chart_paths):
    """Inserts the generated chart images into the Dashboard sheet of Excel."""
    # Delete existing shapes in the BESS section to avoid overlap
    for pic in sheet.pictures:
        if pic.name.startswith("BESS_Chart"):
            pic.delete()
            
    # Inject pictures at specific coordinates
    # We put them to the right of the Solar Dashboard charts
    sheet.pictures.add(
        chart_paths['revenue_stack'], 
        name="BESS_Chart_Revenue", 
        left=sheet.range("O10").left, 
        top=sheet.range("O10").top,
        width=400,
        height=225
    )
    
    sheet.pictures.add(
        chart_paths['soc_tracker'], 
        name="BESS_Chart_SoC", 
        left=sheet.range("O25").left, 
        top=sheet.range("O25").top,
        width=400,
        height=225
    )
    
    sheet.pictures.add(
        chart_paths['degradation'], 
        name="BESS_Chart_Degradation", 
        left=sheet.range("O40").left, 
        top=sheet.range("O40").top,
        width=400,
        height=225
    )
