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
        self.Q_hp_results = {} # To store optimized values
        self.E_stored_results = {} # To store optimized values

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

    def build_and_solve(self):
        """Builds the PuLP optimization model and solves it."""
        print("\nBuilding Optimization Model...")

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

        # Objective Function
        annualized_capex = (P_hp_max * self.capex_hp_per_kw + V_tank * self.capex_tank_per_m3) * self.annuity_factor

        total_opex = 0
        for day in self.day_types:
            daily_opex = 0
            for h in range(24):
                # Power Consumed = Q_hp / COP
                power_consumed = Q_hp[day][h] / self.cop_dict[day]
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

        # Store Results
        self.p_hp_opt_kw = pulp.value(P_hp_max)
        self.v_tank_opt = pulp.value(V_tank)
        self.total_cost = pulp.value(self.prob.objective)
        self.capex_val = pulp.value(annualized_capex)
        self.opex_val = pulp.value(total_opex)

        # Store operational variables values for visualization
        for day in self.day_types:
            self.Q_hp_results[day] = [pulp.value(Q_hp[day][h]) for h in range(24)]
            self.E_stored_results[day] = [pulp.value(E_stored[day][h]) for h in range(24)]

        print("\n--- Results ---")
        print(f"Optimal Heat Pump Size: {self.p_hp_opt_kw/1000:.4f} MW")
        print(f"Optimal Tank Volume: {self.v_tank_opt:.2f} m3")
        print(f"Total Annual Cost: {self.total_cost/1000:.2f} k€")
        print(f"  - Annualized CAPEX: {self.capex_val/1000:.2f} k€")
        print(f"  - Total Annual OPEX: {self.opex_val/1000:.2f} k€")

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
        """Generates the README.md report."""
        readme_content = f"""# TES Optimization Results

## Optimal System Sizing
* **Heat Pump Capacity ($P_{{hp\\_max}}$):** {self.p_hp_opt_kw/1000:.4f} MW
* **Storage Tank Volume ($V_{{tank}}$):** {self.v_tank_opt:.2f} m³

## Economic Analysis
* **Total Annualized Cost:** {self.total_cost/1000:.2f} k€
    * **Annualized CAPEX:** {self.capex_val/1000:.2f} k€
    * **Annual OPEX:** {self.opex_val/1000:.2f} k€

## Operational Strategy Summary
The system minimizes costs by shifting heat production to off-peak electricity hours and storing it for peak hours.
* **Off-Peak Hours (Low Price):** 00:00-06:00, 12:00-14:00.
* **Peak Hours (High Price):** 06:00-12:00, 14:00-24:00.

The Heat Pump tends to run at higher capacity during off-peak times to charge the storage tank, which then discharges during peak times to satisfy the heat load, thereby avoiding expensive electricity.

## Operational Profiles
Below are the detailed operational profiles for each day type, showing the Heat Load, HP Output, Electricity Price, and Storage Level.
"""
        for day in self.day_types:
            readme_content += f"\n### {day}\n"
            readme_content += f"![{day} Operation](plots/operation_{day}.png)\n"

        with open("README.md", "w") as f:
            f.write(readme_content)
        print("\nREADME.md generated.")

if __name__ == "__main__":
    optimizer = TESOptimizer()
    optimizer.load_data()
    optimizer.calculate_parameters()
    optimizer.build_and_solve()
    optimizer.visualize_results() # Added this call
    optimizer.generate_readme()
