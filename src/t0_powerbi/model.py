"""Load t0-gamma from the local Hugging Face cache.

Forecasting never touches the network: weights are downloaded once, by
``python -m t0_powerbi.model`` (or ``t0-powerbi-setup``), and every later load
uses ``local_files_only=True``. If the weights are not there, loading fails with
the command that fixes it — it never falls back to another model.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

MODEL_ID = "theforecastingcompany/t0-gamma"

# Kept for the lifetime of the Python process. Power BI starts a fresh process for
# every Python step, so this only helps notebooks, tests and repeated calls in one script.
_CACHE: dict[tuple[str, str], object] = {}


class ModelLoadError(RuntimeError):
    """t0-gamma could not be loaded."""


def resolve_device(device: str | None = None) -> str:
    """``"cuda"`` when requested or available, else ``"cpu"``. ``T0_POWERBI_DEVICE`` overrides the default."""
    import torch

    device = (device or os.environ.get("T0_POWERBI_DEVICE") or "auto").lower()
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ModelLoadError(f"Device {device!r} requested but PyTorch reports no CUDA GPU.")
    return device


def load_model(
    model_id: str | None = None,
    *,
    device: str | None = None,
    local_files_only: bool = True,
):
    """Return the t0-gamma ``T0Forecaster`` in eval mode on ``device``.

    ``model_id`` defaults to ``T0_POWERBI_MODEL`` or the official Hub id; a local
    directory holding ``config.json`` and ``model.safetensors`` also works.
    """
    model_id = model_id or os.environ.get("T0_POWERBI_MODEL") or MODEL_ID
    try:
        from t0 import T0Forecaster
    except ImportError as exc:
        raise ModelLoadError(
            "The official t0 package is not installed in this Python environment "
            f"({sys.executable}). Install it with: pip install \"tfc-t0[torch]>=0.6.0\""
        ) from exc

    device = resolve_device(device)
    key = (model_id, device)
    if key in _CACHE:
        return _CACHE[key]
    try:
        model = T0Forecaster.from_pretrained(model_id, local_files_only=local_files_only)
    except Exception as exc:
        hint = (
            f"Weights for {model_id!r} are not in the local cache. Run once, with internet access:\n"
            f"    {sys.executable} -m t0_powerbi.model" + ("" if model_id == MODEL_ID else f" --model {model_id}")
            if local_files_only
            else ""
        )
        raise ModelLoadError(f"Could not load {model_id!r}: {exc}\n{hint}".rstrip()) from exc
    model = model.to(device).eval()
    _CACHE[key] = model
    return model


def main(argv: list[str] | None = None) -> int:
    """Download t0-gamma into the local cache and check it forecasts."""
    parser = argparse.ArgumentParser(description="Download and verify t0-gamma for local use.")
    parser.add_argument("--model", default=MODEL_ID, help="Hub id or local directory (default: %(default)s)")
    parser.add_argument("--device", default=None, help="cpu, cuda or auto (default: auto)")
    args = parser.parse_args(argv)

    import numpy as np

    print(f"Python: {sys.executable}")
    start = time.perf_counter()
    model = load_model(args.model, device=args.device, local_files_only=False)
    print(f"Loaded {args.model} on {resolve_device(args.device)} in {time.perf_counter() - start:.1f}s")

    days = np.arange(120)
    context = 100 + 10 * np.sin(2 * np.pi * days / 7)
    out = model.predict(context, horizon=14, quantile_levels=[0.1, 0.5, 0.9])
    quantiles = out.quantiles.detach().cpu().numpy()
    if quantiles.shape != (1, 14, 3) or not np.isfinite(quantiles).all():
        print(f"Unexpected forecast output: shape {quantiles.shape}", file=sys.stderr)
        return 1
    print(f"Test forecast OK, first P50 values: {[round(float(v), 1) for v in quantiles[0, :7, 1]]}")
    print("Setup complete: Power BI refreshes will now load the model from the local cache.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
