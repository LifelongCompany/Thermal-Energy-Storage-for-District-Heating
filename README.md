# TES Optimization Results

## Optimal System Sizing
* **Heat Pump Capacity ($P_{hp\_max}$):** 2.0225 MW
* **Storage Tank Volume ($V_{tank}$):** 190.99 m³

## Economic Analysis
* **Total Annualized Cost:** 676.86 k€
    * **Annualized CAPEX:** 240.09 k€
    * **Annual OPEX:** 436.77 k€

## Operational Strategy Summary
The system minimizes costs by shifting heat production to off-peak electricity hours and storing it for peak hours.
* **Off-Peak Hours (Low Price):** 00:00-06:00, 12:00-14:00.
* **Peak Hours (High Price):** 06:00-12:00, 14:00-24:00.

The Heat Pump tends to run at higher capacity during off-peak times to charge the storage tank, which then discharges during peak times to satisfy the heat load, thereby avoiding expensive electricity.

## Operational Profiles
Below are the detailed operational profiles for each day type, showing the Heat Load, HP Output, Electricity Price, and Storage Level.

### Interval_Cold
![Interval_Cold Operation](plots/operation_Interval_Cold.png)

### Interval_Mild
![Interval_Mild Operation](plots/operation_Interval_Mild.png)

### Maximum_heatingDay_Profile
![Maximum_heatingDay_Profile Operation](plots/operation_Maximum_heatingDay_Profile.png)

### Minimum_heatingDay_Profile
![Minimum_heatingDay_Profile Operation](plots/operation_Minimum_heatingDay_Profile.png)
