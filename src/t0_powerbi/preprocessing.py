"""Turn any table with a date column and a numeric column into one regular series per entity.

Power BI hands Python whatever table the user selected, so nothing here assumes
column names. The date column, the value to forecast, the identifier columns and
the frequency (daily, weekly or monthly) are detected from the data; every
detection can be overridden with an argument. Every rejection says what was
detected and which rows are at fault.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DUPLICATE_POLICIES = ("sum", "mean", "last", "error")
FREQUENCIES = ("D", "W", "M")
# pandas offset used to build the regular grid for each detected frequency
_STEP = {"D": "D", "W": "7D", "MS": "MS", "ME": "ME"}
MIN_OBSERVATIONS = {"D": 14, "W": 12, "M": 12}
MAX_SERIES = 500
DEFAULT_SERIES = "All"

# Column-name hints, matched against lower-case tokens of the name ("Order Date" -> order, date).
DATE_HINTS = {"date", "day", "time", "timestamp", "period", "month", "week", "ds"}
VALUE_HINTS = {"sales", "revenue", "amount", "qty", "quantity", "units", "demand", "volume", "value",
               "total", "count", "orders", "target", "turnover", "visitors"}
ID_HINTS = {"id", "code", "key", "sku", "store", "shop", "product", "item", "region", "name", "category",
            "country", "city", "segment", "branch", "location", "customer", "site"}


class DataValidationError(ValueError):
    """The input table cannot be forecast as given."""


@dataclass
class Detected:
    """Which columns were used, for logs and error messages."""

    date: str
    value: str
    ids: list[str]
    freq: str = "D"
    ignored_numeric: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        ids = " + ".join(self.ids) if self.ids else "none (single series)"
        text = f"date={self.date!r}, value={self.value!r}, series ids={ids}, frequency={self.freq}"
        if self.ignored_numeric:
            text += f"; ignored numeric columns {self.ignored_numeric}"
        if self.warnings:
            text += "; WARNING: " + " ".join(self.warnings)
        return text


@dataclass
class SeriesBatch:
    """Regular series ready for the model, one per entity, in a fixed order.

    ``values[i]`` is a float array covering every period from its first to its last
    date; periods without an observation are NaN, which t0 reads as missing.
    ``last_dates[i]`` is its final period, so the forecast starts one period later.
    """

    ids: list[str]
    values: list[np.ndarray]
    last_dates: list[pd.Timestamp]
    step: str  # key of _STEP
    detected: Detected

    def dates(self, i: int) -> pd.DatetimeIndex:
        """The period of every value of series ``i`` (what t0 saw)."""
        return pd.date_range(end=self.last_dates[i], periods=len(self.values[i]), freq=_STEP[self.step])

    def future_dates(self, i: int, horizon: int) -> pd.DatetimeIndex:
        return pd.date_range(self.last_dates[i], periods=horizon + 1, freq=_STEP[self.step])[1:]


def _tokens(name: object) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


def _role_hint(name: object) -> str | None:
    tokens = _tokens(name)
    if tokens & VALUE_HINTS:
        return "value"
    if tokens & ID_HINTS or any(t.endswith(("id", "code", "key")) and len(t) > 2 for t in tokens):
        return "id"
    return None


def _pick(candidates: list, hints: set[str]):
    for col in candidates:
        if _tokens(col) & hints:
            return col
    return candidates[0]


def _is_datetime_like(s: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(s):
        return True
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
        return False
    sample = s.dropna().head(200)
    if sample.empty:
        return False
    if all(isinstance(v, datetime.date) for v in sample):  # date, datetime and Timestamp
        return True
    if all(isinstance(v, str) for v in sample):
        text = sample.astype(object).map(str.strip)
        if pd.to_numeric(text, errors="coerce").notna().mean() > 0.5:
            return False  # "1200" is a number, not the year 1200
        return pd.to_datetime(text, errors="coerce", format="mixed").notna().mean() >= 0.9
    return False


def _is_numeric_like(s: pd.Series) -> bool:
    if pd.api.types.is_bool_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
        return False
    if pd.api.types.is_numeric_dtype(s):
        return bool(s.notna().any())
    text = s.dropna().astype("string").str.strip()
    text = text[text != ""]
    if text.empty:
        return False
    return bool(pd.to_numeric(text, errors="coerce").notna().mean() >= 0.9)


def _summary(df: pd.DataFrame) -> str:
    """Column names with their pandas dtype, Python type and first value, for error messages."""
    parts = []
    for col in df.columns:
        values = df[col].dropna()
        example = values.iloc[0] if len(values) else None
        parts.append(f"{str(col)!r} (dtype {df[col].dtype}, python type {type(example).__name__}, first value {example!r})")
    return "; ".join(parts)


def _find_column(df: pd.DataFrame, name: str, role: str) -> str:
    lookup = {str(col).strip().lower(): col for col in df.columns}
    key = str(name).strip().lower()
    if key not in lookup:
        raise DataValidationError(f"The {role} column {name!r} was not found. Columns received: {_summary(df)}")
    return lookup[key]


def detect_columns(
    df: pd.DataFrame,
    date_col: str | None = None,
    value_col: str | None = None,
    id_cols: str | list[str] | None = None,
) -> Detected:
    """Decide which columns hold the date, the value to forecast and the series identifiers.

    Explicit names (case-insensitive) always win. Otherwise: the date is the first date-like
    column (preferring names such as "date"); the value is the first numeric column that is not
    named like an identifier (preferring "sales", "revenue", "amount", ...); the identifiers are
    all remaining text columns plus numeric columns named like ids ("store_id", "sku", ...).
    ``id_cols=[]`` means one series with no identifier.
    """
    columns = list(df.columns)
    for col in columns:
        first = df[col].dropna().head(1).astype(str)
        if len(first) and first.iloc[0].startswith("Microsoft.OleDb."):
            raise DataValidationError(
                f"Power BI sent column {str(col)!r} as the placeholder text {first.iloc[0]!r} instead of "
                "dates: it does that for columns of type Date. Convert date columns to text or to "
                "date/time before the Run Python script step (see powerbi/dashboard_guide.md, step 2, "
                "or paste powerbi/queries/Forecast.pq)."
            )
    found = f"Columns received: {_summary(df)}."

    date = _find_column(df, date_col, "date") if date_col else None
    if date is None:
        candidates = [c for c in columns if _is_datetime_like(df[c])]
        if not candidates:
            raise DataValidationError(f"No date column found (a column of dates is required). {found}")
        date = _pick(candidates, DATE_HINTS)

    if isinstance(id_cols, str):
        id_cols = [id_cols]
    explicit_ids = [_find_column(df, c, "series id") for c in id_cols] if id_cols else []

    if value_col:
        value = _find_column(df, value_col, "value")
    else:
        pool = [c for c in columns if c != date and c not in explicit_ids and _is_numeric_like(df[c])
                and _role_hint(c) != "id"]
        if not pool:
            raise DataValidationError(f"No numeric column to forecast found (besides the date). {found}")
        value = _pick(pool, VALUE_HINTS)
    ignored = [c for c in columns if c not in (date, value) and c not in explicit_ids and _is_numeric_like(df[c])
               and _role_hint(c) != "id"]

    if id_cols is not None:
        ids = explicit_ids
    else:
        ids = []
        for col in columns:
            if col in (date, value) or _is_datetime_like(df[col]) or not df[col].notna().any():
                continue
            if _is_numeric_like(df[col]) and _role_hint(col) != "id":
                continue
            if len(df) > 1 and df[col].nunique(dropna=True) == len(df):
                continue  # unique per row (an order number, a comment): not a series identifier
            ids.append(col)
    return Detected(date=str(date), value=str(value), ids=[str(c) for c in ids],
                    ignored_numeric=[str(c) for c in ignored if c != value])


def _examples(frame: pd.DataFrame, n: int = 3) -> str:
    return repr(frame.head(n).to_dict("records"))


def _parse_non_iso(text: pd.Series) -> pd.Series:
    """Parse day-first or month-first text such as 01.03.2026 by trying both.

    A time series is contiguous, so the wrong reading ("1 March" as "3 January") scatters its
    dates over a much longer span. Keep the reading with the fewest unparseable values, then
    the shortest span.
    """
    best = None
    for dayfirst in (False, True):
        parsed = pd.to_datetime(text, errors="coerce", format="mixed", dayfirst=dayfirst)
        valid = parsed.dropna()
        score = (int(parsed.isna().sum()), (valid.max() - valid.min()) if len(valid) else pd.Timedelta(0))
        if best is None or score < best[0]:
            best = (score, parsed)
    return best[1]


def _parse_dates(raw: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(raw):
        parsed = raw
    else:
        obj = raw.astype(object)
        is_text = obj.map(lambda v: isinstance(v, str))
        parsed = pd.to_datetime(obj.where(~is_text), errors="coerce")  # date / datetime objects
        if is_text.any():
            text = obj[is_text].map(str.strip)
            # ISO first (unambiguous); fall back to pandas' inference for other layouts.
            by_text = pd.to_datetime(text, errors="coerce", format="ISO8601")
            retry = by_text.isna()
            if retry.any():
                by_text[retry] = _parse_non_iso(text[retry])
            parsed = parsed.copy()
            parsed[is_text] = by_text
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    return parsed.dt.normalize()


def _parse_values(raw: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(raw) and not pd.api.types.is_bool_dtype(raw):
        return raw.astype("float64")
    text = raw.astype("string").str.strip()
    text = text.mask(text == "")
    return pd.to_numeric(text, errors="coerce").astype("float64")


def _classify_step(days: float) -> str | None:
    if days <= 1.5:
        return "D"
    if 5.5 <= days <= 8.5:
        return "W"
    if 27 <= days <= 32:
        return "M"
    return None


def _infer_freq(frame: pd.DataFrame, freq: str | None) -> str:
    if freq:
        freq = str(freq).strip().upper()[:1]
        if freq not in FREQUENCIES:
            raise ValueError(f"freq must be one of {FREQUENCIES} (daily, weekly, monthly), got {freq!r}")
        return freq
    found: dict[str, str] = {}
    for key, group in frame.groupby("series", sort=True):
        days = group["date"].drop_duplicates().sort_values()
        if len(days) < 3:
            continue
        step = days.diff().dropna().median().total_seconds() / 86400
        kind = _classify_step(step)
        if kind is None:
            raise DataValidationError(
                f"Series {key!r}: dates are spaced {step:g} days apart (median). Daily, weekly and monthly "
                "data are supported; aggregate the table to one of those, or pass freq explicitly."
            )
        found[key] = kind
    kinds = sorted(set(found.values()))
    if len(kinds) > 1:
        examples = {k: next(s for s, v in found.items() if v == k) for k in kinds}
        raise DataValidationError(
            f"Series have different frequencies (e.g. {examples}). Forecast one frequency at a time, "
            "or pass freq explicitly."
        )
    return kinds[0] if kinds else "D"


def _snap(dates: pd.Series, freq: str) -> tuple[pd.Series, str]:
    """Move every date onto the period grid (the same weekday, or the same month day) and name the grid."""
    if freq == "D":
        return dates, "D"
    if freq == "W":
        anchor = int(dates.dt.day_of_week.mode().iloc[0])
        name = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][anchor]
        return dates.dt.to_period(f"W-{name}").dt.end_time.dt.normalize(), "W"
    if dates.dt.is_month_end.mean() > 0.5:
        return dates.dt.to_period("M").dt.end_time.dt.normalize(), "ME"
    return dates.dt.to_period("M").dt.start_time, "MS"


def prepare_series(
    df: pd.DataFrame,
    *,
    date_col: str | None = None,
    value_col: str | None = None,
    id_cols: str | list[str] | None = None,
    freq: str | None = None,
    duplicates: str = "sum",
    drop_last_periods: int = 0,
    max_context: int | None = 2048,
    min_observations: int | None = None,
    max_series: int = MAX_SERIES,
) -> SeriesBatch:
    """Validate ``df`` and split it into one gap-filled regular series per entity.

    Columns and frequency are detected unless given (see ``detect_columns``). Rules:
      * every row needs a parseable date and, when identifiers exist, no empty identifier;
      * the value may be blank (a missing observation) but not non-numeric text or infinite;
      * rows sharing (series, period) are combined according to ``duplicates``
        (``"sum"`` -- many transactions in a period --, ``"mean"``, ``"last"`` or ``"error"``);
      * dates are snapped to the detected period and reindexed to a contiguous grid per series,
        missing periods becoming NaN;
      * ``drop_last_periods`` removes that many final periods from every series: use 1 when the
        last week or month is still in progress, because t0 would read its small total as a real drop;
      * only the most recent ``max_context`` periods are kept.
    """
    if duplicates not in DUPLICATE_POLICIES:
        raise ValueError(f"duplicates must be one of {DUPLICATE_POLICIES}, got {duplicates!r}")
    if not isinstance(drop_last_periods, (int, np.integer)) or drop_last_periods < 0:
        raise ValueError(f"drop_last_periods must be a non-negative integer, got {drop_last_periods!r}")
    if not isinstance(df, pd.DataFrame):
        raise DataValidationError(f"Expected a pandas DataFrame, got {type(df).__name__}")
    if df.empty:
        raise DataValidationError("The input table is empty.")

    detected = detect_columns(df, date_col, value_col, id_cols)
    used = [detected.date, detected.value, *detected.ids]
    raw_values = df[detected.value]
    frame = pd.DataFrame({"date": _parse_dates(df[detected.date]), "value": _parse_values(raw_values)})

    bad_dates = frame["date"].isna()
    if bad_dates.any():
        raise DataValidationError(
            f"{int(bad_dates.sum())} row(s) have a missing or unparseable date in {detected.date!r}. "
            f"Examples: {_examples(df.loc[bad_dates, used])}"
        )

    if detected.ids:
        parts = [df[c].astype("string").str.strip() for c in detected.ids]
        bad_ids = pd.concat([p.isna() | (p == "") for p in parts], axis=1).any(axis=1)
        if bad_ids.any():
            raise DataValidationError(
                f"{int(bad_ids.sum())} row(s) have an empty series identifier ({detected.ids}). "
                f"Examples: {_examples(df.loc[bad_ids, used])}"
            )
        frame["series"] = parts[0].astype(str)
        for part in parts[1:]:
            frame["series"] = frame["series"] + " | " + part.astype(str)
    else:
        frame["series"] = DEFAULT_SERIES

    blank = raw_values.isna() | (raw_values.astype("string").str.strip() == "")
    non_numeric = frame["value"].isna() & ~blank
    if non_numeric.any():
        raise DataValidationError(
            f"{int(non_numeric.sum())} row(s) have a non-numeric value in {detected.value!r}. "
            f"Examples: {_examples(df.loc[non_numeric, used])}"
        )
    infinite = np.isinf(frame["value"])
    if infinite.any():
        raise DataValidationError(
            f"{int(infinite.sum())} row(s) have an infinite value in {detected.value!r}. "
            f"Examples: {_examples(df.loc[infinite, used])}"
        )

    freq = _infer_freq(frame, freq)
    frame["date"], step = _snap(frame["date"], freq)
    detected.freq = {"D": "daily", "W": "weekly", "MS": "monthly", "ME": "monthly"}[step]

    dup_mask = frame.duplicated(["series", "date"], keep=False)
    if dup_mask.any():
        if duplicates == "error":
            raise DataValidationError(
                f"{int(dup_mask.sum())} row(s) share a (series, period) with another row. "
                f"Examples: {_examples(frame.loc[dup_mask])}"
            )
        logger.warning("Combining %d rows that share a (series, period) with %r", int(dup_mask.sum()), duplicates)
    grouped = frame.groupby(["series", "date"], sort=True)["value"]
    if duplicates == "sum":
        # min_count=1 keeps a period whose rows are all blank as missing rather than 0.
        combined = grouped.sum(min_count=1)
    elif duplicates == "mean":
        combined = grouped.mean()
    else:
        combined = grouped.last()

    n_series = combined.index.get_level_values("series").nunique()
    if n_series > max_series:
        raise DataValidationError(
            f"Found {n_series} series (more than {max_series}), built from {detected.ids or 'no id columns'}. "
            "This is usually the wrong id column; set the series id columns explicitly."
        )

    minimum = min_observations or MIN_OBSERVATIONS[freq]
    batch = SeriesBatch(ids=[], values=[], last_dates=[], step=step, detected=detected)
    for key, series in combined.groupby(level="series", sort=True):
        series = series.droplevel("series").sort_index()
        daily = series.reindex(pd.date_range(series.index[0], series.index[-1], freq=_STEP[step]))
        if drop_last_periods:
            daily = daily.iloc[:-drop_last_periods]
            if daily.empty:
                raise DataValidationError(f"Series {key!r} has no data left after dropping {drop_last_periods} period(s).")
        if max_context is not None and len(daily) > max_context:
            daily = daily.iloc[-max_context:]
        n_observed = int(daily.notna().sum())
        if n_observed < minimum:
            raise DataValidationError(
                f"Series {key!r} has only {n_observed} observed {detected.freq} period(s); at least {minimum} are required."
            )
        gaps = len(daily) - n_observed
        if gaps:
            logger.info("Series %r: %d missing period(s) passed to the model as missing values", key, gaps)
        observed = daily.dropna()
        if step != "D" and not drop_last_periods and len(observed) >= 6:
            earlier = observed.iloc[-5:-1].median()
            if earlier > 0 and observed.iloc[-1] < 0.5 * earlier and observed.index[-1] == daily.index[-1]:
                detected.warnings.append(
                    f"The last {detected.freq} period of {key!r} ({observed.iloc[-1]:g}) is under half the previous "
                    f"ones ({earlier:g}); if it is still in progress, set drop_last_periods=1."
                )
        batch.ids.append(str(key))
        batch.values.append(daily.to_numpy(dtype=np.float64, copy=True))
        batch.last_dates.append(daily.index[-1])
    logger.info("Detected %s", detected.describe())
    return batch
