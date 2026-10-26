# excel_dashboard_generator.py
"""
Production-Grade Formula-Linked Excel Exporter with Executive Dashboard for BESS Optimization
Generates an interactive, audit-ready Excel workbook with:
  1. 'Dashboard': Executive KPI scorecards, Revenue Stack Table, Monthly Breakdown with live SUMIFS.
  2. '8760_Dispatch_Results': Hourly dispatch with LIVE Excel formulas in all revenue and cost columns.
  3. 'Inputs': Named/referenced system & financial assumptions.
  4. 'Tranche_Settings': User-defined tranche parameters.
"""

import io
import pandas as pd
import numpy as np
import xlsxwriter

def export_bess_dashboard_excel(
    df_results: pd.DataFrame,
    metrics: dict,
    power_mw: float,
    duration_hr: float,
    rte: float,
    capacity_price_mw_day: float,
    elcc_factor: float,
    deg_cost: float,
    mileage_factor: float,
    tranches: list = None,
    market_name: str = "PJM"
) -> bytes:
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    
    # ---------------------------------------------------------
    # STYLES & FORMATS
    # ---------------------------------------------------------
    title_fmt = workbook.add_format({'bold': True, 'font_size': 15, 'font_color': '#1E293B'})
    subtitle_fmt = workbook.add_format({'font_size': 10, 'font_color': '#64748B'})
    
    header_navy = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': '#1E293B',
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    header_blue = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': '#0284C7',
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    header_emerald = workbook.add_format({
        'bold': True, 'font_color': 'white', 'bg_color': '#059669',
        'border': 1, 'align': 'center', 'valign': 'vcenter'
    })
    
    kpi_card_lbl = workbook.add_format({
        'font_size': 8, 'font_color': '#475569', 'bg_color': '#F1F5F9',
        'border': 1, 'align': 'center', 'bold': True
    })
    kpi_card_val = workbook.add_format({
        'font_size': 13, 'bold': True, 'font_color': '#0F172A', 'bg_color': '#F8FAFC',
        'border': 1, 'align': 'center', 'num_format': '$#,##0'
    })
    kpi_card_val_num = workbook.add_format({
        'font_size': 13, 'bold': True, 'font_color': '#0F172A', 'bg_color': '#F8FAFC',
        'border': 1, 'align': 'center', 'num_format': '#,##0.00'
    })
    
    currency_fmt = workbook.add_format({'num_format': '$#,##0.00', 'border': 1})
    currency_bold = workbook.add_format({'bold': True, 'num_format': '$#,##0.00', 'border': 1, 'bg_color': '#F8FAFC'})
    pct_fmt = workbook.add_format({'num_format': '0.0%', 'border': 1})
    num_fmt = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
    date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm', 'border': 1})
    text_fmt = workbook.add_format({'border': 1})
    bold_fmt = workbook.add_format({'bold': True, 'border': 1})

    # =========================================================
    # 1. INPUTS WORKSHEET
    # =========================================================
    ws_in = workbook.add_worksheet('Inputs')
    ws_in.set_column('A:A', 28)
    ws_in.set_column('B:B', 16)
    ws_in.set_column('C:C', 50)
    
    ws_in.write('A1', 'System Parameter', header_navy)
    ws_in.write('B1', 'Value', header_navy)
    ws_in.write('C1', 'Description', header_navy)
    
    inputs_data = [
        ('Power_MW', power_mw, 'Rated Inverter Power Capacity (MW)'),
        ('Duration_Hr', duration_hr, 'Storage Duration (Hours)'),
        ('Energy_MWh', power_mw * duration_hr, 'Nameplate Energy Capacity (MWh)'),
        ('Capacity_Price_MW_Day', capacity_price_mw_day, 'PJM RPM / Capacity Price ($/MW-day)'),
        ('ELCC_Factor', elcc_factor, 'Marginal ELCC Capacity Derate Factor (0.0 to 1.0)'),
        ('Degradation_Cost_MWh', deg_cost, 'Cell degradation cost per MWh throughput ($/MWh)'),
        ('Mileage_Factor', mileage_factor, 'Fraction of regulation capacity translating to cycle wear'),
        ('RTE', rte, 'Round-Trip AC-AC Efficiency'),
        ('Eff_C', float(np.sqrt(rte)), 'Square root of RTE (Charging efficiency)'),
        ('Eff_D', float(np.sqrt(rte)), 'Square root of RTE (Discharging efficiency)'),
    ]
    
    for idx, (param, val, desc) in enumerate(inputs_data, start=2):
        ws_in.write(f'A{idx}', param, text_fmt)
        if isinstance(val, (int, float)):
            ws_in.write_number(f'B{idx}', float(val), num_fmt)
        else:
            ws_in.write(f'B{idx}', str(val), text_fmt)
        ws_in.write(f'C{idx}', desc, text_fmt)

    # =========================================================
    # 2. 8760 DISPATCH RESULTS WORKSHEET (LIVE FORMULAS)
    # =========================================================
    ws_disp = workbook.add_worksheet('8760_Dispatch_Results')
    num_rows = len(df_results)
    
    # Identify dynamic tranche columns
    has_multi_tranches = bool(tranches and len(tranches) > 1)
    if has_multi_tranches:
        tranche_cols = [f"{t['name']}_MW" for t in tranches if f"{t['name']}_MW" in df_results.columns]
        tranche_rev_cols = [f"{t['name']}_Revenue" for t in tranches if f"{t['name']}_MW" in df_results.columns]
    else:
        tranche_cols = []
        tranche_rev_cols = []

    col_idx = 0
    header_col_map = {}
    
    cols_order = ['timestamp', 'LMP', 'Reg_Price', 'Price_SYNCH', 'Charge_MW', 'Discharge_MW']
    if has_multi_tranches:
        cols_order += tranche_cols
    cols_order.append('Total_Reg_MW')
    cols_order += ['Synch_MW', 'SOC_MWh', 'SOC_Pct', 'Energy_Revenue']
    if has_multi_tranches:
        cols_order += tranche_rev_cols
    cols_order += ['Regulation_Revenue', 'SYNCH_Revenue', 'Capacity_Revenue', 'Total_Degradation_Cost', 'Net_Merchant_Revenue', 'Operating_Mode']
    
    for c in cols_order:
        header_col_map[c] = col_idx
        col_letter = xlsxwriter.utility.xl_col_to_name(col_idx)
        ws_disp.write(0, col_idx, c, header_navy)
        ws_disp.set_column(f'{col_letter}:{col_letter}', 15)
        col_idx += 1
        
    ws_disp.set_column('A:A', 20) # Timestamp column
    
    # Pre-extract column letters
    col_lmp = xlsxwriter.utility.xl_col_to_name(header_col_map['LMP'])
    col_reg_p = xlsxwriter.utility.xl_col_to_name(header_col_map['Reg_Price'])
    col_synch_p = xlsxwriter.utility.xl_col_to_name(header_col_map['Price_SYNCH'])
    col_c = xlsxwriter.utility.xl_col_to_name(header_col_map['Charge_MW'])
    col_d = xlsxwriter.utility.xl_col_to_name(header_col_map['Discharge_MW'])
    col_soc_mwh = xlsxwriter.utility.xl_col_to_name(header_col_map['SOC_MWh'])
    col_synch = xlsxwriter.utility.xl_col_to_name(header_col_map['Synch_MW'])
    col_tot_reg = xlsxwriter.utility.xl_col_to_name(header_col_map['Total_Reg_MW'])
    
    for i in range(num_rows):
        r = i + 2 # 1-based Excel row
        
        # Raw Data Extraction
        ts_val = df_results['timestamp'].iloc[i]
        lmp_val = float(df_results['LMP'].iloc[i]) if 'LMP' in df_results.columns else 0.0
        
        if 'Reg_Price' in df_results.columns:
            reg_p_val = float(df_results['Reg_Price'].iloc[i])
        elif 'Reg_Effective_Price' in df_results.columns:
            reg_p_val = float(df_results['Reg_Effective_Price'].iloc[i])
        elif 'RMCCP_D' in df_results.columns:
            rmpcp_d = float(df_results['RMPCP_D'].iloc[i]) if 'RMPCP_D' in df_results.columns else 2.5
            reg_p_val = float(df_results['RMCCP_D'].iloc[i] * 0.95 + rmpcp_d * 3.2 * 0.95)
        elif 'RMCCP' in df_results.columns:
            mileage = float(df_results['Mileage'].iloc[i]) if 'Mileage' in df_results.columns else 3.2
            rmpcp = float(df_results['RMPCP'].iloc[i]) if 'RMPCP' in df_results.columns else 2.5
            reg_p_val = float(df_results['RMCCP'].iloc[i] * 0.95 + rmpcp * mileage * 0.95)
        else:
            reg_p_val = 30.0
            
        if 'Price_SYNCH' in df_results.columns:
            synch_p_val = float(df_results['Price_SYNCH'].iloc[i])
        elif 'Price_Spin' in df_results.columns:
            synch_p_val = float(df_results['Price_Spin'].iloc[i])
        else:
            synch_p_val = 4.0
        
        if 'Charge_MW' in df_results.columns:
            c_val = float(df_results['Charge_MW'].iloc[i])
        elif 'charge_mw' in df_results.columns:
            c_val = float(df_results['charge_mw'].iloc[i])
        else:
            c_val = 0.0
            
        if 'Discharge_MW' in df_results.columns:
            d_val = float(df_results['Discharge_MW'].iloc[i])
        elif 'discharge_mw' in df_results.columns:
            d_val = float(df_results['discharge_mw'].iloc[i])
        else:
            d_val = 0.0
            
        ws_disp.write_datetime(i+1, header_col_map['timestamp'], ts_val, date_fmt)
        ws_disp.write_number(i+1, header_col_map['LMP'], lmp_val, currency_fmt)
        ws_disp.write_number(i+1, header_col_map['Reg_Price'], reg_p_val, currency_fmt)
        ws_disp.write_number(i+1, header_col_map['Price_SYNCH'], synch_p_val, currency_fmt)
        ws_disp.write_number(i+1, header_col_map['Charge_MW'], c_val, num_fmt)
        ws_disp.write_number(i+1, header_col_map['Discharge_MW'], d_val, num_fmt)
        
        # Tranche Cleared MWs
        if has_multi_tranches:
            for t_col in tranche_cols:
                t_val = float(df_results[t_col].iloc[i]) if t_col in df_results.columns else 0.0
                ws_disp.write_number(i+1, header_col_map[t_col], t_val, num_fmt)
            first_t_col = xlsxwriter.utility.xl_col_to_name(header_col_map[tranche_cols[0]])
            last_t_col = xlsxwriter.utility.xl_col_to_name(header_col_map[tranche_cols[-1]])
            ws_disp.write_formula(i+1, header_col_map['Total_Reg_MW'], f'=SUM({first_t_col}{r}:{last_t_col}{r})', num_fmt)
        else:
            tot_reg_val = float(df_results['Total_Reg_MW'].iloc[i]) if 'Total_Reg_MW' in df_results.columns else (float(df_results['Regulation_MW'].iloc[i]) if 'Regulation_MW' in df_results.columns else 0.0)
            ws_disp.write_number(i+1, header_col_map['Total_Reg_MW'], tot_reg_val, num_fmt)
            
        if 'Synch_MW' in df_results.columns:
            synch_val = float(df_results['Synch_MW'].iloc[i])
        elif 'SYNCH_MW' in df_results.columns:
            synch_val = float(df_results['SYNCH_MW'].iloc[i])
        elif 'Spin_MW' in df_results.columns:
            synch_val = float(df_results['Spin_MW'].iloc[i])
        else:
            synch_val = 0.0
        ws_disp.write_number(i+1, header_col_map['Synch_MW'], synch_val, num_fmt)
        
        if 'SOC_MWh' in df_results.columns:
            soc_val = float(df_results['SOC_MWh'].iloc[i])
        elif 'soc_mwh' in df_results.columns:
            soc_val = float(df_results['soc_mwh'].iloc[i])
        else:
            soc_val = 0.0
        ws_disp.write_number(i+1, header_col_map['SOC_MWh'], soc_val, num_fmt)
        
        # LIVE FORMULA: SOC Pct
        ws_disp.write_formula(i+1, header_col_map['SOC_Pct'], f'=({col_soc_mwh}{r}/Inputs!$B$4)*100', num_fmt, value=(soc_val / (power_mw * duration_hr) * 100.0) if (power_mw * duration_hr) > 0 else 0.0)
        
        # LIVE FORMULA: Energy Revenue = (Discharge - Charge) * LMP
        ene_val = float(df_results['Energy_Revenue'].iloc[i]) if 'Energy_Revenue' in df_results.columns else ((d_val - c_val) * lmp_val)
        ws_disp.write_formula(i+1, header_col_map['Energy_Revenue'], f'=({col_d}{r}-{col_c}{r})*{col_lmp}{r}', currency_fmt, value=ene_val)
        
        # LIVE FORMULA: Regulation Revenue
        reg_val = float(df_results['Regulation_Revenue'].iloc[i]) if 'Regulation_Revenue' in df_results.columns else (tot_reg_val * reg_p_val)
        if has_multi_tranches:
            for t_col, t_rev_col in zip(tranche_cols, tranche_rev_cols):
                t_col_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map[t_col])
                t_rev_val = float(df_results[t_rev_col].iloc[i]) if t_rev_col in df_results.columns else (float(df_results[t_col].iloc[i]) * reg_p_val if t_col in df_results.columns else 0.0)
                ws_disp.write_formula(i+1, header_col_map[t_rev_col], f'={t_col_ltr}{r}*{col_reg_p}{r}', currency_fmt, value=t_rev_val)
            first_rev_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map[tranche_rev_cols[0]])
            last_rev_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map[tranche_rev_cols[-1]])
            ws_disp.write_formula(i+1, header_col_map['Regulation_Revenue'], f'=SUM({first_rev_ltr}{r}:{last_rev_ltr}{r})', currency_fmt, value=reg_val)
        else:
            ws_disp.write_formula(i+1, header_col_map['Regulation_Revenue'], f'={col_tot_reg}{r}*{col_reg_p}{r}', currency_fmt, value=reg_val)
            
        # LIVE FORMULA: Synch Revenue
        if 'SYNCH_Revenue' in df_results.columns:
            synch_rev_val = float(df_results['SYNCH_Revenue'].iloc[i])
        elif 'Spin_Revenue' in df_results.columns:
            synch_rev_val = float(df_results['Spin_Revenue'].iloc[i])
        else:
            synch_rev_val = synch_val * synch_p_val
        ws_disp.write_formula(i+1, header_col_map['SYNCH_Revenue'], f'={col_synch}{r}*{col_synch_p}{r}', currency_fmt, value=synch_rev_val)
        
        # LIVE FORMULA: Capacity Revenue
        cap_rev_val = float(df_results['Capacity_Revenue'].iloc[i]) if 'Capacity_Revenue' in df_results.columns else ((power_mw * elcc_factor * capacity_price_mw_day) / 24.0)
        ws_disp.write_formula(i+1, header_col_map['Capacity_Revenue'], '=(Inputs!$B$2*Inputs!$B$6*Inputs!$B$5)/24', currency_fmt, value=cap_rev_val)
        
        # LIVE FORMULA: Degradation Cost
        deg_val = float(df_results['Total_Degradation_Cost'].iloc[i]) if 'Total_Degradation_Cost' in df_results.columns else 0.0
        ws_disp.write_formula(i+1, header_col_map['Total_Degradation_Cost'], f'=({col_c}{r}*Inputs!$B$10+{col_d}{r}/Inputs!$B$11)*Inputs!$B$7+({col_tot_reg}{r}*Inputs!$B$7*Inputs!$B$8)', currency_fmt, value=deg_val)
        
        # LIVE FORMULA: Net Merchant Revenue
        col_e_rev = xlsxwriter.utility.xl_col_to_name(header_col_map['Energy_Revenue'])
        col_r_rev = xlsxwriter.utility.xl_col_to_name(header_col_map['Regulation_Revenue'])
        col_s_rev = xlsxwriter.utility.xl_col_to_name(header_col_map['SYNCH_Revenue'])
        col_cap_rev = xlsxwriter.utility.xl_col_to_name(header_col_map['Capacity_Revenue'])
        col_deg = xlsxwriter.utility.xl_col_to_name(header_col_map['Total_Degradation_Cost'])
        # Net revenue must strictly equal the sum of component formula columns
        net_val = round(ene_val + reg_val + synch_rev_val + cap_rev_val - deg_val, 4)
        
        ws_disp.write_formula(i+1, header_col_map['Net_Merchant_Revenue'], f'={col_e_rev}{r}+{col_r_rev}{r}+{col_s_rev}{r}+{col_cap_rev}{r}-{col_deg}{r}', currency_fmt, value=net_val)
        
        # Mode string
        if 'Operating_Mode' in df_results.columns:
            mode_str = str(df_results['Operating_Mode'].iloc[i])
        elif 'decision' in df_results.columns:
            mode_str = str(df_results['decision'].iloc[i])
        else:
            mode_str = 'Active'
        ws_disp.write(i+1, header_col_map['Operating_Mode'], mode_str, text_fmt)

    # =========================================================
    # 3. EXECUTIVE DASHBOARD WORKSHEET (LIVE FORMULAS)
    # =========================================================
    ws_dash = workbook.add_worksheet('Dashboard')
    ws_dash.set_column('A:A', 4)
    ws_dash.set_column('B:B', 32)
    ws_dash.set_column('C:C', 20)
    ws_dash.set_column('D:D', 20)
    ws_dash.set_column('E:E', 20)
    ws_dash.set_column('F:F', 20)
    ws_dash.set_column('G:G', 20)

    # Title & Header
    ws_dash.write('B2', f'⚡ {market_name} BESS Optimization & Revenue Stacking Dashboard', title_fmt)
    ws_dash.write('B3', f'Asset: {power_mw:.0f} MW / {power_mw*duration_hr:.0f} MWh | Market: {market_name} | Simulation: {num_rows} Hours', subtitle_fmt)

    # Top KPI Cards
    col_net_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Net_Merchant_Revenue'])
    col_reg_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Regulation_Revenue'])
    col_ene_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Energy_Revenue'])
    col_cap_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Capacity_Revenue'])
    col_syn_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['SYNCH_Revenue'])
    col_deg_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Total_Degradation_Cost'])
    col_d_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Discharge_MW'])
    col_c_ltr = xlsxwriter.utility.xl_col_to_name(header_col_map['Charge_MW'])

    reg_total = float(metrics.get('Regulation Revenue ($)', 0.0))
    cap_total = float(metrics.get('Static Capacity Revenue ($)', 0.0))
    ene_total = float(metrics.get('Energy Arbitrage Revenue ($)', 0.0))
    syn_total = float(metrics.get('SYNCH Revenue ($)', 0.0))
    deg_total = float(metrics.get('Degradation Expense ($)', 0.0))
    gross_total = reg_total + cap_total + ene_total + syn_total
    net_operating_total = gross_total - deg_total

    ws_dash.write('B5', 'NET MERCHANT REVENUE', kpi_card_lbl)
    ws_dash.write_formula('B6', '=C19', kpi_card_val, value=net_operating_total)

    ws_dash.write('C5', 'REGULATION REVENUE', kpi_card_lbl)
    ws_dash.write_formula('C6', f"=SUM('8760_Dispatch_Results'!{col_reg_ltr}2:{col_reg_ltr}{num_rows+1})", kpi_card_val, value=reg_total)

    ws_dash.write('D5', 'ENERGY ARBITRAGE', kpi_card_lbl)
    ws_dash.write_formula('D6', f"=SUM('8760_Dispatch_Results'!{col_ene_ltr}2:{col_ene_ltr}{num_rows+1})", kpi_card_val, value=ene_total)

    ws_dash.write('E5', 'CAPACITY REVENUE (RPM)', kpi_card_lbl)
    ws_dash.write_formula('E6', f"=SUM('8760_Dispatch_Results'!{col_cap_ltr}2:{col_cap_ltr}{num_rows+1})", kpi_card_val, value=cap_total)

    ws_dash.write('F5', 'SYNCH RESERVES', kpi_card_lbl)
    ws_dash.write_formula('F6', f"=SUM('8760_Dispatch_Results'!{col_syn_ltr}2:{col_syn_ltr}{num_rows+1})", kpi_card_val, value=syn_total)

    ws_dash.write('G5', 'DEGRADATION EXPENSE', kpi_card_lbl)
    ws_dash.write_formula('G6', f"=SUM('8760_Dispatch_Results'!{col_deg_ltr}2:{col_deg_ltr}{num_rows+1})", kpi_card_val, value=deg_total)

    # Operational Cards
    tot_d_mwh = float(metrics.get('Total Discharged (MWh)', (df_results['discharge_mw'].sum() if 'discharge_mw' in df_results.columns else 0.0)))
    tot_c_mwh = float(metrics.get('Total Charged (MWh)', (df_results['charge_mw'].sum() if 'charge_mw' in df_results.columns else 0.0)))
    efc_val = float(metrics.get('Equivalent Full Cycles (EFC)', tot_d_mwh / (power_mw * duration_hr) if (power_mw * duration_hr) > 0 else 0.0))
    rte_val = float(metrics.get('Achieved Round-Trip Efficiency', tot_d_mwh / tot_c_mwh if tot_c_mwh > 0 else 0.0))

    ws_dash.write('B8', 'EQUIVALENT FULL CYCLES', kpi_card_lbl)
    ws_dash.write_formula('B9', f"=SUM('8760_Dispatch_Results'!{col_d_ltr}2:{col_d_ltr}{num_rows+1})/Inputs!$B$4", kpi_card_val_num, value=efc_val)

    ws_dash.write('C8', 'TOTAL DISCHARGED (MWH)', kpi_card_lbl)
    ws_dash.write_formula('C9', f"=SUM('8760_Dispatch_Results'!{col_d_ltr}2:{col_d_ltr}{num_rows+1})", kpi_card_val_num, value=tot_d_mwh)

    ws_dash.write('D8', 'TOTAL CHARGED (MWH)', kpi_card_lbl)
    ws_dash.write_formula('D9', f"=SUM('8760_Dispatch_Results'!{col_c_ltr}2:{col_c_ltr}{num_rows+1})", kpi_card_val_num, value=tot_c_mwh)

    ws_dash.write('E8', 'ACHIEVED ROUND-TRIP EFF.', kpi_card_lbl)
    ws_dash.write_formula('E9', f'=IF(D9>0, C9/D9, 0)', workbook.add_format({'font_size': 13, 'bold': True, 'font_color': '#0F172A', 'bg_color': '#F8FAFC', 'border': 1, 'align': 'center', 'num_format': '0.0%'}), value=rte_val)

    # TABLE 1: REVENUE STACK SUMMARY TABLE (Rows 12-19)
    ws_dash.write('B12', 'Revenue Stream', header_blue)
    ws_dash.write('C12', 'Annual Revenue ($)', header_blue)
    ws_dash.write('D12', 'Share of Gross (%)', header_blue)
    
    ws_dash.write('B13', 'Frequency Regulation', text_fmt)
    ws_dash.write_formula('C13', '=C6', currency_fmt, value=float(metrics.get('Regulation Revenue ($)', 0.0)))
    ws_dash.write_formula('D13', '=C13/$C$17', pct_fmt)

    ws_dash.write('B14', 'RPM Capacity Market', text_fmt)
    ws_dash.write_formula('C14', '=E6', currency_fmt, value=float(metrics.get('Static Capacity Revenue ($)', 0.0)))
    ws_dash.write_formula('D14', '=C14/$C$17', pct_fmt)

    ws_dash.write('B15', 'Energy Arbitrage', text_fmt)
    ws_dash.write_formula('C15', '=D6', currency_fmt, value=float(metrics.get('Energy Arbitrage Revenue ($)', 0.0)))
    ws_dash.write_formula('D15', '=C15/$C$17', pct_fmt)

    ws_dash.write('B16', 'Synchronized Reserves', text_fmt)
    ws_dash.write_formula('C16', '=F6', currency_fmt, value=float(metrics.get('SYNCH Revenue ($)', 0.0)))
    ws_dash.write_formula('D16', '=C16/$C$17', pct_fmt)
        
    ws_dash.write('B17', 'Total Gross Merchant Revenue', bold_fmt)
    ws_dash.write_formula('C17', '=SUM(C13:C16)', currency_bold, value=gross_total)
    ws_dash.write_formula('D17', '=SUM(D13:D16)', pct_fmt)

    ws_dash.write('B18', 'Less: Battery Degradation Expense', text_fmt)
    ws_dash.write_formula('C18', '=-G6', currency_fmt, value=-deg_total)
    ws_dash.write('D18', '-', text_fmt)

    ws_dash.write('B19', 'NET MERCHANT OPERATING REVENUE', bold_fmt)
    ws_dash.write_formula('C19', '=C17+C18', currency_bold, value=net_operating_total)
    ws_dash.write('D19', '-', text_fmt)

    # TABLE 2: MONTHLY BREAKDOWN TABLE (Rows 22-35)
    ws_dash.write('B22', 'Month', header_emerald)
    ws_dash.write('C22', 'Energy ($)', header_emerald)
    ws_dash.write('D22', 'Regulation ($)', header_emerald)
    ws_dash.write('E22', 'Capacity ($)', header_emerald)
    ws_dash.write('F22', 'Reserves ($)', header_emerald)
    ws_dash.write('G22', 'Total Net ($)', header_emerald)

    months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    year_val = df_results['timestamp'].iloc[0].year if len(df_results) > 0 else 2026
    
    for m_idx, m_name in enumerate(months, start=1):
        r_m = 22 + m_idx
        if m_idx == 12:
            end_year = year_val + 1
            end_m = 1
        else:
            end_year = year_val
            end_m = m_idx + 1
            
        ws_dash.write(f'B{r_m}', m_name, text_fmt)
        ws_dash.write_formula(f'C{r_m}', f"=SUMIFS('8760_Dispatch_Results'!{col_ene_ltr}:{col_ene_ltr}, '8760_Dispatch_Results'!A:A, \">=\"&DATE({year_val},{m_idx},1), '8760_Dispatch_Results'!A:A, \"<\"&DATE({end_year},{end_m},1))", currency_fmt)
        ws_dash.write_formula(f'D{r_m}', f"=SUMIFS('8760_Dispatch_Results'!{col_reg_ltr}:{col_reg_ltr}, '8760_Dispatch_Results'!A:A, \">=\"&DATE({year_val},{m_idx},1), '8760_Dispatch_Results'!A:A, \"<\"&DATE({end_year},{end_m},1))", currency_fmt)
        ws_dash.write_formula(f'E{r_m}', f"=SUMIFS('8760_Dispatch_Results'!{col_cap_ltr}:{col_cap_ltr}, '8760_Dispatch_Results'!A:A, \">=\"&DATE({year_val},{m_idx},1), '8760_Dispatch_Results'!A:A, \"<\"&DATE({end_year},{end_m},1))", currency_fmt)
        ws_dash.write_formula(f'F{r_m}', f"=SUMIFS('8760_Dispatch_Results'!{col_syn_ltr}:{col_syn_ltr}, '8760_Dispatch_Results'!A:A, \">=\"&DATE({year_val},{m_idx},1), '8760_Dispatch_Results'!A:A, \"<\"&DATE({end_year},{end_m},1))", currency_fmt)
        ws_dash.write_formula(f'G{r_m}', f"=SUMIFS('8760_Dispatch_Results'!{col_net_ltr}:{col_net_ltr}, '8760_Dispatch_Results'!A:A, \">=\"&DATE({year_val},{m_idx},1), '8760_Dispatch_Results'!A:A, \"<\"&DATE({end_year},{end_m},1))", currency_bold)

    ws_dash.write('B35', 'Full Year Total', bold_fmt)
    ws_dash.write_formula('C35', '=SUM(C23:C34)', currency_bold)
    ws_dash.write_formula('D35', '=SUM(D23:D34)', currency_bold)
    ws_dash.write_formula('E35', '=SUM(E23:E34)', currency_bold)
    ws_dash.write_formula('F35', '=SUM(F23:F34)', currency_bold)
    ws_dash.write_formula('G35', '=SUM(G23:G34)', currency_bold)

    # =========================================================
    # 4. TRANCHE SETTINGS WORKSHEET
    # =========================================================
    if tranches:
        ws_tr = workbook.add_worksheet('Tranche_Settings')
        ws_tr.set_column('A:C', 28)
        ws_tr.write('A1', 'Tranche Name', header_emerald)
        ws_tr.write('B1', 'Capacity (MW)', header_emerald)
        ws_tr.write('C1', 'Hurdle Rate ($/MW)', header_emerald)
        for t_idx, tr_item in enumerate(tranches, start=2):
            ws_tr.write(f'A{t_idx}', tr_item['name'], text_fmt)
            ws_tr.write_number(f'B{t_idx}', float(tr_item['mw']), num_fmt)
            ws_tr.write_number(f'C{t_idx}', float(tr_item['hurdle_rate']), currency_fmt)

    workbook.close()
    return output.getvalue()
