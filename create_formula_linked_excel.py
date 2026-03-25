import pandas as pd
import numpy as np
import xlsxwriter
from bess_rtc_model import BESS_Simulator

def create_formula_linked_excel():
    print("Generating v3 8760-hour advanced formula-linked Excel model...")
    sim = BESS_Simulator()
    df = sim.generate_sample_data(days=365, freq='1h')
    
    filename = 'BESS_Formula_Linked_Model_v3.xlsx'
    workbook = xlsxwriter.Workbook(filename)
    
    header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3', 'border': 1})
    input_fmt = workbook.add_format({'bg_color': '#FFFFE0', 'border': 1})
    date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm', 'border': 1})
    num_fmt = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
    
    # --- 1. Inputs Worksheet ---
    ws_inputs = workbook.add_worksheet('Inputs')
    ws_inputs.set_column('A:A', 30)
    ws_inputs.set_column('B:B', 15)
    ws_inputs.set_column('C:C', 80)
    
    ws_inputs.write('A1', 'Parameter', header_fmt)
    ws_inputs.write('B1', 'Value', header_fmt)
    ws_inputs.write('C1', 'Description', header_fmt)
    
    inputs = [
        ('Power_MW', 100, 'Battery Power Capacity (MW)'),
        ('Duration_Hr', 4, 'Battery Duration (Hours)'),
        ('Max_Cycles_Per_Day', 1.0, 'Max Throughput per day (1.0 = 1 full discharge capability per day)'),
        ('RTE', 0.90, 'Round Trip Efficiency'),
        ('Initial_SoC_Pct', 0.50, 'Initial State of Charge (%)'),
        ('Reg_SoC_Drift_Pct', 0.02, 'SoC drift during regulation (MWh lost per MW Reg per Hour)'),
        ('Min_Arb_Spread_$/MWh', 20.0, 'Minimum profit spread required to engage in arbitrage ($/MWh)'),
        ('Ancillary_Participation_Pct', 0.80, 'Max % of physical capacity allocatable to Regulation (70-90%)'),
        ('Discharge_Priority_Mult', 1.20, 'Only discharge if LMP > Daily_Average * This Multiplier (saves cycles for peaks)'),
        ('Min_Mode_Duration_Hr', 2.0, '(Locked in formulas: Prevents 1-hour mode flipping)'),
    ]
    
    for idx, (name, val, desc) in enumerate(inputs, start=1):
        ws_inputs.write(idx, 0, name)
        ws_inputs.write(idx, 1, val, input_fmt)
        ws_inputs.write(idx, 2, desc)
        
    ws_inputs.write('A13', 'Calculations', header_fmt)
    ws_inputs.write('A14', 'Max_Energy_MWh')
    ws_inputs.write_formula('B14', '=B2*B3', num_fmt)
    ws_inputs.write('A15', 'Eff_c')
    ws_inputs.write_formula('B15', '=SQRT(B5)', num_fmt)
    ws_inputs.write('A16', 'Eff_d')
    ws_inputs.write_formula('B16', '=SQRT(B5)', num_fmt)

    # --- 2. Hourly Model Worksheet ---
    ws_model = workbook.add_worksheet('Hourly_Model')
    ws_model.set_column('A:A', 16)
    ws_model.set_column('B:V', 14)
    ws_model.set_column('W:W', 0) # hide helper cols if preferred, but let's keep visible
    
    headers = [
        'Timestamp', 'LMP ($)', 'Reg_Price ($)', 'Daily_Avg_LMP', 'Forward_6hr_Avg', 
        'Starting_SoC', 'Cum_Discharge_Tdy', 
        'Max_Charge_MW', 'Max_Discharge_MW', 'Max_Reg_MW',
        'Exp_Charge_$', 'Exp_Discharge_$', 'Exp_Reg_$', 
        'Suggested_Mode', 'Final_Mode_2hr',
        'Charge_MW', 'Discharge_MW', 'Reg_MW', 
        'Energy_Rev', 'Reg_Rev', 'Reg_SoC_Drift', 'Ending_SoC'
    ]
    
    for col, h in enumerate(headers):
        ws_model.write(0, col, h, header_fmt)
        
    for i in range(len(df)):
        row = i + 1
        r = row + 1 
        
        ws_model.write_datetime(row, 0, df['timestamp'].iloc[i], date_fmt)
        ws_model.write_number(row, 1, df['LMP'].iloc[i], num_fmt)
        ws_model.write_number(row, 2, df['Reg_Price'].iloc[i], num_fmt)
        
        # D: Daily Avg LMP
        ws_model.write_formula(row, 3, f'=AVERAGEIFS(B:B, A:A, ">="&INT(A{r}), A:A, "<"&INT(A{r})+1)', num_fmt)
        # E: Forward 6hr Avg
        max_r = min(r + 5, 8761)
        ws_model.write_formula(row, 4, f'=AVERAGE(B{r}:B{max_r})', num_fmt)
        
        # F: Starting SoC
        if row == 1:
            ws_model.write_formula(row, 5, '=Inputs!$B$14 * Inputs!$B$6', num_fmt)
        else:
            ws_model.write_formula(row, 5, f'=V{r-1}', num_fmt)
            
        # G: Cum Discharge Today
        if row == 1:
            ws_model.write_formula(row, 6, '=0', num_fmt)
        else:
            ws_model.write_formula(row, 6, f'=IF(INT(A{r})=INT(A{r-1}), G{r-1} + Q{r-1}, 0)', num_fmt)
            
        # H: Max Charge MW
        ws_model.write_formula(row, 7, f'=MIN(Inputs!$B$2, (Inputs!$B$14 - F{r})/Inputs!$B$15)', num_fmt)
        # I: Max Discharge MW
        ws_model.write_formula(row, 8, f'=MAX(MIN(Inputs!$B$2, F{r}*Inputs!$B$16, Inputs!$B$14*Inputs!$B$4 - G{r}), 0)', num_fmt)
        # J: Max Reg MW (with Participation factor)
        ws_model.write_formula(row, 9, f'=MAX(MIN(Inputs!$B$2, F{r}*Inputs!$B$16, (Inputs!$B$14-F{r})/Inputs!$B$15)*Inputs!$B$9, 0)', num_fmt)
        
        # K: Expected Charge $ Total
        # Margin = (Forward_Avg * RTE) - LMP - Arb_Spread. Mul by Max_Charge_MW
        ws_model.write_formula(row, 10, f'=MAX(E{r}*Inputs!$B$5 - B{r} - Inputs!$B$8, 0) * H{r}', num_fmt)
        
        # L: Expected Discharge $ Total
        # Priorty Filter: IF(LMP > Daily_Avg * Multiplier). Margin = LMP - (ForwardAvg/RTE) - Arb_Spread.
        ws_model.write_formula(row, 11, f'=IF(B{r} >= D{r}*Inputs!$B$10, MAX(B{r} - E{r}/Inputs!$B$5 - Inputs!$B$8, 0) * I{r}, 0)', num_fmt)
        
        # M: Expected Reg $ Total
        ws_model.write_formula(row, 12, f'=C{r} * J{r}', num_fmt)
        
        # N: Suggested Mode
        ws_model.write_formula(row, 13, f'=IF(MAX(K{r}, L{r}, M{r})<=0, "Idle", IF(AND(M{r}>=K{r}, M{r}>=L{r}), "Reg", IF(L{r}>=K{r}, "Discharge", "Charge")))')
        
        # O: Final Mode (Min 2 Hours rule)
        if row <= 2:
            ws_model.write_formula(row, 14, f'=N{r}')
        else:
            # If t-1 <> t-2, we must hold t-1 into t.
            ws_model.write_formula(row, 14, f'=IF(O{r-1}<>O{r-2}, O{r-1}, N{r})')
            
        # P: Charge MW
        ws_model.write_formula(row, 15, f'=IF(O{r}="Charge", H{r}, 0)', num_fmt)
        # Q: Discharge MW
        ws_model.write_formula(row, 16, f'=IF(O{r}="Discharge", I{r}, 0)', num_fmt)
        # R: Reg MW
        ws_model.write_formula(row, 17, f'=IF(O{r}="Reg", J{r}, 0)', num_fmt)
        
        # S: Energy Rev
        ws_model.write_formula(row, 18, f'=(Q{r} - P{r}) * B{r}', num_fmt)
        # T: Reg Rev
        ws_model.write_formula(row, 19, f'=R{r} * C{r}', num_fmt)
        
        # U: Reg SoC Drift MWh
        ws_model.write_formula(row, 20, f'=IF(O{r}="Reg", R{r} * Inputs!$B$7, 0)', num_fmt)
        
        # V: Ending SoC
        ws_model.write_formula(row, 21, f'=MAX(F{r} + P{r}*Inputs!$B$15 - (Q{r}/Inputs!$B$16) - U{r}, 0)', num_fmt)

    # --- 3. Summary Worksheet ---
    ws_summary = workbook.add_worksheet('Summary')
    ws_summary.set_column('A:A', 25)
    ws_summary.set_column('B:B', 20)
    
    ws_summary.write('A1', 'Metric', header_fmt)
    ws_summary.write('B1', 'Total Value', header_fmt)
    ws_summary.write('A2', 'Total Energy Revenue ($)')
    ws_summary.write_formula('B2', '=SUM(Hourly_Model!S:S)', num_fmt)
    ws_summary.write('A3', 'Total Reg Revenue ($)')
    ws_summary.write_formula('B3', '=SUM(Hourly_Model!T:T)', num_fmt)
    ws_summary.write('A4', 'Total Revenue ($)')
    ws_summary.write_formula('B4', '=B2+B3', num_fmt)
    ws_summary.write('A5', 'Total Discharge (MWh)')
    ws_summary.write_formula('B5', '=SUM(Hourly_Model!Q:Q)', num_fmt)
    ws_summary.write('A6', 'Cycles / Year')
    ws_summary.write_formula('B6', '=B5/Inputs!$B$14', num_fmt)

    workbook.close()
    print(f"Successfully generated {filename}!")

if __name__ == '__main__':
    create_formula_linked_excel()
