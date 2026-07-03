# create_dashboard_button.py
import xlwings as xw
import os

def create_button(sheet, cell_name, caption, macro_name):
    # Determine the coordinates based on the cell position
    cell = sheet.range(cell_name)
    left = cell.left + 5
    top = cell.top + 5
    width = 120
    height = 30
    
    # Delete existing button if it exists
    for button in sheet.api.Buttons():
        if button.Caption == caption:
            button.Delete()
            
    print(f"Creating button '{caption}' in sheet '{sheet.name}' at cell {cell_name}...")
    btn = sheet.api.Buttons().Add(left, top, width, height)
    btn.Caption = caption
    btn.OnAction = macro_name
    
    # Set premium font styles
    btn.Font.Name = "Segoe UI"
    btn.Font.Bold = True
    btn.Font.Size = 10
    
def main():
    file_path = r"c:\Users\abhishek\Desktop\Financial models\FM deliverable\BESS Revenue Optimization\260511-FM-SanAngelo_(Solar+BESS)_V1_SV_AK.xlsb"
    abs_path = os.path.abspath(file_path)
    
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    
    try:
        print(f"Opening workbook to add buttons: {abs_path}")
        wb = app.books.open(abs_path)
        
        # 1. Unprotect Dashboard sheet, add button, protect back
        sheet_dash = wb.sheets["Dashboard"]
        sheet_dash.api.Unprotect()
        # Place next to other controls, e.g. at cell N6
        create_button(sheet_dash, "N6", "Optimize BESS", "Run_BESS_Optimization")
        sheet_dash.api.Protect()
        
        # 2. Add button to Input sheet
        sheet_input = wb.sheets["Input"]
        sheet_input.api.Unprotect()
        # Place next to row 500 configuration header
        create_button(sheet_input, "E500", "Optimize BESS", "Run_BESS_Optimization")
        sheet_input.api.Protect()
        
        wb.save()
        print("Buttons created and macro assigned successfully!")
        wb.close()
    except Exception as e:
        print(f"Error: {e}")
    finally:
        app.quit()

if __name__ == "__main__":
    main()
