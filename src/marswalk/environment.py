"""Environmental context: MSL RAD dose rate and MSL REMS weather. Historical observations, never live data."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

RAD_FILL = -1e30
MSL_SOL0_UTC = datetime(2012, 8, 6, 5, 17, 57)   # Curiosity landing (sol 0)
SOL_SECONDS = 88775.244


def sol_to_utc(sol: int) -> str:
    """Approximate Earth date of a sol (good to ~1 day)."""
    return (MSL_SOL0_UTC + timedelta(seconds=sol * SOL_SECONDS)).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- RAD
def load_rad(rad_dir: Path) -> pd.DataFrame:
    frames = [pd.read_csv(f, comment="#") for f in sorted(Path(rad_dir).glob("msl_rad_dose_*.csv"))]
    df = pd.concat(frames, ignore_index=True)
    df.columns = ["t", "dose_B", "dose_B_rtg", "dose_E"]
    df["t"] = pd.to_datetime(df["t"].str.slice(0, 19))
    return df


def radiation_summary(rad: pd.DataFrame, window_days: int, eva_hours: float, alert_factor: float,
                      event_date: str) -> dict:
    n_total = len(rad)
    fill = rad["dose_E"] < RAD_FILL
    r = rad.loc[~fill, ["t", "dose_E"]].sort_values("t").set_index("t")["dose_E"]

    last = r.index.max()
    win = r[r.index > last - pd.Timedelta(days=window_days)]
    ref = float(win.median())

    monthly = r.resample("MS").median().dropna()
    annual = r.groupby(r.index.year).median()

    # gaps longer than 7 days
    dt = r.index.to_series().diff()
    gaps = [{"from": (t - d).strftime("%Y-%m-%d"), "to": t.strftime("%Y-%m-%d"), "days": round(d.total_seconds() / 86400, 1)}
            for t, d in dt[dt > pd.Timedelta(days=7)].items()]

    # historical solar particle event: hourly medians around the date
    ev = pd.Timestamp(event_date)
    ev_series = r[(r.index >= ev - pd.Timedelta(days=2)) & (r.index < ev + pd.Timedelta(days=4))]
    ev_hourly = ev_series.resample("1h").median().dropna()
    baseline = float(r[(r.index >= ev - pd.Timedelta(days=9)) & (r.index < ev - pd.Timedelta(days=2))].median())
    # dose measured in the worst window with the same duration as the EVA (mean rate x duration)
    roll = ev_series.rolling(f"{int(eva_hours * 60)}min").mean()
    worst_end = roll.idxmax()
    eva_days = eva_hours / 24.0

    # climatology: hourly means grouped by calendar day (MM-DD) and UTC hour, over every year with data
    hourly_all = r.resample("1h").mean().dropna()
    clim = hourly_all.groupby([hourly_all.index.strftime("%m-%d"), hourly_all.index.hour]).agg(["mean", "min", "max", "count"])
    by_day: dict = {}
    for (day, hour), row in clim.iterrows():
        d = by_day.setdefault(day, {k: [None] * 24 for k in ("mean", "min", "max", "n")})
        d["mean"][hour], d["min"][hour], d["max"][hour] = (round(float(row[k]), 1) for k in ("mean", "min", "max"))
        d["n"][hour] = int(row["count"])

    return {
        "source": "NASA PDS / SPDF CDAWeb, MSL_RAD_OBS-L2, variable DOSIMETRY_TOTAL_f (quality filtered)",
        "quantity": "Absorbed dose rate in tissue-equivalent plastic (detector E), TOTAL_DOSE_E",
        "units": "uGy/day",
        "not": "Absorbed dose, NOT dose equivalent (mSv). Measured inside the rover's environment, not on a person.",
        "rows_total": n_total,
        "fill_fraction": round(float(fill.mean()), 4),
        "first": r.index.min().strftime("%Y-%m-%d"), "last": last.strftime("%Y-%m-%d %H:%M"),
        "gaps": gaps,
        "reference": {"value": round(ref, 1), "window_days": window_days,
                      "from": (last - pd.Timedelta(days=window_days)).strftime("%Y-%m-%d"),
                      "to": last.strftime("%Y-%m-%d"),
                      "label": "Measured by Curiosity RAD (median of the last %d days with data)" % window_days},
        "annual_median": {str(k): round(float(v), 1) for k, v in annual.items()},
        "monthly_median": [{"month": k.strftime("%Y-%m"), "value": round(float(v), 1)} for k, v in monthly.items()],
        "illustrative_budget": {
            "value_uGy": round(ref * eva_days, 1), "eva_hours": eva_hours,
            "label": "Illustrative EVA budget = reference rate x EVA duration. Not an estimated human dose: "
                     "no suit, no body self-shielding, no quality factor."},
        "actual_astronaut_exposure": "not modelled",
        "alert": {"factor": alert_factor, "threshold": round(ref * alert_factor, 1),
                  "label": "Illustrative alert (assumption): rate above %g x reference" % alert_factor},
        "climatology": {
            "label": "Hourly mean dose rate by calendar day and UTC hour, over all years with data (historical).",
            "hour_axis": "UTC (RAD timestamps; not Mars local time)",
            "years": [int(hourly_all.index.year.min()), int(hourly_all.index.year.max())],
            "hourly_range": {"min": round(float(hourly_all.min()), 1), "max": round(float(hourly_all.max()), 1)},
            "by_day": by_day,
        },
        "solar_event": {
            "date": event_date,
            "label": "Historical scenario: what RAD measured during the %s solar particle event. Not a prediction." % event_date,
            "baseline_before": round(baseline, 1),
            "peak_value": round(float(ev_series.max()), 1),
            "peak_time_utc": ev_series.idxmax().strftime("%Y-%m-%d %H:%M"),
            "worst_eva_window": {"end_utc": worst_end.strftime("%Y-%m-%d %H:%M"), "hours": eva_hours,
                                 "dose_uGy": round(float(roll.max()) * eva_days, 1)},
            "hourly": [{"t": k.strftime("%Y-%m-%d %H:%M"), "value": round(float(v), 1)} for k, v in ev_hourly.items()],
        },
    }


# --------------------------------------------------------------------------- REMS
def _rems_columns(xml_text: str) -> list[str]:
    return [re.search(r"<name>(.*?)</name>", f).group(1)
            for f in re.findall(r"<Field_Character>(.*?)</Field_Character>", xml_text, flags=re.S)]


def _flag_ok(series: pd.Series, positions: list[int]) -> pd.Series:
    s = series.astype(str).str.strip()
    ok = pd.Series(True, index=series.index)
    for p in positions:
        ok &= s.str.len().gt(p) & (s.str[p] == "1")
    return ok


def load_rems_sol(sol_dir: Path) -> pd.DataFrame:
    xml = next(sol_dir.glob("*.xml")).read_text(encoding="utf-8", errors="replace")
    cols = _rems_columns(xml)
    flags = {c: str for c in cols if c.endswith("CONFIDENCE_LEVEL") or c in ("LMST", "LTST")}
    t = pd.read_csv(next(sol_dir.glob("*.TAB")), header=None, names=cols, skipinitialspace=True, dtype=flags)
    t["hour"] = t["LMST"].str.slice(6, 8).astype(int) + t["LMST"].str.slice(9, 11).astype(int) / 60.0
    clean = lambda c: t[c].where(t[c] > -998)  # noqa: E731
    # Confidence rules (from the PDS4 label; byte 0 = leftmost):
    #   ground temp (GTS): byte0 power supply in range AND byte1 high-quality recalibration
    #   air temp: byte2 power supply in range
    #   UV: byte3 no shadow on the sensor
    t["ground_c"] = clean("BRIGHTNESS_TEMP").where(_flag_ok(t["GTS_CONFIDENCE_LEVEL"], [0, 1])) - 273.15
    t["air_c"] = clean("AMBIENT_TEMP").where(_flag_ok(t["AMBIENT_TEMP_CONFIDENCE_LEVEL"], [2])) - 273.15
    t["pressure_pa"] = clean("PRESSURE")
    t["uv_w_m2"] = clean("UV_ABC").where(_flag_ok(t["UVS_CONFIDENCE_LEVEL"], [3]))
    t["rh_pct"] = clean("LOCAL_RELATIVE_HUMIDITY")
    t["wind_valid"] = t["HORIZONTAL_WIND_SPEED"] > -998
    return t


def weather_summary(rems_dir: Path, start_lmst: str, eva_hours: float) -> dict:
    sols = []
    for d in sorted(Path(rems_dir).glob("SOL*")):
        sol = int(d.name[3:])
        t = load_rems_sol(d)
        g = t.groupby(np.floor(t["hour"]).astype(int))[["air_c", "ground_c", "pressure_pa", "uv_w_m2", "rh_pct"]].median()
        hourly = [{"hour": int(h), **{k: (None if pd.isna(v) else round(float(v), 2)) for k, v in row.items()}}
                  for h, row in g.iterrows()]
        gmax = g["ground_c"].idxmax() if g["ground_c"].notna().any() else None
        sols.append({
            "sol": sol, "date_utc_approx": sol_to_utc(sol), "rows": len(t),
            "valid_fraction": {k: round(float(t[k].notna().mean()), 3) for k in ["air_c", "ground_c", "pressure_pa", "uv_w_m2"]},
            "wind_valid_fraction": round(float(t["wind_valid"].mean()), 4),
            "ground_c_range": [round(float(g["ground_c"].min()), 1), round(float(g["ground_c"].max()), 1)],
            "air_c_range": [round(float(g["air_c"].min()), 1), round(float(g["air_c"].max()), 1)],
            "warmest_ground_hour_lmst": None if gmax is None else int(gmax),
            "hourly": hourly,
        })
    h0, m0 = (int(x) for x in start_lmst.split(":"))
    end_min = int(round(h0 * 60 + m0 + eva_hours * 60)) % (24 * 60)
    return {
        "source": "NASA PDS Atmospheres Node, MSL REMS bundle, MODRDR (RMD) environmental product",
        "label": "Historical REMS observations at Gale Crater (not current weather, not a forecast)",
        "hour_axis": "Local Mean Solar Time (LMST), hourly medians of 1 Hz samples",
        "units": {"air_c": "degC", "ground_c": "degC", "pressure_pa": "Pa", "uv_w_m2": "W/m2", "rh_pct": "%"},
        "quality": "-999 removed; confidence flags applied: ground temp (GTS bytes 0,1), air temp (byte 2), UV (byte 3)",
        "wind": "not available in these sols (almost no valid samples)",
        "eva_window_lmst": {"start": start_lmst, "end": f"{end_min // 60:02d}:{end_min % 60:02d}", "hours": eva_hours},
        "sols": sols,
    }
