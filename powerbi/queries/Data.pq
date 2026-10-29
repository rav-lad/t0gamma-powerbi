// Data: the table to forecast. Any table works: it needs a date column and a numeric column,
// plus optional text columns (store, product, ...) that identify one series each. No renaming needed.
// To use Excel, SQL or SharePoint instead of a CSV, replace this query with that source.
let
    Source = Csv.Document(File.Contents(DataFile), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true])
in
    Headers
