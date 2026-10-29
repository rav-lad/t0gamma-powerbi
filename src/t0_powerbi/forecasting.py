"""Forecast every store in a sales table with t0-gamma and return a tidy DataFrame."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from t0_powerbi.preprocessing import SeriesBatch, prepare_series

QUANTILE_LEVELS = (0.1, 0.5, 0.9)
OUTPUT_COLUMNS = ["date", "series_id", "forecast", "p10", "p50", "p90"]
HISTORY_COLUMNS = ["date", "series_id", "kind", "actual", "forecast", "p10", "p50", "p90"]


class ForecastError(RuntimeError):
    """The model failed or returned an output that does not match the request."""


def _to_numpy(array) -> np.ndarray:
    if hasattr(array, "detach"):  # torch.Tensor, possibly on a GPU
        array = array.detach().cpu().numpy()
    return np.asarray(array, dtype=np.float64)


def predict_quantiles(model, batch: SeriesBatch, horizon: int, quantile_levels: Sequence[float]) -> np.ndarray:
    """Run t0 on every series of ``batch`` at once; returns ``(n_series, horizon, n_levels)``.

    Series of different lengths are right-aligned with ``t0.batch_series``, which marks
    the left padding as ``PAD`` (not as missing data) and gives each store its own group,
    so stores are forecast independently. The four adjustments are the settings the
    t0-gamma README recommends; each only uses the store's own history.
    """
    from t0 import batch_series

    context, mask, group_ids = batch_series(batch.values)
    try:
        out = model.predict(
            context,
            horizon=horizon,
            quantile_levels=list(quantile_levels),
            mask=mask,
            group_ids=group_ids,
            trim_leading_missing=True,
            symmetric_averaging=True,
            clip_nonnegative=True,
            snap_integers=True,
        )
    except Exception as exc:
        raise ForecastError(f"t0-gamma inference failed: {exc}") from exc

    quantiles = _to_numpy(out.quantiles)
    expected = (len(batch.ids), horizon, len(quantile_levels))
    if quantiles.shape != expected:
        raise ForecastError(f"Model returned quantiles shaped {quantiles.shape}, expected {expected}")
    if not np.isfinite(quantiles).all():
        raise ForecastError("Model returned non-finite forecast values")
    if (np.diff(quantiles, axis=-1) < -1e-6).any():
        raise ForecastError("Model returned crossing quantiles (P10 > P50 or P50 > P90)")
    return quantiles


def for_power_bi(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of ``df`` as Power Query loads it without errors.

    Dates become ISO text (``2026-01-31``, which the query types as Date) and every empty numeric
    cell becomes ``None`` instead of NaN: the Power BI model rejects NaN, which shows up as an
    error in every row of a table that mixes history and forecast rows.
    """
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    for col in out.select_dtypes("float").columns:
        out[col] = out[col].astype(object).where(out[col].notna(), None)
    return out


def forecast_dataframe(
    df: pd.DataFrame,
    horizon: int = 30,
    *,
    model=None,
    date_col: str | None = None,
    value_col: str | None = None,
    id_cols: str | list[str] | None = None,
    freq: str | None = None,
    duplicates: str = "sum",
    drop_last_periods: int = 0,
    include_history: bool = False,
    max_context: int | None = 2048,
    device: str | None = None,
) -> pd.DataFrame:
    """Forecast ``horizon`` periods for every series in ``df``.

    ``df`` is any long table with a date column, a numeric column and optionally identifier
    columns (store, product, ...); columns and frequency (daily, weekly or monthly) are detected
    unless given, see ``t0_powerbi.preprocessing.detect_columns``. ``horizon`` counts periods of the
    detected frequency. Returns one row per series and future period with columns
    ``date, series_id, forecast, p10, p50, p90``; ``forecast`` is the P50. Each series' forecast
    starts one period after its own last date. What was detected is in ``result.attrs["detected"]``,
    and ``result.attrs["warnings"]`` lists anything suspicious (e.g. an unfinished last month).
    Set ``drop_last_periods=1`` when the last week or month is still in progress.

    With ``include_history=True`` the result also holds one row per observed period, with the value
    in a leading ``actual`` column and empty forecast columns, so a report needs this one table
    for history and forecast alike. ``kind`` says which: ``"actual"`` for observed periods,
    ``"forecast"`` for future ones: ``date, series_id, kind, actual, forecast, p10, p50, p90``.

    ``model`` is any object with t0's ``predict`` interface; by default t0-gamma
    is loaded from the local cache (see ``t0_powerbi.model``).
    """
    if not isinstance(horizon, (int, np.integer)) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError(f"horizon must be a positive integer, got {horizon!r}")
    horizon = int(horizon)

    batch = prepare_series(
        df, date_col=date_col, value_col=value_col, id_cols=id_cols, freq=freq,
        duplicates=duplicates, drop_last_periods=drop_last_periods, max_context=max_context,
    )
    if model is None:
        from t0_powerbi.model import load_model

        model = load_model(device=device)

    quantiles = predict_quantiles(model, batch, horizon, QUANTILE_LEVELS)

    frames = []
    for i, (series_id, q) in enumerate(zip(batch.ids, quantiles)):
        frames.append(
            pd.DataFrame(
                {
                    "date": batch.future_dates(i, horizon),
                    "series_id": series_id,
                    "forecast": q[:, 1],
                    "p10": q[:, 0],
                    "p50": q[:, 1],
                    "p90": q[:, 2],
                }
            )
        )
    result = pd.concat(frames, ignore_index=True)[OUTPUT_COLUMNS]
    if include_history:
        past = pd.concat(
            [pd.DataFrame({"date": batch.dates(i), "series_id": sid, "kind": "actual", "actual": batch.values[i]})
             for i, sid in enumerate(batch.ids)],
            ignore_index=True,
        )
        result = pd.concat([past, result.assign(kind="forecast")], ignore_index=True)[HISTORY_COLUMNS]
        result = result.sort_values(["series_id", "date"], kind="stable", ignore_index=True)
    result["date"] = result["date"].astype("datetime64[ns]")
    result["series_id"] = result["series_id"].astype(str)
    result.attrs["detected"] = batch.detected.describe()
    result.attrs["warnings"] = list(batch.detected.warnings)
    return result
