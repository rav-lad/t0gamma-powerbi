// Forecast: sends Data to t0-gamma via Python (local, offline) and returns, per series:
// date, series_id, kind ("actual" or "forecast"), actual (past only), forecast = p50, p10, p50, p90 (future only).
// Runs on every refresh. Generated from powerbi/power_query_script.py.
let
    Source = Data,
    // Power BI hands Python a Date column as the literal text "Microsoft.OleDb.Date", so every
    // date column of the table is sent as ISO text (yyyy-MM-dd). Other columns are untouched.
    ForPython = Table.TransformColumns(
        Source,
        List.Transform(
            Table.ColumnNames(Source),
            (name) => {name, each if _ is date or _ is datetime or _ is datetimezone then DateTime.ToText(DateTime.From(_), [Format = "yyyy-MM-dd", Culture = "en-US"]) else _}
        )
    ),
    RunPython = Python.Execute("import os#(lf)import warnings#(lf)#(lf)os.environ[""HF_HUB_OFFLINE""] = ""1""  # never reach the network from a refresh#(lf)os.environ[""HF_HUB_DISABLE_PROGRESS_BARS""] = ""1""#(lf)warnings.filterwarnings(""ignore"")#(lf)#(lf)from t0_powerbi import for_power_bi, forecast_dataframe#(lf)#(lf)HORIZON = 30  # periods to forecast: days, weeks or months, depending on the data#(lf)DROP_LAST_PERIODS = 0  # set to 1 if the last week or month in your data is still in progress#(lf)INCLUDE_HISTORY = True  # one table with actuals AND forecasts, so a report needs no second query#(lf)#(lf)# Everything below is detected automatically. Set a name only if the detection picks the wrong column.#(lf)DATE_COL = None  # e.g. ""Order Date""#(lf)VALUE_COL = None  # e.g. ""Revenue""#(lf)ID_COLS = None  # e.g. [""Store"", ""Product""]; [] forecasts the whole table as one series#(lf)FREQ = None  # ""D"" daily, ""W"" weekly or ""M"" monthly#(lf)#(lf)forecast = forecast_dataframe(#(lf)    dataset,#(lf)    horizon=HORIZON,#(lf)    date_col=DATE_COL,#(lf)    value_col=VALUE_COL,#(lf)    id_cols=ID_COLS,#(lf)    freq=FREQ,#(lf)    drop_last_periods=DROP_LAST_PERIODS,#(lf)    include_history=INCLUDE_HISTORY,#(lf))#(lf)forecast = for_power_bi(forecast)  # ISO dates as text, empty cells as null (never NaN: the model rejects it)#(lf)", [dataset = ForPython]),
    Forecast = RunPython{[Name = "forecast"]}[Value],
    Present = (columns as list) as list => List.Select(columns, each List.Contains(Table.ColumnNames(Forecast), _)),
    // Every value becomes a real number (or null); NaN becomes null because the model rejects it.
    NoNaN = Table.TransformColumns(
        Forecast,
        List.Transform(
            Present({"actual", "forecast", "p10", "p50", "p90"}),
            (c) => {c, each if _ = null then null else if _ is text then Number.FromText(_, "en-US") else let n = Number.From(_) in if Number.IsNaN(n) then null else n, type nullable number}
        )
    ),
    Typed = Table.TransformColumnTypes(
        NoNaN,
        {{"date", type date}} & List.Transform(Present({"series_id", "kind"}), (c) => {c, type text}),
        "en-US"
    )
in
    Typed
