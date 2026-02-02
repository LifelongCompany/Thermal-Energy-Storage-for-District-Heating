import pandas as pd
import pulp
import numpy as np
import os
import matplotlib.pyplot as plt
import seaborn as sns

class TESOptimizer:
    def __init__(self, file_path='data/Typical_Days_Data.xlsx'):
        self.file_path = file_path

        # Economic Parameters
        self.r = 0.04
        self.n = 20
        self.annuity_factor = (self.r * (1 + self.r)**self.n) / ((1 + self.r)**self.n - 1)
        self.price_off_peak = 0.1846
        self.price_peak = 0.2461
        self.capex_hp_per_kw = 1500
        self.capex_tank_per_m3 = 1200

        # Physical Parameters
        self.T_return = 55.0

        # Day Types and Weights
        self.day_types = ['Interval_Cold', 'Interval_Mild', 'Maximum_heatingDay_Profile', 'Minimum_heatingDay_Profile']
        self.day_weights = {
            'Interval_Cold': 100,
            'Interval_Mild': 150,
            'Maximum_heatingDay_Profile': 10,
            'Minimum_heatingDay_Profile': 105
        }

        # Data placeholders
        self.hourly_loads_kw = None
        self.t_amb_series = None
        self.t_in_series = None
        self.cop_dict = {}
        self.density_dict = {}
        self.electricity_prices = []

        # Optimization Results
        self.prob = None
        self.p_hp_opt_kw = 0
        self.v_tank_opt = 0
        self.total_cost = 0
        self.capex_val = 0
        self.opex_val = 0
        self.total_initial_capex = 0
        self.npc = 0
        self.Q_hp_results = {} # To store optimized values
        self.E_stored_results = {} # To store optimized values

        # Scenario Results
        self.results_no_storage = None
        self.results_with_storage = None

    def load_data(self):
        """Loads and parses data from the Excel file."""
        print(f"Loading data from {self.file_path}...")

        # Load sheet with header at row 0
        df = pd.read_excel(self.file_path, sheet_name='Sheet1', header=0)

        # Extract Hourly Loads (Rows 0-23) and convert MW to kW
        hourly_loads_mw = df.iloc[0:24][self.day_types]
        self.hourly_loads_kw = hourly_loads_mw * 1000

        # Extract Scalars (Row 24: T_amb, Row 25: T_in)
        self.t_amb_series = df.iloc[24][self.day_types]
        self.t_in_series = df.iloc[25][self.day_types]

        print("Data loaded successfully.")

    def calculate_parameters(self):
        """Calculates physical parameters (COP, Density) and electricity prices."""
        print("Calculating Physical Parameters...")

        for day in self.day_types:
            T_supply = float(self.t_in_series[day])
            T_amb = float(self.t_amb_series[day])

            # COP Calculation
            T_sink_C = (T_supply + self.T_return) / 2.0
            T_sink_K = T_sink_C + 273.15
            T_source_K = T_amb + 273.15

            # Lorentz Efficiency = 0.50
            cop = 0.50 * T_sink_K / (T_sink_K - T_source_K)
            self.cop_dict[day] = cop

            # Storage Density Calculation (kWh/m3)
            # Density = 1000 * 4.18 * DeltaT / 3600
            density = 1000 * 4.18 * (T_supply - self.T_return) / 3600
            self.density_dict[day] = density

            print(f"Day: {day}, T_amb: {T_amb:.2f}, T_in: {T_supply:.2f}, COP: {cop:.2f}, Density: {density:.2f} kWh/m3")

        # Electricity Prices Profile
        self.electricity_prices = []
        for h in range(24):
            # Off-Peak: 00:00-06:00 (0-5) and 12:00-14:00 (12-13)
            if (0 <= h < 6) or (12 <= h < 14):
                self.electricity_prices.append(self.price_off_peak)
            else:
                self.electricity_prices.append(self.price_peak)

    def build_and_solve(self, enable_storage=True):
        """
        Builds the PuLP optimization model and solves it.
        :param enable_storage: If False, enforces V_tank = 0.
        """
        scenario_name = "With Storage" if enable_storage else "No Storage"
        print(f"\nBuilding Optimization Model ({scenario_name})...")

        self.prob = pulp.LpProblem("TES_Optimization", pulp.LpMinimize)

        # Variables
        P_hp_max = pulp.LpVariable("P_hp_max", lowBound=0, cat='Continuous')
        V_tank = pulp.LpVariable("V_tank", lowBound=0, cat='Continuous')

        Q_hp = {}
        E_stored = {}

        for day in self.day_types:
            Q_hp[day] = {}
            E_stored[day] = {}
            for h in range(24):
                Q_hp[day][h] = pulp.LpVariable(f"Q_hp_{day}_{h}", lowBound=0, cat='Continuous')
                E_stored[day][h] = pulp.LpVariable(f"E_stored_{day}_{h}", lowBound=0, cat='Continuous')

        # Constraint: No Storage Scenario
        if not enable_storage:
            self.prob += V_tank == 0, "No_Storage_Constraint"

        # Objective Function
        annualized_capex = (P_hp_max * self.capex_hp_per_kw + V_tank * self.capex_tank_per_m3) * self.annuity_factor

        total_opex = 0
        for day in self.day_types:
            daily_opex = 0
            for h in range(24):
                # Power Consumed = Q_hp / COP
                power_consumed = Q_hp[day][h] * (1.0 / self.cop_dict[day])
                cost = power_consumed * self.electricity_prices[h]
                daily_opex += cost
            total_opex += daily_opex * self.day_weights[day]

        self.prob += annualized_capex + total_opex, "Total_Annualized_Cost"

        # Constraints
        for day in self.day_types:
            density = self.density_dict[day]
            load_profile = self.hourly_loads_kw[day].values

            for h in range(24):
                # 1. Max HP Power
                self.prob += Q_hp[day][h] <= P_hp_max, f"Max_HP_Power_{day}_{h}"

                # 2. Max Storage Capacity
                self.prob += E_stored[day][h] <= V_tank * density, f"Max_Storage_{day}_{h}"

                # 3. Energy Balance
                load_val = load_profile[h]
                if h == 0:
                    self.prob += E_stored[day][0] == E_stored[day][23] + Q_hp[day][0] - load_val, f"Balance_0_{day}"
                else:
                    self.prob += E_stored[day][h] == E_stored[day][h-1] + Q_hp[day][h] - load_val, f"Balance_{h}_{day}"

        # Solve
        print("Solving...")
        self.prob.solve()

        status = pulp.LpStatus[self.prob.status]
        print(f"Status: {status}")

        # Extract values
        p_hp_val = pulp.value(P_hp_max)
        v_tank_val = pulp.value(V_tank)
        total_cost_val = pulp.value(self.prob.objective)
        capex_val_res = pulp.value(annualized_capex)
        opex_val_res = pulp.value(total_opex)
        initial_capex_val = p_hp_val * self.capex_hp_per_kw + v_tank_val * self.capex_tank_per_m3
        npc_val = total_cost_val / self.annuity_factor

        results = {
            'p_hp_opt_kw': p_hp_val,
            'v_tank_opt': v_tank_val,
            'total_cost': total_cost_val,
            'capex_val': capex_val_res,
            'opex_val': opex_val_res,
            'total_initial_capex': initial_capex_val,
            'npc': npc_val
        }

        # Store Results based on scenario
        if enable_storage:
            self.results_with_storage = results
            # Update main attributes for compatibility
            self.p_hp_opt_kw = p_hp_val
            self.v_tank_opt = v_tank_val
            self.total_cost = total_cost_val
            self.capex_val = capex_val_res
            self.opex_val = opex_val_res
            self.total_initial_capex = initial_capex_val
            self.npc = npc_val

            # Store operational variables values for visualization
            for day in self.day_types:
                self.Q_hp_results[day] = [pulp.value(Q_hp[day][h]) for h in range(24)]
                self.E_stored_results[day] = [pulp.value(E_stored[day][h]) for h in range(24)]
        else:
            self.results_no_storage = results

        print("\n--- Results ---")
        print(f"Optimal Heat Pump Size: {p_hp_val/1000:.4f} MW")
        print(f"Optimal Tank Volume: {v_tank_val:.2f} m3")
        print(f"Total Annual Cost: {total_cost_val/1000:.2f} k€")
        print(f"  - Annualized CAPEX: {capex_val_res/1000:.2f} k€")
        print(f"  - Total Annual OPEX: {opex_val_res/1000:.2f} k€")


    def visualize_results(self):
        """Generates plots for the operational strategy."""
        print("Generating visualizations...")

        # Set style
        sns.set_theme(style="whitegrid")

        # Ensure plots directory exists
        os.makedirs("plots", exist_ok=True)

        hours = list(range(24))

        for day in self.day_types:
            # Prepare data
            load_profile = self.hourly_loads_kw[day].values
            hp_output = self.Q_hp_results[day]
            storage_level = self.E_stored_results[day]
            prices = self.electricity_prices

            # Create figure with 2 subplots
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)

            # Subplot 1: Production vs Demand + Price

            # Plot Heat Load (Area/Line)
            ax1.plot(hours, load_profile, label='Heat Load (kW)', color='tab:orange', linewidth=2, linestyle='--')

            # Plot HP Output
            ax1.step(hours, hp_output, where='mid', label='HP Output (kW)', color='tab:blue', linewidth=2)

            ax1.set_ylabel('Power (kW)', color='black')
            ax1.tick_params(axis='y', labelcolor='black')
            ax1.set_title(f"Operational Strategy - {day}")

            # Secondary Y-Axis for Price
            ax1_twin = ax1.twinx()
            ax1_twin.plot(hours, prices, label='Elec. Price (€/kWh)', color='tab:red', linestyle=':', linewidth=1.5)
            ax1_twin.set_ylabel('Electricity Price (€/kWh)', color='tab:red')
            ax1_twin.tick_params(axis='y', labelcolor='tab:red')

            # Combine legends
            lines_1, labels_1 = ax1.get_legend_handles_labels()
            lines_2, labels_2 = ax1_twin.get_legend_handles_labels()
            ax1.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper left')

            # Subplot 2: Storage Level
            ax2.plot(hours, storage_level, label='Storage Level (kWh)', color='tab:green', linewidth=2)
            ax2.fill_between(hours, storage_level, color='tab:green', alpha=0.3)

            ax2.set_ylabel('Energy Stored (kWh)')
            ax2.set_xlabel('Hour of Day')
            ax2.legend(loc='upper left')
            ax2.set_title("Thermal Energy Storage State")

            # Set x-ticks
            ax2.set_xticks(range(0, 25, 2))

            plt.tight_layout()

            # Save
            filename = f"plots/operation_{day}.png"
            plt.savefig(filename, dpi=300)
            plt.close()
            print(f"Saved plot: {filename}")

    def generate_readme(self):
        """Generates the README.md report with detailed mathematical formulations."""

        readme_content = f"""# Thermal Energy Storage (TES) Optimization Project

## 1. Project Overview
This project optimizes the sizing of a **Heat Pump (HP)** and a **Thermal Energy Storage (TES)** tank for a District Heating Network (DHN). The goal is to minimize the **Total Annualized Cost (TOTEX)** while satisfying the hourly heat demand of the network.

---

## 2. Optimization Results (Executive Summary)

### ✅ Optimal System Sizing
* **Heat Pump Capacity ($P_{{hp\\_max}}$):** {self.p_hp_opt_kw / 1000:.4f} MW
* **Storage Tank Volume ($V_{{tank}}$):** {self.v_tank_opt:.2f} m³

### 💰 Economic Analysis
* **Total Annualized Cost:** {self.total_cost / 1000:.2f} k€
    * **Annualized CAPEX:** {self.capex_val / 1000:.2f} k€
    * **Annual OPEX:** {self.opex_val / 1000:.2f} k€
* **Total Initial Investment (CAPEX):** {self.total_initial_capex / 1000:.2f} k€
* **Net Present Cost (NPC) over {self.n} years:** {self.npc / 1000:.2f} k€

---

## 3. Mathematical Model & Methodology

### 3.1. Physical Parameters
The model uses a physics-based approach to calculate system performance.

* **Heat Pump COP (Lorentz Efficiency):**
    The Coefficient of Performance is modeled using the Lorentz efficiency with a system efficiency factor ($\\eta_{{sys}} = 0.50$).

    $$ COP = \\eta_{{sys}} \\cdot \\frac{{T_{{sink, K}}}}{{T_{{sink, K}} - T_{{source, K}}}} $$

    Where:
    * $T_{{source, K}} = T_{{amb}} + 273.15$ (Air Temperature)
    * $T_{{sink, K}} = \\frac{{T_{{supply}} + T_{{return}}}}{{2}} + 273.15$ (Mean Water Temperature)
    * $T_{{return}} = {self.T_return}^\\circ C$ (Fixed)
    * $T_{{supply}}$ varies by day type (Climate Curve).

* **Storage Energy Density:**
    The energy storage capacity depends on the supply temperature for the specific day type.

    $$ \\rho_{{energy}} [kWh/m^3] = \\frac{{\\rho_{{water}} \\cdot c_p \\cdot (T_{{supply}} - T_{{return}})}}{{3600}} $$

    * $\\rho_{{water}} \\approx 1000 kg/m^3$
    * $c_p \\approx 4.18 kJ/kgK$

### 3.2. Economic Parameters
* **Annuity Factor ($\\alpha$):**
    Used to annualize the initial CAPEX over the project lifetime.

    $$ \\alpha = \\frac{{r(1+r)^N}}{{(1+r)^N - 1}} = {self.annuity_factor:.4f} $$

    * Discount Rate ($r$): {self.r * 100}%
    * Lifetime ($N$): {self.n} years

### 3.3. Optimization Problem (MILP)
The problem is formulated as a Mixed-Integer Linear Program (MILP) solved using `PuLP`.

**Objective Function:** Minimize Total Annual Cost
$$ C_{{total}} = CAPEX_{{annual}} + OPEX_{{annual}} $$

1.  **CAPEX:**
    $$ CAPEX_{{annual}} = \\alpha \\cdot (P_{{hp}}^{{cap}} \\cdot {self.capex_hp_per_kw} + V_{{tank}} \\cdot {self.capex_tank_per_m3}) $$

2.  **OPEX:**
    $$ OPEX_{{annual}} = \\sum_{{days}} W_{{day}} \\sum_{{h=0}}^{{23}} \\left( \\frac{{Q_{{hp}}(h)}}{{COP_{{day}}}} \\cdot Price_{{elec}}(h) \\right) $$

**Constraints:**
1.  **Power Limit:** $Q_{{hp}}(h) \\le P_{{hp}}^{{cap}}$
2.  **Storage Limit:** $E_{{stored}}(h) \\le V_{{tank}} \\cdot \\rho_{{energy}}(day)$
3.  **Energy Balance:** $E_{{stored}}(h) = E_{{stored}}(h-1) + Q_{{hp}}(h) - Load(h)$
4.  **Cyclic Constraint:** $E_{{stored}}(24) = E_{{stored}}(0)$

---

## 4. Code Structure & Logic Explained
The Python script `optimize_tes.py` is structured into a class `TESOptimizer` that handles the entire workflow:

1.  **`load_data()`**:
    * Reads hourly heat load profiles and temperature data from Excel.
    * **Crucial Step:** Converts heat loads from **MW** to **kW** to ensure consistency with cost parameters (€/kW).

2.  **`calculate_parameters()`**:
    * Computes the **COP** and **Storage Density** for each specific day type based on ambient and supply temperatures.
    * Generates the electricity price profile (Peak/Off-Peak hours) for France 2026.

3.  **`build_and_solve()` (The Core)**:
    * Constructs the MILP model using the `PuLP` library.
    * Defines decision variables: Heat Pump Size ($P_{{hp\\_max}}$) and Tank Volume ($V_{{tank}}$).
    * Sets up the objective function (minimize cost) and physical constraints (energy balance, capacity limits).
    * Solves the problem to find the global optimum.

4.  **`visualize_results()`**:
    * Generates dual-axis plots using `matplotlib`.
    * Visualizes the "Load Shifting" strategy: showing how the Heat Pump ramps up during low-price hours to charge the storage.

---

## 5. Operational Strategy
The system minimizes costs by shifting heat production to off-peak electricity hours and storing it for peak hours.
* **Off-Peak Hours ({self.price_off_peak} €/kWh):** 00:00-06:00, 12:00-14:00.
* **Peak Hours ({self.price_peak} €/kWh):** 06:00-12:00, 14:00-24:00.

The Heat Pump tends to run at higher capacity during off-peak times to charge the storage tank, which then discharges during peak times to satisfy the heat load, thereby avoiding expensive electricity.

## 6. Operational Profiles
Below are the detailed operational profiles for each day type.

"""
        for day in self.day_types:
            readme_content += f"### {day}\n"
            readme_content += f"![{day} Operation](plots/operation_{day}.png)\n\n"

        # Append Comparison Section if results exist
        if self.results_no_storage:
            no_storage_cost = self.results_no_storage['total_cost']
            savings = no_storage_cost - self.total_cost

            # Helper to safely handle subtractions if needed, but direct is fine here
            ns_capex = self.results_no_storage['capex_val']
            ns_opex = self.results_no_storage['opex_val']
            ns_init_capex = self.results_no_storage['total_initial_capex']
            ns_hp = self.results_no_storage['p_hp_opt_kw']
            ns_tank = self.results_no_storage['v_tank_opt']

            readme_content += f"""
## 7. Economic Benefit of Energy Storage
By introducing Thermal Energy Storage, the system achieves significant cost savings compared to a "Heat Pump Only" scenario.

| Metric | With Storage (Optimized) | No Storage (HP Only) | Savings |
| :--- | :--- | :--- | :--- |
| **Total Annual Cost** | **{self.total_cost/1000:.2f} k€** | **{no_storage_cost/1000:.2f} k€** | **{savings/1000:.2f} k€** |
| Annualized CAPEX | {self.capex_val/1000:.2f} k€ | {ns_capex/1000:.2f} k€ | {(ns_capex - self.capex_val)/1000:.2f} k€ |
| Annual OPEX | {self.opex_val/1000:.2f} k€ | {ns_opex/1000:.2f} k€ | {(ns_opex - self.opex_val)/1000:.2f} k€ |
| Initial CAPEX | {self.total_initial_capex/1000:.2f} k€ | {ns_init_capex/1000:.2f} k€ | {(ns_init_capex - self.total_initial_capex)/1000:.2f} k€ |
| Heat Pump Size | {self.p_hp_opt_kw/1000:.4f} MW | {ns_hp/1000:.4f} MW | - |
| Tank Volume | {self.v_tank_opt:.2f} m³ | {ns_tank:.2f} m³ | - |

> **Note:** "Savings" = (No Storage) - (With Storage). A positive value indicates the Storage scenario is cheaper.
"""

        # 使用 utf-8 编码写入
        with open('README.md', 'w', encoding='utf-8') as f:
            f.write(readme_content)
        print("\nREADME.md generated successfully with corrected formatting.")

if __name__ == "__main__":
    optimizer = TESOptimizer()
    optimizer.load_data()
    optimizer.calculate_parameters()

    # 1. Run No Storage Baseline
    optimizer.build_and_solve(enable_storage=False)

    # 2. Run With Storage (Main Optimization)
    optimizer.build_and_solve(enable_storage=True)

    optimizer.visualize_results()
    optimizer.generate_readme()
