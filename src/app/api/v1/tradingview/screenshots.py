import io
from urllib.parse import urlparse

import cloudinary
import cloudinary.uploader
import matplotlib

matplotlib.use("Agg")

import mplfinance as mpf
import pandas as pd

from app.api.v1.tradingview.schemas import TradingViewPriceCandle
from app.core.config import settings


def configure_cloudinary() -> None:
    if not settings.cloudinary_url:
        raise ValueError("CLOUDINARY_URL is not configured")

    parsed = urlparse(settings.cloudinary_url)

    cloudinary.config(
        cloud_name=parsed.hostname,
        api_key=parsed.username,
        api_secret=parsed.password,
        secure=True,
    )


def upload_chart_image(buffer: io.BytesIO, public_id: str) -> str:
    configure_cloudinary()

    result = cloudinary.uploader.upload(
        buffer,
        resource_type="image",
        folder="tradingview-analysis",
        public_id=public_id,
        overwrite=True,
    )

    return result["secure_url"]


def build_setup_highlight(data, prev_time: int, next_time: int) -> dict | None:
    if data.empty:
        return None

    prev_timestamp = pd.to_datetime(prev_time, unit="s", utc=True)
    next_timestamp = pd.to_datetime(next_time, unit="s", utc=True)
    setup_mask = (data.index >= prev_timestamp) & (data.index <= next_timestamp)

    if not setup_mask.any():
        return None

    setup_window = data.loc[setup_mask]
    setup_low = setup_window["Low"].min()
    setup_high = setup_window["High"].max()

    return {
        "y1": [setup_low] * len(data),
        "y2": [setup_high] * len(data),
        "where": setup_mask,
        "color": "#d9d9d9",
        "alpha": 0.5,
        "linewidth": 0,
        "zorder": 0.5,
    }


def plot_to_png_buffer(data, prev_time: int, next_time: int) -> io.BytesIO:
    buffer = io.BytesIO()
    setup_highlight = build_setup_highlight(data, prev_time, next_time)

    kwargs = dict(
        type="candle",
        volume=True,
        savefig={
            "fname": buffer,
            "format": "png",
            "dpi": 150,
            "bbox_inches": "tight",
        },
        datetime_format="%Y-%m-%d",
    )
    if setup_highlight is not None:
        kwargs["fill_between"] = setup_highlight

    mpf.plot(
        data,
        **kwargs,
        style="charles",
    )

    buffer.seek(0)
    return buffer


def transform_candles_to_dataframe(
    candles: list[TradingViewPriceCandle],
):
    rows = []
    for candle in candles:
        rows.append(
            {
                "Date": pd.to_datetime(candle.time, unit="s", utc=True),
                "Open": candle.open,
                "High": candle.max,
                "Low": candle.min,
                "Close": candle.close,
                "Volume": candle.volume or 0,
            }
        )

    df = pd.DataFrame(rows)
    df = df.set_index("Date")

    return df
