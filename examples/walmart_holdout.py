"""How good is t0-gamma on the Walmart data? Hide the last 12 weeks, forecast them, compare.

    python examples/walmart_holdout.py

One window, 45 stores: a sanity check, not a full backtest.
"""

from pathlib import Path

import pandas as pd

from t0_powerbi import forecast_dataframe

HORIZON = 12

sales = pd.read_csv(Path(__file__).with_name("Walmart_Sales.csv"))
sales["week"] = pd.to_datetime(sales["Date"], format="%d-%m-%Y")

cutoff = sales["week"].sort_values().unique()[-HORIZON - 1]
train, actual = sales[sales["week"] <= cutoff], sales[sales["week"] > cutoff]

forecast = forecast_dataframe(train, horizon=HORIZON)
forecast["Store"] = forecast["series_id"].astype(int)
merged = forecast.merge(
    actual.rename(columns={"week": "date"})[["Store", "date", "Weekly_Sales"]], on=["Store", "date"]
)
merged["error"] = (merged["p50"] - merged["Weekly_Sales"]).abs() / merged["Weekly_Sales"]
inside = merged["Weekly_Sales"].between(merged["p10"], merged["p90"])

# Baseline: repeat each store's last observed week.
last_week = train.sort_values("week").groupby("Store")["Weekly_Sales"].last()
naive = (merged["Store"].map(last_week) - merged["Weekly_Sales"]).abs() / merged["Weekly_Sales"]

print(f"{len(merged)} store-weeks forecast, from {forecast['date'].min().date()} to {forecast['date'].max().date()}")
print(f"mean absolute % error of P50 : {merged['error'].mean():.1%}   (repeat-last-week baseline: {naive.mean():.1%})")
print(f"actuals inside P10-P90       : {inside.mean():.0%}   (80% expected for a calibrated band)")
