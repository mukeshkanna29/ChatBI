"""Generate one rich demo dataset that exercises every part of ChatBI.

It is built with real signals in it — seasonality, an underperforming region, a
category that gets returned, discounting that eats margin — so the AI has genuine
findings to report rather than noise. A few columns are left deliberately messy
(money as text, dates as text, some blanks) to show the import cleaning them up.

    python make_demo_dataset.py                 # writes to your Desktop
    python make_demo_dataset.py --out .         # or anywhere else
    python make_demo_dataset.py --rows 12000    # bigger, if you want to stress it
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

REGIONS = {"North": 1.00, "South": 0.78, "East": 1.12, "West": 0.95}
CITIES = {
    "North": ["Manchester", "Leeds", "Newcastle"],
    "South": ["Bristol", "Brighton", "Southampton"],
    "East":  ["London", "Cambridge", "Norwich"],
    "West":  ["Cardiff", "Bath", "Exeter"],
}
CATEGORIES = {
    #             price   margin  return-rate
    "Electronics": (340.0, 0.14, 0.05),
    "Furniture":   (185.0, 0.19, 0.07),
    "Apparel":      (52.0, 0.38, 0.11),
    "Beauty":       (28.0, 0.44, 0.19),   # returned far more than anything else
    "Grocery":      (16.0, 0.22, 0.02),
}
PRODUCTS = {
    "Electronics": ["Aurora Laptop", "Nimbus Tablet", "Pulse Headphones"],
    "Furniture":   ["Oak Desk", "Ergo Chair", "Shelving Unit"],
    "Apparel":     ["Field Jacket", "Merino Knit", "Trail Runners"],
    "Beauty":      ["Serum No.5", "Clay Cleanser", "Rose Balm"],
    "Grocery":     ["Coffee 1kg", "Olive Oil", "Dark Chocolate"],
}
CHANNELS = {"Online": 0.52, "Store": 0.33, "Wholesale": 0.15}
SEGMENTS = {"Consumer": 0.58, "Corporate": 0.29, "Education": 0.13}

# December and November lift; February is the trough.
SEASON = {1: .76, 2: .66, 3: .88, 4: .95, 5: 1.00, 6: 1.03,
          7: 1.06, 8: .98, 9: 1.08, 10: 1.18, 11: 1.45, 12: 1.72}


def build(rows: int, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    dates = pd.to_datetime(rng.choice(pd.date_range("2024-01-01", "2025-12-31"), rows))
    month = dates.month
    weekday = dates.dayofweek

    region = rng.choice(list(REGIONS), rows, p=[.27, .23, .29, .21])
    city = np.array([rng.choice(CITIES[r]) for r in region])
    category = rng.choice(list(CATEGORIES), rows, p=[.18, .14, .26, .17, .25])
    product = np.array([rng.choice(PRODUCTS[c]) for c in category])
    channel = rng.choice(list(CHANNELS), rows, p=list(CHANNELS.values()))
    segment = rng.choice(list(SEGMENTS), rows, p=list(SEGMENTS.values()))

    base_price = np.array([CATEGORIES[c][0] for c in category])
    base_margin = np.array([CATEGORIES[c][1] for c in category])
    return_rate = np.array([CATEGORIES[c][2] for c in category])

    unit_price = np.round(base_price * rng.uniform(0.88, 1.14, rows), 2)

    # Wholesale buys in bulk; weekends are busier in store.
    qty = rng.integers(1, 9, rows)
    qty = np.where(channel == "Wholesale", qty * rng.integers(4, 14, rows), qty)
    qty = np.where((weekday >= 5) & (channel == "Store"), qty + rng.integers(0, 3, rows), qty)

    # Discounting is heaviest on Wholesale, and it is what quietly kills margin.
    discount = np.clip(rng.beta(1.6, 9, rows) * 0.62, 0, 0.30)
    discount = np.where(channel == "Wholesale", np.clip(discount + 0.09, 0, 0.34), discount)
    discount = np.round(discount, 3)

    season = np.array([SEASON[m] for m in month])
    region_factor = np.array([REGIONS[r] for r in region])

    gross = unit_price * qty
    revenue = np.round(gross * (1 - discount) * season * region_factor, 2)
    # Every point of discount comes off the margin, and the South carries higher
    # fulfilment cost on top - so it trails on profitability, not just revenue.
    south_drag = np.where(region == "South", 0.035, 0.0)
    margin = np.clip(base_margin - discount * 0.9 - south_drag + rng.normal(0, 0.025, rows),
                     -0.10, 0.62)
    profit = np.round(revenue * margin, 2)
    cost = np.round(revenue - profit, 2)

    # The South ships slower, and slow shipping shows up in satisfaction.
    ship = rng.poisson(np.where(region == "South", 5.2, 3.1), rows) + 1
    ship = np.where(channel == "Store", 0, ship)

    satisfaction = np.clip(
        5.0 - ship * 0.14 - (discount > 0.25) * 0.3 + rng.normal(0, 0.5, rows), 1, 5)
    satisfaction = np.round(satisfaction, 1)
    satisfaction[rng.random(rows) < 0.07] = np.nan          # a few blanks, as in real life

    returned = rng.random(rows) < return_rate
    order_id = ["SO-{:06d}".format(n) for n in rng.choice(
        np.arange(100000, 999999), rows, replace=False)]

    frame = pd.DataFrame({
        "Order ID": order_id,
        "Order Date": [d.strftime("%d/%m/%Y") for d in dates],      # text dates, UK style
        "Region": region,
        "City": city,
        "Channel": channel,
        "Customer Segment": segment,
        "Category": category,
        "Product": product,
        "Quantity": qty,
        "Unit Price": ["${:,.2f}".format(v) for v in unit_price],   # money as text
        "Discount": discount,
        "Revenue": ["${:,.2f}".format(v) for v in revenue],         # money as text
        "Cost": np.round(cost, 2),
        "Profit": np.round(profit, 2),
        "Shipping Days": ship,
        "Satisfaction": satisfaction,
        "Returned": np.where(returned, "Yes", "No"),
    })
    return frame.sort_values("Order Date").reset_index(drop=True)


def desktop() -> str:
    """The Desktop the user actually sees.

    Windows may redirect the Desktop into OneDrive while leaving an empty
    Desktop folder behind in the user profile, so guessing by folder name writes
    somewhere the user never looks. Ask the shell where it really is.
    """
    if os.name == "nt":
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                path = winreg.QueryValueEx(handle, "Desktop")[0]
            if os.path.isdir(path):
                return path
        except Exception:  # noqa: BLE001 - fall back to the guesses below
            pass

    home = os.path.expanduser("~")
    for candidate in ("OneDrive/Desktop", "OneDrive - Personal/Desktop", "Desktop"):
        path = os.path.join(home, *candidate.split("/"))
        if os.path.isdir(path):
            return path
    return home


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    parser.add_argument("--rows", type=int, default=6000)
    args = parser.parse_args()

    target = args.out or os.path.join(desktop(), "ChatBI samples")
    os.makedirs(target, exist_ok=True)
    frame = build(args.rows)
    path = os.path.join(target, "omnichannel_sales.csv")
    frame.to_csv(path, index=False)

    print("Wrote {}".format(os.path.abspath(path)))
    print("  {:,} rows x {} columns".format(len(frame), len(frame.columns)))
    print("  dates and money are text on purpose - ChatBI converts them on import")
    print("\nSignals planted in the data:")
    print("  - November and December peak; the new year is the trough")
    print("  - South trails on revenue and margin, and ships ~2 days slower")
    print("  - Beauty is returned ~19% of the time; Grocery almost never")
    print("  - Wholesale moves volume at heavy discount, and margin suffers for it")
    print("  - Slower shipping tracks lower satisfaction")


if __name__ == "__main__":
    main()
