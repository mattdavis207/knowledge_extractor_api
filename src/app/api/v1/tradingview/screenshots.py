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


def plot_to_png_buffer(data, prev_date, next_date) -> io.BytesIO:
    buffer = io.BytesIO()

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
        vlines=dict(vlines=[prev_date, next_date], linewidths=(2, 2)),
    )

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
