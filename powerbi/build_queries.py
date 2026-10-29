"""Write the Power Query (M) queries for the report, embedding power_query_script.py.

    python powerbi/build_queries.py

Produces three files in powerbi/queries/, to paste into Power Query (Blank Query > Advanced Editor):

  DataFile.pq  a text parameter: the path of the CSV to forecast
  Data.pq      reads that CSV (replace it with any other source: Excel, SQL, ...)
  Forecast.pq  sends Data to t0-gamma through Python; one table with actuals and forecasts

Rerun after editing power_query_script.py.
"""

from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent

DATA_FILE = """// DataFile: a parameter holding the path of the CSV to forecast.
// Create it with Home > Manage Parameters > New Parameter (Type: Text), or paste this as a Blank Query.
"{csv}" meta [IsParameterQuery = true, IsParameterQueryRequired = true, Type = "Text"]
"""

DATA = """// Data: the table to forecast. Any table works: it needs a date column and a numeric column,
// plus optional text columns (store, product, ...) that identify one series each. No renaming needed.
// To use Excel, SQL or SharePoint instead of a CSV, replace this query with that source.
let
    Source = Csv.Document(File.Contents(DataFile), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
in
    Headers
"""

FORECAST = """// Forecast: sends Data to t0-gamma via Python (local, offline) and returns, per series:
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
            (name) => {{name, each if _ is date or _ is datetime or _ is datetimezone then DateTime.ToText(DateTime.From(_), [Format = "yyyy-MM-dd", Culture = "en-US"]) else _}}
        )
    ),
    RunPython = Python.Execute("{script}", [dataset = ForPython]),
    Forecast = RunPython{{[Name = "forecast"]}}[Value],
    Present = (columns as list) as list => List.Select(columns, each List.Contains(Table.ColumnNames(Forecast), _)),
    // Every value becomes a real number (or null); NaN becomes null because the model rejects it.
    NoNaN = Table.TransformColumns(
        Forecast,
        List.Transform(
            Present({{"actual", "forecast", "p10", "p50", "p90"}}),
            (c) => {{c, each if _ = null then null else if _ is text then Number.FromText(_, "en-US") else let n = Number.From(_) in if Number.IsNaN(n) then null else n, type nullable number}}
        )
    ),
    Typed = Table.TransformColumnTypes(
        NoNaN,
        {{{{"date", type date}}}} & List.Transform(Present({{"series_id", "kind"}}), (c) => {{c, type text}}),
        "en-US"
    )
in
    Typed
"""


def m_string(text: str) -> str:
    """Escape text for an M string literal: quotes doubled, newlines as #(lf)."""
    return text.replace('"', '""').replace("\r\n", "\n").replace("\n", "#(lf)")


def main() -> None:
    script_lines = (HERE / "power_query_script.py").read_text(encoding="utf-8").splitlines()
    # Drop the leading comment block; keep the code.
    while script_lines and (script_lines[0].startswith("#") or not script_lines[0].strip()):
        script_lines.pop(0)
    out = HERE / "queries"
    out.mkdir(exist_ok=True)
    (out / "Sales.pq").unlink(missing_ok=True)  # replaced by DataFile.pq + Data.pq
    csv = r"C:\path\to\t0-powerbi\examples\Walmart_Sales.csv"  # placeholder: set it to your own copy
    (out / "DataFile.pq").write_text(DATA_FILE.format(csv=csv), encoding="utf-8")
    (out / "Data.pq").write_text(DATA, encoding="utf-8")
    (out / "Forecast.pq").write_text(FORECAST.format(script=m_string("\n".join(script_lines) + "\n")), encoding="utf-8")
    print(f"Wrote DataFile.pq, Data.pq and Forecast.pq to {out}")


if __name__ == "__main__":
    main()
