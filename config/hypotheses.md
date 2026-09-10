# Hypothesis register

One entry per row of PLAN.md section 9. The weekly report lists the entries due
for review with the current readings of the indicators named; the research
harness (`invest/research/`) holds the scripts that measure them. Update
`Outcome` and `Next review` when a measurement is made, and never before.

Format: a level-two heading `## Hn. Title`, then `- **Field:** value` lines.
`Indicators` names catalog series ids whose latest readings the report prints.

## H1. Inflation is disguised as growth
- **Status:** open
- **Next review:** 2026-12-01
- **Measurement:** Nominal versus CPI-deflated S&P earnings growth by decade; real P/E (CAPE).
- **Data:** Shiller (SHILLER_EARNINGS, SHILLER_CPI), CPIAUCSL.
- **Indicators:** SP500_NOMINAL_EPS_GROWTH, SP500_REAL_EPS_GROWTH, SP500_CAPE
- **Caveat:** Roughly 40% of S&P 500 revenue is foreign, so a weaker dollar lifts reported earnings by translation; separate that with a DXY-versus-EPS regression before attributing it to inflation.
- **Script:** invest.research.hypotheses:h1_real_earnings
- **Outcome:** not yet measured

## H2. Debt gets cheaper to repay as inflation rises, and leveraged holders win
- **Status:** open
- **Next review:** 2026-12-01
- **Measurement:** Real yields versus asset returns; margin debt and corporate debt growth versus real rates.
- **Data:** DFII10, FINRA margin debt, Z.1.
- **Indicators:** US_REAL_10Y, US_MARGIN_DEBT, US_CREDIT_GAP
- **Caveat:** True for fixed-rate debt, not floating; the relevant variable is the real rate, not inflation alone.
- **Outcome:** not yet measured

## H3. Concentrated ownership means the big players set the direction
- **Status:** open
- **Next review:** 2027-03-01
- **Measurement:** Ownership shares over time; whether positioning extremes predict forward returns.
- **Data:** Fed DFA, COT, NAAIM, AAII.
- **Indicators:** COT_SP500_NET_SPEC, NAAIM_EXPOSURE, AAII_BULL_BEAR
- **Caveat:** Positioning measures are modestly contrarian in the literature; expect small effects.
- **Outcome:** not yet measured

## H4. Consumers spend on debt, and markets fall when they stop
- **Status:** open
- **Next review:** 2026-12-01
- **Measurement:** Real retail sales, revolving credit, delinquencies and the saving rate versus forward index returns and recessions.
- **Data:** RRSFS, REVOLSL, DRCCLACBS, PSAVERT, NY Fed HHDC.
- **Indicators:** US_REAL_RETAIL_SALES, US_REVOLVING_CREDIT, US_CARD_DELINQUENCY, US_SAVING_RATE
- **Caveat:** Consumption is about 68% of US GDP; the turn is usually visible in claims first.
- **Outcome:** not yet measured

## H5. The labor market lags and then breaks
- **Status:** open
- **Next review:** 2026-12-01
- **Measurement:** Lead time of claims over the unemployment rate; claims rules versus the Sahm rule as recession calls.
- **Data:** ICSA, UNRATE, SAHMREALTIME, USREC.
- **Indicators:** US_CLAIMS_4WK_YOY, US_CLAIMS_OFF_LOW, US_SAHM, US_UNRATE
- **Caveat:** About eight recessions since 1970; n is tiny and is printed.
- **Script:** invest.research.hypotheses:h5_claims_lead
- **Outcome:** not yet measured

## H6. Liquidity drives prices
- **Status:** open
- **Next review:** 2026-12-01
- **Measurement:** Net liquidity 13-week change versus forward index returns.
- **Data:** WALCL, WTREGEN, RRPONTSYD, ^GSPC.
- **Indicators:** US_NET_LIQUIDITY_13W, SPX_VS_200D
- **Caveat:** The 2020 to 2022 episode dominates the sample.
- **Script:** invest.research.hypotheses:h6_liquidity
- **Outcome:** not yet measured

## H7. Crash fatigue, then a confidence break
- **Status:** open
- **Next review:** 2027-03-01
- **Measurement:** Event study on HY OAS spikes, VIX spikes and curve un-inversions: forward return distribution at 1, 3, 6 and 12 months.
- **Data:** BAMLH0A0HYM2, VIXCLS, T10Y3M.
- **Indicators:** US_HY_OAS, VIX, US_CURVE_10Y3M
- **Caveat:** Overlapping windows; n and intervals are printed.
- **Script:** invest.research.hypotheses:h7_stress_events
- **Outcome:** not yet measured

## H8. The longer the crash is delayed, the bigger it is
- **Status:** open
- **Next review:** 2027-03-01
- **Measurement:** Regress drawdown depth on time since the previous 20% drawdown, monthly since 1871.
- **Data:** Shiller.
- **Indicators:** SPX_DRAWDOWN
- **Caveat:** Cheap and decisive either way.
- **Script:** invest.research.hypotheses:h8_drawdown_gaps
- **Outcome:** First measurement 2026-09-10 (Shiller real price 1871 to 2026; the stored `research` snapshot carries the code version): 12 real drawdowns deeper than 20%, 11 with a preceding episode; correlation between the years since the previous trough and the depth 0.09, slope 0.002 per year. No support: with n = 11 the sign is noise, and the 1929 and 2000 to 2009 episodes sit at opposite ends of the gap scale.

## H9. The end of the Ukraine war lifts markets but not defence
- **Status:** open
- **Next review:** 2027-03-01
- **Measurement:** Pre-registered effects on EU indices, defence names and energy over a stated window, measured after the event.
- **Data:** yfinance.
- **Indicators:** STOXX600_VS_200D, OIL_BRENT
- **Caveat:** A single event: a case study, not a statistic.
- **Outcome:** not yet measured

## H10. Manufacturing surveys no longer call the cycle in a services economy
- **Status:** open
- **Next review:** 2027-06-01
- **Measurement:** Lead and hit rate of ISM Manufacturing versus ISM Services and the regional Fed surveys against USREC, by decade.
- **Data:** ISM captures, regional Fed surveys, USREC.
- **Indicators:** US_ISM_MFG_PMI, US_ISM_SERVICES_PMI, US_REGIONAL_FED_AVG
- **Caveat:** ISM Services history starts in 1997; the manufacturing share of GDP has roughly halved since 1970.
- **Outcome:** not yet measured

## H11. Buying dips beats holding
- **Status:** open
- **Next review:** 2026-12-01
- **Measurement:** Rules (buy after -10%, -20%, below the 200-day average, %B below 0) versus buy-and-hold with monthly contributions, walk-forward, since 1928 and since 1990.
- **Data:** Shiller, yfinance.
- **Indicators:** SPX_DRAWDOWN, SPX_VS_200D, SPX_PCT_B
- **Caveat:** Do this before considering any model.
- **Script:** invest.research.hypotheses:h11_dip_buying
- **Outcome:** First measurement 2026-09-10 on the Shiller real price, one unit a month, dividends excluded on both sides, idle cash earning nothing: since 1928 holding turned 1,185 units into 13.6x, buy-after-10% 13.4x, buy-after-20% 13.5x, buy-below-the-10-month-average 13.3x; since 1990 holding 3.8x against 3.3x, 2.9x and 3.7x. Waiting for dips lost to investing at once in both samples, and the gap widens in the sample with fewer crashes. Idle cash at the end (42 units for the 20% rule) is part of the cost. Not yet tested: a cash yield on the idle balance, which would narrow but, at historical bill yields, not close the gap.
