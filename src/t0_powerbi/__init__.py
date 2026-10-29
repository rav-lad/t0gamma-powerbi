"""Local t0-gamma forecasting for Power BI Desktop's Python scripts in Power Query."""

from t0_powerbi.forecasting import ForecastError, for_power_bi, forecast_dataframe
from t0_powerbi.preprocessing import DataValidationError, prepare_series

__all__ = [
    "DataValidationError",
    "ForecastError",
    "ModelLoadError",
    "for_power_bi",
    "forecast_dataframe",
    "load_model",
    "prepare_series",
]
__version__ = "0.1.0"


def __getattr__(name: str):
    # Imported on first use so that `python -m t0_powerbi.model` does not find the module already loaded.
    if name in ("ModelLoadError", "load_model"):
        from t0_powerbi import model

        return getattr(model, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
