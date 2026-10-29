# Paste this into Power BI Desktop: Power Query Editor > Transform > Run Python script.
# It works on ANY table that has a date column and a numeric column (plus optional
# columns such as store or product that identify one series each). No renaming needed.
#
# Power BI provides the current table as the pandas DataFrame `dataset`. Every DataFrame
# left in scope when the script ends is offered as an output table; pick `forecast`.
# Output columns: date, series_id, actual (history only), forecast, p10, p50, p90 (future only).
#
# Prerequisite: the `t0_powerbi` package is installed in the Python environment
# selected under File > Options > Python scripting, and the model has been
# downloaded once with `python -m t0_powerbi.model` (see powerbi/setup.md).

import os
import warnings

os.environ["HF_HUB_OFFLINE"] = "1"  # never reach the network from a refresh
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
warnings.filterwarnings("ignore")

from t0_powerbi import for_power_bi, forecast_dataframe

HORIZON = 30  # periods to forecast: days, weeks or months, depending on the data
DROP_LAST_PERIODS = 0  # set to 1 if the last week or month in your data is still in progress
INCLUDE_HISTORY = True  # one table with actuals AND forecasts, so a report needs no second query

# Everything below is detected automatically. Set a name only if the detection picks the wrong column.
DATE_COL = None  # e.g. "Order Date"
VALUE_COL = None  # e.g. "Revenue"
ID_COLS = None  # e.g. ["Store", "Product"]; [] forecasts the whole table as one series
FREQ = None  # "D" daily, "W" weekly or "M" monthly

forecast = forecast_dataframe(
    dataset,
    horizon=HORIZON,
    date_col=DATE_COL,
    value_col=VALUE_COL,
    id_cols=ID_COLS,
    freq=FREQ,
    drop_last_periods=DROP_LAST_PERIODS,
    include_history=INCLUDE_HISTORY,
)
forecast = for_power_bi(forecast)  # ISO dates as text, empty cells as null (never NaN: the model rejects it)
