"""Write five practice datasets to your Desktop.

They are deliberately messy in realistic ways - money written as "$1,200.50", dates as
text, a few blanks - so you can see ChatBI clean them up on import.

    python make_sample_files.py            # writes to your Desktop
    python make_sample_files.py --out .    # or anywhere else
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd


def desktop_path() -> str:
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


def retail_sales(rng: np.random.Generator, rows: int = 900) -> pd.DataFrame:
    dates = pd.to_datetime(rng.choice(pd.date_range("2024-01-01", "2025-06-30"), rows))
    category = rng.choice(
        ["Electronics", "Apparel", "Home", "Grocery", "Beauty"], rows, p=[.25, .25, .2, .2, .1]
    )
    unit_price = pd.Series(category).map(
        {"Electronics": 320.0, "Apparel": 55.0, "Home": 90.0, "Grocery": 18.0, "Beauty": 34.0}
    ).to_numpy()
    quantity = rng.integers(1, 14, rows)
    revenue = np.round(unit_price * quantity * rng.uniform(0.8, 1.25, rows), 2)
    return pd.DataFrame({
        "Order Date": [d.strftime("%Y-%m-%d") for d in dates],     # dates as text
        "Region": rng.choice(["North", "South", "East", "West"], rows),
        "Category": category,
        "Product": rng.choice(["A-100", "B-220", "C-330", "D-440", "E-550", "F-660"], rows),
        "Channel": rng.choice(["Online", "Retail", "Wholesale"], rows, p=[.5, .35, .15]),
        "Quantity": quantity,
        "Revenue": ["${:,.2f}".format(v) for v in revenue],        # money as text
        "Profit": np.round(revenue * rng.uniform(0.08, 0.34, rows), 2),
        "Returned": rng.choice(["Yes", "No"], rows, p=[.06, .94]),
    })


def subscriptions(rng: np.random.Generator, rows: int = 700) -> pd.DataFrame:
    signups = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 540, rows), unit="D")
    plan = rng.choice(["Free", "Starter", "Pro", "Enterprise"], rows, p=[.4, .3, .22, .08])
    seats = np.where(plan == "Enterprise", rng.integers(25, 400, rows), rng.integers(1, 25, rows))
    price = pd.Series(plan).map(
        {"Free": 0, "Starter": 12, "Pro": 39, "Enterprise": 95}
    ).to_numpy()
    return pd.DataFrame({
        "SignupDate": signups,
        "Plan": plan,
        "Region": rng.choice(["NA", "EMEA", "APAC", "LATAM"], rows, p=[.4, .3, .2, .1]),
        "Channel": rng.choice(["Organic", "Paid", "Referral", "Partner"], rows),
        "Seats": seats,
        "MRR": np.round(seats * price * rng.uniform(0.85, 1.15, rows), 2),
        "ChurnRisk": np.round(rng.beta(2, 6, rows), 3),
        "SupportTickets": rng.poisson(1.6, rows),
    })


def marketing(rng: np.random.Generator, rows: int = 600) -> pd.DataFrame:
    spend = np.round(rng.gamma(3, 220, rows), 2)
    impressions = (spend * rng.uniform(60, 140, rows)).astype(int)
    clicks = (impressions * rng.uniform(0.004, 0.03, rows)).astype(int)
    return pd.DataFrame({
        "Week": pd.to_datetime(rng.choice(pd.date_range("2024-01-01", "2025-06-30", freq="W"), rows)),
        "Campaign": rng.choice(["Brand", "Retargeting", "Search", "Social", "Email"], rows),
        "Platform": rng.choice(["Google", "Meta", "LinkedIn", "TikTok"], rows),
        "Country": rng.choice(["US", "UK", "DE", "IN", "BR"], rows),
        "Spend": spend,
        "Impressions": impressions,
        "Clicks": clicks,
        "Conversions": rng.binomial(np.maximum(clicks, 1), 0.05),
    })


def support_tickets(rng: np.random.Generator, rows: int = 800) -> pd.DataFrame:
    opened = pd.Timestamp("2024-06-01") + pd.to_timedelta(rng.integers(0, 400, rows), unit="D")
    priority = rng.choice(["Low", "Medium", "High", "Urgent"], rows, p=[.4, .35, .2, .05])
    hours = np.round(
        pd.Series(priority).map({"Low": 40, "Medium": 20, "High": 8, "Urgent": 3}).to_numpy()
        * rng.uniform(0.4, 1.8, rows), 1)
    satisfaction = np.round(rng.uniform(1, 5, rows), 1)
    satisfaction[rng.random(rows) < 0.12] = np.nan          # some blanks
    return pd.DataFrame({
        "Opened": opened,
        "Team": rng.choice(["Billing", "Onboarding", "Technical", "Account"], rows),
        "Priority": priority,
        "Channel": rng.choice(["Email", "Chat", "Phone"], rows, p=[.5, .35, .15]),
        "ResolutionHours": hours,
        "Reopened": rng.choice([0, 1], rows, p=[.88, .12]),
        "Satisfaction": satisfaction,
    })


def hr_headcount(rng: np.random.Generator, rows: int = 500) -> pd.DataFrame:
    department = rng.choice(
        ["Engineering", "Sales", "Support", "Marketing", "Finance", "People"], rows,
        p=[.34, .22, .16, .12, .09, .07])
    base = pd.Series(department).map({
        "Engineering": 118000, "Sales": 92000, "Support": 61000,
        "Marketing": 84000, "Finance": 97000, "People": 76000}).to_numpy()
    return pd.DataFrame({
        "StartDate": pd.Timestamp("2019-01-01") + pd.to_timedelta(
            rng.integers(0, 2400, rows), unit="D"),
        "Department": department,
        "Location": rng.choice(["London", "Berlin", "Austin", "Bangalore", "Remote"], rows),
        "Level": rng.choice(["Junior", "Mid", "Senior", "Lead"], rows, p=[.28, .38, .25, .09]),
        "Salary": np.round(base * rng.uniform(0.82, 1.35, rows), -2),
        "EngagementScore": np.round(rng.uniform(2.0, 5.0, rows), 2),
        "DaysAbsent": rng.poisson(4.2, rows),
        "Attrition": rng.choice(["Active", "Left"], rows, p=[.85, .15]),
    })


BUILDERS = {
    "1_retail_sales.csv": retail_sales,
    "2_subscriptions.csv": subscriptions,
    "3_marketing_spend.csv": marketing,
    "4_support_tickets.xlsx": support_tickets,
    "5_hr_headcount.csv": hr_headcount,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="Where to write (default: your Desktop)")
    args = parser.parse_args()

    target = args.out or os.path.join(desktop_path(), "ChatBI samples")
    os.makedirs(target, exist_ok=True)
    rng = np.random.default_rng(7)

    print("Writing sample files to:\n  {}\n".format(os.path.abspath(target)))
    for filename, builder in BUILDERS.items():
        frame = builder(rng)
        path = os.path.join(target, filename)
        if filename.endswith(".xlsx"):
            # Two worksheets, so you can try "one dataset per worksheet" on import.
            with pd.ExcelWriter(path, engine="openpyxl") as writer:
                frame.to_excel(writer, sheet_name="Tickets", index=False)
                summary = (frame.groupby("Team")
                           .agg(Tickets=("Priority", "size"),
                                AvgHours=("ResolutionHours", "mean"))
                           .round(2).reset_index())
                summary.to_excel(writer, sheet_name="By team", index=False)
        else:
            frame.to_csv(path, index=False)
        print("  {:24s} {:>5,} rows x {:>2} columns".format(filename, len(frame), len(frame.columns)))

    print("\nDone. In ChatBI, open 'Add data' and drop these in - you can select all five "
          "at once.")


if __name__ == "__main__":
    main()
