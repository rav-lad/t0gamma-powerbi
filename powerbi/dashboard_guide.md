# Build the report in Power BI Desktop

Do the install from the [README](../README.md) first (environment + model download). This guide takes about
15 minutes and ends with a report like `screenshots/dashboard_walmart.png`.

## 1. Point Power BI at the Python environment

**File → Options and settings → Options → Python scripting** → *Detected Python home directories*: **Other**
→ browse to the folder printed at the end of `install.ps1`, e.g. `C:\...\t0-powerbi\.venv\Scripts`.
If Power BI does not accept a virtual environment, install into a regular Python instead:
`py -3.11 -m pip install -e .` then `py -3.11 -m t0_powerbi.model`, and give Power BI that Python's folder.
Power BI does not find packages on its own: `t0_powerbi` must be installed in exactly the environment you pick.

**File → Options → Current file**:
- *Privacy* → **Ignore the Privacy levels** (required before Power BI sends a table to a Python script).
- *Data Load* → untick **Allow data preview to download in the background** (otherwise editing queries
  can run the model more often than needed).

## 2. The three queries

**Home → Transform data**. In the Power Query window, for each file in [`queries/`](queries/):
**Home → New Source → Blank Query → Advanced Editor**, paste the file, **Done**, rename the query to the file name.

1. **`DataFile`** ([`DataFile.pq`](queries/DataFile.pq)): the path of the CSV. Change the path inside the quotes
   (e.g. to `examples\Walmart_Sales.csv`). It should be a *parameter*; if Power BI shows it as a plain
   query, use **Home → Manage Parameters → New Parameter** (Type: Text) instead.
2. **`Data`** ([`Data.pq`](queries/Data.pq)): reads that CSV, with no types so dates stay text. Load the CSV
   this way, not with the *Text/CSV* button, which converts dates using your locale and can swap day and month
   (`19-02-2010` is not a US date). For Excel or SQL, replace this query with your own source.
3. **`Forecast`** ([`Forecast.pq`](queries/Forecast.pq)): sends `Data` to t0-gamma through Python.
   Allow the Python script to run if asked.

`Forecast` should show one row per series and period: observed periods (`kind = actual`) and the 30 forecast
periods (`kind = forecast`). **Close & Apply.**

| `date` | `series_id` | `kind` | `actual` | `forecast`, `p10`, `p50`, `p90` |
| --- | --- | --- | --- | --- |
| past | yes | `actual` | observed value | empty |
| future | yes | `forecast` | empty | t0-gamma's quantiles (`forecast` = `p50`) |

The empty cells are layout, not predictions: the model only forecasts the future, and history is copied
into the same table so the report needs a single query. There are no relationships to create.

## 3. Measures

Select the `Forecast` table → **Home → New measure**, once per measure. They depend on nothing else.

```DAX
Actual = SUM ( Forecast[actual] )
Forecast P50 = SUM ( Forecast[p50] )
Forecast P10 = SUM ( Forecast[p10] )
Forecast P90 = SUM ( Forecast[p90] )
Expected Demand = SUM ( Forecast[p50] )
Forecast Periods = CALCULATE ( DISTINCTCOUNT ( Forecast[date] ), Forecast[p50] <> BLANK () )
Avg Forecast per Period = AVERAGE ( Forecast[p50] )
Avg Actual per Period = AVERAGE ( Forecast[actual] )
Expected Change % = DIVIDE ( AVERAGE ( Forecast[p50] ), AVERAGE ( Forecast[actual] ) ) - 1
Forecast Uncertainty % = DIVIDE ( SUM ( Forecast[p90] ) - SUM ( Forecast[p10] ), SUM ( Forecast[p50] ) )
Volatility % = DIVIDE ( STDEV.P ( Forecast[actual] ), AVERAGE ( Forecast[actual] ) )
Peak Forecast = MAX ( Forecast[p50] )
Peak Date = VAR PeakValue = MAX ( Forecast[p50] ) RETURN CALCULATE ( MAX ( Forecast[date] ), Forecast[p50] = PeakValue )
```

Format the three `%` measures with **Measure tools → %**, and `Peak Date` as a date. Paste each formula on one line
if Power BI drops the line breaks. A measure refers to another by its exact name, so create them in this order.

| measure | what it says |
| --- | --- |
| `Expected Demand` | sum of the P50 forecast over the whole horizon, for the selected series |
| `Expected Change %` | average forecast per period against the average of the whole history (overstates the gap for a series that grows fast) |
| `Forecast Uncertainty %` | width of the P10–P90 band relative to the P50 |
| `Volatility %` | standard deviation of the history over its mean (includes seasonality and trend, so it is variability, not pure noise) |

## 4. Report page

1. **Slicer**: `Forecast[series_id]`, *Format → Slicer settings → Selection → Single select* (see the note on quantiles below).
2. **Line chart**:
   - X-axis `Forecast[date]`: pick **Date**, not *Date Hierarchy*, in the field's dropdown, and set
     *Format → X-axis → Type → Continuous*. Otherwise the chart shows one point per year.
   - Y-axis: `Actual`, `Forecast P50`, `Forecast P10`, `Forecast P90`. Make P10 and P90 thin or light.
   - Optional: *Filters on this visual* → `Forecast[date]` → *is on or after* a date a few months before the end
     of the history, to zoom in.
3. **Cards**: `Expected Demand`, `Expected Change %`, `Forecast Uncertainty %`, `Volatility %`, plus `Peak Forecast` and `Peak Date`.
4. **Table** (optional): `date`, `series_id`, `kind`, `p10`, `p50`, `p90`.

The two curves leave a gap of one period where the history ends and the forecast begins; `actual` is empty on
forecast rows and the other way round.

## 5. Refresh = recompute

**Home → Refresh** re-runs the queries, so `Forecast` re-runs t0-gamma on whatever `Data` now contains.
Changing the CSV path in `DataFile` and refreshing forecasts another file.

## What the slicer does and does not do

- **It filters forecasts that already exist.** Every series is forecast at refresh time. No model runs when you
  click a slicer or open the report.
- **A refresh reloads the model** (~3 s) because Power BI starts a new Python process each time.
- **Quantiles do not add up across series.** Summed P10/P90 are not the P10/P90 of the total; that is why the slicer is single-select.
- **An unfinished last period misleads the model.** If the last week or month is still in progress, set
  `DROP_LAST_PERIODS = 1` in the script, or the forecast follows its small total.

## Troubleshooting

| In Power BI | Cause and fix |
| --- | --- |
| `No module named 't0_powerbi'` | Power BI uses another Python than the one you installed into. Check step 1. |
| `ModelLoadError: ... not in the local cache` | Run `python -m t0_powerbi.model` with the same Python Power BI uses. |
| `placeholder text 'Microsoft.OleDb.Date'` | A Date-typed column reached Python as that text. Use the provided `Forecast.pq`, which converts date columns to text first. |
| `No date column found` / `No numeric column to forecast found` | The message lists the columns Python received, with their types. If detection picks the wrong column, set `DATE_COL`, `VALUE_COL` or `ID_COLS` at the top of the script. |
| `... days apart` / `different frequencies` | Daily, weekly and monthly data are supported. Aggregate to one of them, or set `FREQ`. |
| The load reports an error in every row | Empty cells reached the model as NaN. The provided script returns real nulls (`for_power_bi`); use the current `Forecast.pq`. |
| Chart shows one point per year | The X-axis is on *Date Hierarchy*. See step 4. |
| Python script times out | Power BI stops Python in Power Query after 30 minutes. A refresh takes seconds; check CPU and RAM. |

Scheduled refresh in the Power BI Service needs a personal-mode on-premises gateway with the same Python
environment; this was not tested.
