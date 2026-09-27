"""Portfolio design: set a target allocation with linked sliders, compare it with today's, save it.

Percentages are of the portfolio: investments at market value, valued exactly as on the
dashboard (``md.live_valuations``), plus Dry Powder. The cash reserve in current accounts
is outside the portfolio and outside the design. The saved design is what the dashboard
measures the allocation against.
"""

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import market_data as md
import portfolio_math as pm
from database import get_all_transactions, get_cash_accounts, get_portfolio_design, save_portfolio_design

BAND_COLOURS = {
    "Dry Powder": "#c9a227",
    "Bonds": "#4c78a8",
    "Equity funds": "#72b7b2",
    "Stocks": "#f58518",
    "Other": "#b279a2",
}

DRIFT_HELP = {
    "Off by (pp)": "Today minus design, in percentage points of the portfolio: positive is over the design.",
    "To reach design (EUR)": (
        "Euros to add (+) or take out (−) of the category to match the design at today's "
        "portfolio value. The column adds up to zero: rebalancing moves money, it does not add any."
    ),
}

st.title("Portfolio design")

# ==========================================================================================
# Today's allocation
# ==========================================================================================

transactions, _ = pm.load_transactions(get_all_transactions())
positions = {aid: pos for aid, pos in pm.build_positions(transactions).items() if pos.is_open}
valuations, _, _ = md.live_valuations(positions)
accounts, cash_problems = pm.clean_cash_accounts(get_cash_accounts())
dry_total = sum(a.balance_eur for a in accounts if a.category != pm.RESERVE_CATEGORY)
reserve_total = sum(a.balance_eur for a in accounts if a.category == pm.RESERVE_CATEGORY)

# capital_by_band leaves the cash reserve out by itself.
current = pm.capital_by_band(
    [(positions[aid].category, val.market_value_eur) for aid, val in valuations.items()]
    + [(a.category, a.balance_eur) for a in accounts]
)
portfolio_value = sum(current.values())

unvalued = [positions[aid].name or aid for aid, val in valuations.items() if val.market_value_eur is None]
if unvalued:
    st.warning(f"Not priced, so left out of the total: {', '.join(unvalued)}.")
for problem in cash_problems:
    st.warning(f"Cash account skipped: {problem}")

if portfolio_value <= 0:
    st.info("Nothing to design yet. Log a transaction, or a Dry Powder account on the Log Transactions page.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric(
    "Portfolio value (EUR)",
    f"€{portfolio_value:,.0f}",
    help=f"Investments at market value plus Dry Powder. The €{reserve_total:,.0f} cash reserve is not part of it.",
)
m2.metric("Investments (EUR)", f"€{portfolio_value - dry_total:,.0f}")
m3.metric("Dry Powder (EUR)", f"€{dry_total:,.0f}")

saved_doc = get_portfolio_design() or {}
saved, design_problems = pm.clean_design(saved_doc.get("targets"))
for problem in design_problems:
    st.warning(f"Saved design ignored: {problem}")

flash = st.session_state.pop("design_flash", None)
if flash:
    st.success(flash)

# ==========================================================================================
# The sliders
# ==========================================================================================

# Seeded once per session -- from the saved design, or today's allocation rounded to
# exactly 100% -- and after that set only by the widgets and the callback below.
# Giving a slider a default *and* setting it from a callback makes Streamlit warn.
start = saved or pm.round_to_100({band: current.get(band, 0.0) for band in pm.DESIGN_BANDS})
for band in pm.DESIGN_BANDS:
    st.session_state.setdefault(f"design_{band}", int(round(start[band])))
    st.session_state.setdefault(f"lock_{band}", False)


def _slider_moved(changed: str) -> None:
    """Runs before the rerun, so it may set the other sliders: they absorb the move."""
    values = {band: st.session_state[f"design_{band}"] for band in pm.DESIGN_BANDS}
    locked = [band for band in pm.DESIGN_BANDS if st.session_state[f"lock_{band}"]]
    for band, value in pm.rebalance(values, changed, locked).items():
        st.session_state[f"design_{band}"] = value


@st.fragment  # a slider or lock reruns this block only, not the valuations above
def designer() -> None:
    st.subheader("Target allocation")
    st.caption(
        "Share of the portfolio for each category; they always add up to 100%. Moving one slider "
        "takes the difference from the unlocked others, in proportion to their size. Lock a "
        "category to keep it where it is."
    )

    for column, band in zip(st.columns(len(pm.DESIGN_BANDS)), pm.DESIGN_BANDS):
        locked = st.session_state[f"lock_{band}"]
        column.slider(
            band,
            min_value=0,
            max_value=100,
            format="%d%%",
            key=f"design_{band}",
            on_change=_slider_moved,
            args=(band,),
            disabled=locked,
        )
        column.checkbox("🔒 Lock", key=f"lock_{band}")
        column.caption(
            f"€{portfolio_value * st.session_state[f'design_{band}'] / 100:,.0f} · "
            f"today {current.get(band, 0.0) / portfolio_value:.1%}"
        )

    design = {band: st.session_state[f"design_{band}"] for band in pm.DESIGN_BANDS}
    free = [band for band in pm.DESIGN_BANDS if not st.session_state[f"lock_{band}"]]
    total = sum(design.values())

    status, button = st.columns([4, 1], vertical_alignment="center")
    if total != 100:  # cannot happen through the sliders; kept as a guard for the save
        status.warning(f"The design adds up to {total}%, not 100%.")
    elif len(free) < 2:
        status.caption("Unlock at least two categories to move the sliders.")
    elif saved and all(design[band] == saved[band] for band in pm.DESIGN_BANDS):
        status.caption(f"This is your saved design (saved {saved_doc.get('saved') or 'on an unknown date'}).")
    elif saved:
        status.caption(f"Changed from the saved design of {saved_doc.get('saved') or 'an unknown date'}; not saved yet.")
    else:
        status.caption("No design saved yet.")

    if button.button("Save design", type="primary", disabled=total != 100, key="save_design", width="stretch"):
        try:
            save_portfolio_design({"targets": dict(design), "saved": date.today().isoformat()})
        except Exception as exc:
            st.error(f"Error saving the design: {exc}")
        else:
            st.session_state["design_flash"] = "Design saved: " + " · ".join(
                f"{band} {design[band]}%" for band in pm.DESIGN_BANDS
            )
            st.rerun()  # the whole page, so the status line reads the new saved design

    # --- today against the design ---------------------------------------------------------
    fig = go.Figure()
    for band in pm.ALLOCATION_BANDS:
        today_pct = current.get(band, 0.0) / portfolio_value * 100
        design_pct = design.get(band, 0)  # Other has no slider: its design is 0%
        if today_pct <= 0 and design_pct <= 0:
            continue
        fig.add_bar(
            y=["Design", "Today"],  # the first is drawn at the bottom
            x=[design_pct, today_pct],
            orientation="h",
            name=band,
            marker_color=BAND_COLOURS[band],
            text=[f"{band} {design_pct:.0f}%", f"{band} {today_pct:.0f}%"],
            textposition="inside",
            textangle=0,
            insidetextanchor="middle",
            hovertemplate=f"{band}, %{{y}}: %{{x:.1f}}%<extra></extra>",
        )
    fig.update_layout(
        barmode="stack",
        height=230,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(range=[0, 100], ticksuffix="%"),
        # "normal" so the legend reads left to right like the bars; stacked bars reverse it by default.
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, traceorder="normal"),
        # A band too narrow for its label hides the label; the hover and the table carry the number.
        uniformtext=dict(minsize=11, mode="hide"),
        template="plotly_white",
    )
    st.plotly_chart(fig, width="stretch")

    drift = pm.allocation_drift(current, design)
    st.dataframe(
        pd.DataFrame(
            [
                {  # percentages first, then the same comparison in euros
                    "Category": d.band,
                    "Today (%)": d.current_pct,
                    "Design (%)": d.design_pct,
                    "Off by (pp)": d.off_by_pp,
                    "Today (EUR)": d.current_eur,
                    "Design (EUR)": d.design_eur,
                    "To reach design (EUR)": d.to_design_eur,
                }
                for d in drift
            ]
        ).style.format(
            {
                "Today (%)": "{:.1f} %",
                "Today (EUR)": "€ {:,.0f}",
                "Design (%)": "{:.0f} %",
                "Design (EUR)": "€ {:,.0f}",
                "Off by (pp)": "{:+.1f}",
                "To reach design (EUR)": "€ {:+,.0f}",
            }
        ),
        column_config={col: st.column_config.Column(col, help=text) for col, text in DRIFT_HELP.items()},
        hide_index=True,
        width="stretch",
    )


designer()

saved_cash = max((a.updated for a in accounts if a.updated), default=None)
st.caption(
    "Investments at live prices, grouped by the category each was logged with: Fund and ETF count "
    "as Equity funds, Bonds and Fixed Income as Bonds, and Cash/Money Market as Dry Powder; anything "
    "else is Other, which a design targets at 0%. Dry Powder as entered on the Log Transactions page"
    + (f", last saved {saved_cash}" if saved_cash else "")
    + f". Your cash reserve (€{reserve_total:,.0f} in current accounts) is outside the portfolio "
    "and the design."
)
