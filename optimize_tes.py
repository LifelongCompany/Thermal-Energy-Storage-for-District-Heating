import pandas as pd
import pulp
import numpy as np
import os

def optimize_tes():
    # --- 1. Data Loading & Parsing ---
    file_path = 'data/Typical_Days_Data.xlsx'
    print(f"Loading data from {file_path}...")

    # Load sheet
    df = pd.read_excel(file_path, sheet_name='Sheet1', header=None)

    # Slice Data
    # Rows 0-23: Hourly Load (MW)
    # Columns 1-4: Day Types
    # We'll use iloc. rows 0:24 (exclusive of 24), cols 1:5 (exclusive of 5)

    # Check headers (row 0 contains 'Hour', 'Interval_Cold', etc? No, row 0 in pandas is index 0)
    # Based on previous inspection:
    # Row 0 (Index 0) in the raw df seems to be the header row actually, based on 'Hour' being in col 0.
    # Let's re-verify:
    # inspect_data_header.py output:
    # Row 0: Hour, Interval_Cold ...
    # So actually row 0 is the header.
    # Rows 1-24 are the data for hours 0-23.
    # Row 25 (Index 25) is average T_amb (label in col 0 is 'average T_amb', values in cols 1-4)
    # Row 26 (Index 26) is T_in (label in col 0 is 'T_in', values in cols 1-4)

    # Let's reload with header=0 to be safer with column names
    df = pd.read_excel(file_path, sheet_name='Sheet1', header=0)

    # Now:
    # df has 26 rows (indices 0 to 25).
    # Rows 0-23 correspond to Hours 0-23.
    # Row 24 is 'average T_amb'.
    # Row 25 is 'T_in'.

    day_types = ['Interval_Cold', 'Interval_Mild', 'Maximum_heatingDay_Profile', 'Minimum_heatingDay_Profile']

    # Extract Hourly Loads (MW) -> Convert to kW for consistency?
    # Task says "Hourly Heat Load profiles (MW)".
    # CAPEX is 1500 E/kW.
    # Let's work in kW to match CAPEX units.
    # So Load_kW = Load_MW * 1000.

    hourly_loads_mw = df.iloc[0:24][day_types]
    hourly_loads_kw = hourly_loads_mw * 1000

    # Extract Scalars
    # Row 24 is T_amb
    t_amb_series = df.iloc[24][day_types]

    # Row 25 is T_in (Supply Temp)
    t_in_series = df.iloc[25][day_types]

    # Day Weights
    day_weights = {
        'Interval_Cold': 100,
        'Interval_Mild': 150,
        'Maximum_heatingDay_Profile': 10,
        'Minimum_heatingDay_Profile': 105
    }

    # --- 2. Physical Model Parameters ---

    # Return Temp fixed at 55 deg C
    T_return = 55.0

    cop_dict = {}
    density_dict = {} # kWh/m3

    print("\nCalculating Physical Parameters...")
    for day in day_types:
        T_supply = float(t_in_series[day])
        T_amb = float(t_amb_series[day])

        # COP Calculation
        # T_sink = (T_supply + T_return) / 2
        # Convert to Kelvin
        T_sink_C = (T_supply + T_return) / 2.0
        T_sink_K = T_sink_C + 273.15
        T_source_K = T_amb + 273.15

        # Lorentz Efficiency = 0.50
        # COP = 0.50 * T_sink / (T_sink - T_source)
        cop = 0.50 * T_sink_K / (T_sink_K - T_source_K)
        cop_dict[day] = cop

        # Storage Density Calculation
        # Density = 1000 * 4.18 * (T_supply - 55) / 3600  (kWh/m3)
        # Note: Delta T is T_supply - T_return
        density = 1000 * 4.18 * (T_supply - T_return) / 3600
        density_dict[day] = density

        print(f"Day: {day}, T_amb: {T_amb:.2f}, T_in: {T_supply:.2f}, COP: {cop:.2f}, Density: {density:.2f} kWh/m3")

    # --- 3. Economic Parameters ---

    # Annuity Factor
    r = 0.04
    n = 20
    annuity_factor = (r * (1 + r)**n) / ((1 + r)**n - 1)
    print(f"\nAnnuity Factor: {annuity_factor:.4f}")

    # Prices
    price_off_peak = 0.1846
    price_peak = 0.2461

    electricity_prices = []
    for h in range(24):
        # Off-Peak: 00:00-06:00 (0-5) and 12:00-14:00 (12-13)
        if (0 <= h < 6) or (12 <= h < 14):
            electricity_prices.append(price_off_peak)
        else:
            electricity_prices.append(price_peak)

    # CAPEX
    capex_hp_per_kw = 1500
    capex_tank_per_m3 = 1200

    # --- 4. Optimization Model (PuLP) ---
    print("\nBuilding Optimization Model...")

    prob = pulp.LpProblem("TES_Optimization", pulp.LpMinimize)

    # Variables
    # P_hp_max (kW)
    P_hp_max = pulp.LpVariable("P_hp_max", lowBound=0, cat='Continuous')

    # V_tank (m3)
    V_tank = pulp.LpVariable("V_tank", lowBound=0, cat='Continuous')

    # Operational Variables per Day and Hour
    # Q_hp[day][hour]
    # E_stored[day][hour] (State at END of hour)

    Q_hp = {}
    E_stored = {}

    for day in day_types:
        Q_hp[day] = {}
        E_stored[day] = {}
        for h in range(24):
            Q_hp[day][h] = pulp.LpVariable(f"Q_hp_{day}_{h}", lowBound=0, cat='Continuous')
            E_stored[day][h] = pulp.LpVariable(f"E_stored_{day}_{h}", lowBound=0, cat='Continuous')

    # Objective Function Terms

    # 1. Annualized CAPEX
    annualized_capex = (P_hp_max * capex_hp_per_kw + V_tank * capex_tank_per_m3) * annuity_factor

    # 2. Total OPEX
    total_opex = 0
    for day in day_types:
        daily_opex = 0
        for h in range(24):
            # Power Consumed = Q_hp / COP
            power_consumed = Q_hp[day][h] / cop_dict[day]
            cost = power_consumed * electricity_prices[h]
            daily_opex += cost

        total_opex += daily_opex * day_weights[day]

    prob += annualized_capex + total_opex, "Total_Annualized_Cost"

    # Constraints

    for day in day_types:
        density = density_dict[day]
        load_profile = hourly_loads_kw[day].values

        for h in range(24):
            # 1. Max HP Power
            prob += Q_hp[day][h] <= P_hp_max, f"Max_HP_Power_{day}_{h}"

            # 2. Max Storage Capacity
            # E_stored is in kWh. Max energy = V_tank * Density
            prob += E_stored[day][h] <= V_tank * density, f"Max_Storage_{day}_{h}"

            # 3. Energy Balance & Storage Dynamics
            # E_stored[h] = E_stored[h-1] + Q_hp[h] - Load[h]
            # For h=0: E_stored[0] = E_stored[23] + Q_hp[0] - Load[0] (Cyclic)

            load_val = load_profile[h]

            if h == 0:
                prob += E_stored[day][0] == E_stored[day][23] + Q_hp[day][0] - load_val, f"Balance_0_{day}"
            else:
                prob += E_stored[day][h] == E_stored[day][h-1] + Q_hp[day][h] - load_val, f"Balance_{h}_{day}"

    # Solve
    print("Solving...")
    # Using default solver (CBC)
    prob.solve()

    status = pulp.LpStatus[prob.status]
    print(f"Status: {status}")

    # --- 5. Output Results ---

    p_hp_opt_kw = pulp.value(P_hp_max)
    v_tank_opt = pulp.value(V_tank)
    total_cost = pulp.value(prob.objective)

    capex_val = pulp.value(annualized_capex)
    opex_val = pulp.value(total_opex)

    print("\n--- Results ---")
    print(f"Optimal Heat Pump Size: {p_hp_opt_kw/1000:.4f} MW")
    print(f"Optimal Tank Volume: {v_tank_opt:.2f} m3")
    print(f"Total Annual Cost: {total_cost/1000:.2f} k€")
    print(f"  - Annualized CAPEX: {capex_val/1000:.2f} k€")
    print(f"  - Total Annual OPEX: {opex_val/1000:.2f} k€")

    # Generate README
    readme_content = f"""# TES Optimization Results

## Optimal System Sizing
* **Heat Pump Capacity ($P_{{hp\\_max}}$):** {p_hp_opt_kw/1000:.4f} MW
* **Storage Tank Volume ($V_{{tank}}$):** {v_tank_opt:.2f} m³

## Economic Analysis
* **Total Annualized Cost:** {total_cost/1000:.2f} k€
    * **Annualized CAPEX:** {capex_val/1000:.2f} k€
    * **Annual OPEX:** {opex_val/1000:.2f} k€

## Operational Strategy Summary
The system minimizes costs by shifting heat production to off-peak electricity hours and storing it for peak hours.
* **Off-Peak Hours (Low Price):** 00:00-06:00, 12:00-14:00.
* **Peak Hours (High Price):** 06:00-12:00, 14:00-24:00.

The Heat Pump tends to run at higher capacity during off-peak times to charge the storage tank, which then discharges during peak times to satisfy the heat load, thereby avoiding expensive electricity.
"""

    with open("README.md", "w") as f:
        f.write(readme_content)

    print("\nREADME.md generated.")

if __name__ == "__main__":
    optimize_tes()
