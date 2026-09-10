# Weekly report, 2026-09-10


[TOC]

## Header

Portfolio value €11,725 on 2026-09-10 (source: provided); total P&L €3,834 (37.1% of cost deployed); money-weighted return 30.10% a year.

Macro regime: **expansion** as of 2026-09-09. 0 counted rule(s) firing.

Data freshness: manual 33, missing 167, ok 7.

Critical series stale or failed: US_10Y, EA_DFR, EA_10Y, US_FEDFUNDS, US_POLICY_STANCE, US_CFNAI_MA3, US_EBP, US_CPI, US_CORE_PCE, EA_HICP, US_CLAIMS, US_PERMITS, US_SLOOS_TIGHTENING, SP500_CAPE.


## Portfolio

**Weights against targets**

| Bucket | Value | Weight | Target | Drift (pts) | Band | Out of band |
|---|---|---|---|---|---|---|
| bonds | €0 | 0.0% | 20% | -20.0 | 15.0% to 25.0% | yes |
| cash | €0 | 0.0% | 10% | -10.0 | 7.5% to 12.5% | yes |
| crypto | €0 | 0.0% | 5% | -5.0 | 3.8% to 6.2% | yes |
| equity | €11,725 | 100.0% | 65% | +35.0 | 60.0% to 70.0% | yes |


**Proposed trades** (bucket level; pick the holding)

| Action | Bucket | Amount | Why | Candidates (funds first) | Traspaso |
|---|---|---|---|---|---|
| buy | bonds | €2,345 | 0.0% vs target 20% (band 15.0% to 25.0%), to target | – |  |
| buy | cash | €1,172 | 0.0% vs target 10% (band 7.5% to 12.5%), to target | – |  |
| buy | crypto | €586 | 0.0% vs target 5% (band 3.8% to 6.2%), to target | – |  |
| sell | equity | €4,104 | 100.0% vs target 65% (band 60.0% to 70.0%), to target | Drifted Cost Fund (Fund); Plain EUR Fund (Fund); Legacy Schema Fund (Fund) |  |


**Taxable gain of the proposed sales, FIFO lots** (Spain matches sales against the oldest purchases; the app measures performance with average cost, so the two splits differ while the total is the same)

| Holding | Units | Proceeds | FIFO gain | Note |
|---|---|---|---|---|
| Partially Sold ETF | 136.79 | €4,104 | €1,868 | 2 lot(s), oldest 2025-02-01 |


**Where new cash would go** (illustrative 1,000 EUR; no contribution is configured): bonds €1,000.

Notes: sells 4,104 EUR, buys 4,104 EUR; difference +0 EUR stays in or comes from cash

Data quality notes on transactions: 4 (see the app).


## Macro

**Regime: expansion** (as of 2026-09-09). No counted rule is firing. Fewer than two groups fire: expansion. Not evaluated for lack of data: claims_yoy, claims_off_low, cape_percentile, aaii_contrarian, naaim_extreme, trend_broken, ism_manufacturing, ism_services, lei_signal, cfnai_recession, bank_tightening, ebp_top_decile, permits_falling, policy_restrictive. The label summarises the rules; it does not predict anything.

Not evaluated for lack of data: claims_yoy, claims_off_low, cape_percentile, aaii_contrarian, naaim_extreme, trend_broken, ism_manufacturing, ism_services, lei_signal, cfnai_recession, bank_tightening, ebp_top_decile, permits_falling, policy_restrictive.

No earlier snapshot to compare with; changes appear from the next run.

**Rates and curve**

| Indicator | Value | As of | z | Pct | Concern | 2y |
|---|---|---|---|---|---|---|


**Policy stance and currency**

| Indicator | Value | As of | z | Pct | Concern | 2y |
|---|---|---|---|---|---|---|


**Credit and liquidity**

| Indicator | Value | As of | z | Pct | Concern | 2y |
|---|---|---|---|---|---|---|


**Labor**

| Indicator | Value | As of | z | Pct | Concern | 2y |
|---|---|---|---|---|---|---|


An asterisk marks a series older than its publication schedule allows. Sparklines show the last two years of the transformed series.


## Positioning

No positioning series has data yet (COT arrives with the refresh; AAII, NAAIM and flows are hand-entered).

**What the tracked managers did** (13F: long US positions only, up to 45 days late)

Berkshire Hathaway: positions as of 2026-06-30, filed 2026-08-14 (45 days later; long US positions only, no shorts, cash or foreign listings). 10 positions worth $217.2bn; the top position is 24% of the portfolio, the top five 76%, the top ten 100%. New: ALPHABET INC ($9.6bn). Added more than 25%: ALPHABET INC (+59%, $11.7bn). Cut more than 25%: BANK OF AMER CORP (-29%, $-3.7bn).


## Opportunities


**Magic formula (earnings yield + ROIC)**

| # | Ticker | Name | EBIT/EV | ROIC | FCF yield | F | P/B | Margin of safety | Data to |
|---|---|---|---|---|---|---|---|---|---|
| 1 | SAP | SAP SE | 7.0% | 194.9% | – | 7 | 3.1 | -162% | 2025-12-31 |
| 2 | MMM | 3M CO | 5.4% | 61.7% | 5.2% | 5 | 25.8 | -730% | 2026-06-30 |


**Quality-value composite**

| # | Ticker | Name | EBIT/EV | ROIC | FCF yield | F | P/B | Margin of safety | Data to |
|---|---|---|---|---|---|---|---|---|---|
| 1 | SAP | SAP SE | 7.0% | 194.9% | – | 7 | 3.1 | -162% | 2025-12-31 |
| 2 | MMM | 3M CO | 5.4% | 61.7% | 5.2% | 5 | 25.8 | -730% | 2026-06-30 |


Excluded as possible value traps: JPM (Piotroski F-score 2 at or below 3).

**Watchlist** (price against the conservative value)

| Symbol | Price | Value | Margin of safety | Note |
|---|---|---|---|---|
| MMM | 147.72 | 17.80 | -730% | seeded |



## Hypothesis register

No hypothesis is due for review this week.


## Missing and failed

**Series stale, failed or missing** (their rows above carry the last value with its date, or are absent)

| Series | Status | Last obs | Age (days) | Limit |  | Message |
|---|---|---|---|---|---|---|
| US_3M | missing | – | – | 10 |  |  |
| US_2Y | missing | – | – | 10 |  |  |
| US_10Y | missing | – | – | 10 | critical |  |
| US_30Y | missing | – | – | 10 |  |  |
| US_CURVE_10Y2Y | missing | – | – | 10 |  |  |
| US_REAL_10Y | missing | – | – | 10 |  |  |
| US_REAL_5Y | missing | – | – | 10 |  |  |
| US_BREAKEVEN_10Y | missing | – | – | 10 |  |  |
| US_FEDFUNDS_MINUS_2Y | missing | – | – | 70 |  |  |
| EA_DFR | missing | – | – | – | critical |  |
| EA_10Y | missing | – | – | 10 | critical |  |
| EA_2Y | missing | – | – | 10 |  |  |
| EA_3M | missing | – | – | 10 |  |  |
| EA_CURVE_10Y2Y | missing | – | – | 10 |  |  |
| US_NYFED_RECPROB | missing | – | – | 79 |  |  |
| US_RECPROB_SMOOTHED | missing | – | – | 164 |  |  |
| US_FEDFUNDS | missing | – | – | 70 | critical |  |
| US_DFF | missing | – | – | 10 |  |  |
| US_RSTAR | missing | – | – | 266 |  |  |
| EA_RSTAR | missing | – | – | 266 |  |  |
| US_POLICY_STANCE | missing | – | – | 266 | critical |  |
| EA_REAL_POLICY_RATE | missing | – | – | 86 |  |  |
| US_DOLLAR_BROAD | missing | – | – | 13 |  |  |
| DXY | missing | – | – | 10 |  |  |
| COPPER | missing | – | – | 10 |  |  |
| GOLD | missing | – | – | 10 |  |  |
| COPPER_GOLD | missing | – | – | 10 |  |  |
| GOLD_EUR | missing | – | – | 10 |  |  |
| US_ISM_NEW_ORDERS_MINUS_INVENTORIES | missing | – | – | – |  |  |
| US_EMPIRE_STATE | missing | – | – | 69 |  |  |
| US_PHILLY_FED | missing | – | – | 69 |  |  |
| US_PHILLY_FED_CSV | missing | – | – | 69 |  |  |
| US_PHILLY_NEW_ORDERS | missing | – | – | 69 |  |  |
| US_DALLAS_FED | missing | – | – | 69 |  |  |
| US_REGIONAL_FED_AVG | missing | – | – | 69 |  |  |
| EA_ESI | missing | – | – | 69 |  |  |
| EA_ESI_EA20 | missing | – | – | 69 |  |  |
| US_CFNAI | missing | – | – | 94 |  |  |
| US_CFNAI_MA3 | missing | – | – | 94 | critical |  |
| OECD_CLI_US | missing | – | – | 109 |  |  |
| OECD_CLI_G20 | missing | – | – | 109 |  |  |
| US_EBP | missing | – | – | 84 | critical |  |
| US_EBP_RECPROB | missing | – | – | 84 |  |  |
| US_CPI | missing | – | – | 82 | critical |  |
| US_CPI_3M_ANN | missing | – | – | 82 |  |  |
| US_CORE_CPI | missing | – | – | 82 |  |  |
| US_CORE_PCE | missing | – | – | 99 | critical |  |
| US_MEDIAN_CPI | missing | – | – | 82 |  |  |
| US_TRIMMED_PCE | missing | – | – | 99 |  |  |
| US_STICKY_CPI | missing | – | – | 82 |  |  |
| US_MICH_INFLATION_EXP | missing | – | – | 99 |  |  |
| US_EXPINF_10Y | missing | – | – | 84 |  |  |
| US_AHE | missing | – | – | 76 |  |  |
| US_REAL_WAGES | missing | – | – | 82 |  |  |
| EA_HICP | missing | – | – | 86 | critical |  |
| EA_HICP_CORE | missing | – | – | 86 |  |  |
| ES_HICP | missing | – | – | 86 |  |  |
| OIL_WTI | missing | – | – | 17 |  |  |
| OIL_BRENT | missing | – | – | 17 |  |  |
| US_GASOLINE | missing | – | – | 22 |  |  |
| US_CLAIMS | missing | – | – | 26 | critical |  |
| US_CLAIMS_4WK | missing | – | – | 26 |  |  |
| US_CLAIMS_4WK_YOY | missing | – | – | 26 |  |  |
| US_CLAIMS_OFF_LOW | missing | – | – | 26 |  |  |
| US_CONTINUING_CLAIMS | missing | – | – | 33 |  |  |
| US_PAYEMS | missing | – | – | 76 |  |  |
| US_PAYROLLS_3M | missing | – | – | 76 |  |  |
| US_TEMP_HELP | missing | – | – | 76 |  |  |
| US_MFG_HOURS | missing | – | – | 76 |  |  |
| US_JOB_OPENINGS | missing | – | – | 104 |  |  |
| US_UNEMPLOYED | missing | – | – | 76 |  |  |
| US_VU_RATIO | missing | – | – | 104 |  |  |
| US_QUITS_RATE | missing | – | – | 104 |  |  |
| EA_UNRATE | missing | – | – | 101 |  |  |
| ES_UNRATE | missing | – | – | 101 |  |  |
| US_PERMITS | missing | – | – | 88 | critical |  |
| US_STARTS | missing | – | – | 88 |  |  |
| US_EXISTING_HOME_SALES | missing | – | – | 94 |  |  |
| US_MORTGAGE_30Y | missing | – | – | 22 |  |  |
| US_CORE_CAPEX_ORDERS | missing | – | – | 96 |  |  |
| US_CORE_CAPEX_REAL_YOY | missing | – | – | 96 |  |  |
| US_CAPACITY_UTIL | missing | – | – | 86 |  |  |
| US_INDPRO | missing | – | – | 86 |  |  |
| KR_EXPORTS | missing | – | – | 114 |  |  |
| EA_INDPRO | missing | – | – | 114 |  |  |
| EA_GDP_YOY | missing | – | – | 236 |  |  |
| US_GDP_REAL | missing | – | – | 221 |  |  |
| US_UMCSENT | missing | – | – | 99 |  |  |
| EA_CONSUMER_CONFIDENCE | missing | – | – | 69 |  |  |
| EA_CONSUMER_CONFIDENCE_EA20 | missing | – | – | 69 |  |  |
| EA_RETAIL_VOLUME | missing | – | – | 104 |  |  |
| US_REAL_RETAIL_SALES | missing | – | – | 85 |  |  |
| US_REAL_DPI | missing | – | – | 99 |  |  |
| US_SAVING_RATE | missing | – | – | 99 |  |  |
| US_REVOLVING_CREDIT | missing | – | – | 107 |  |  |
| US_CONSUMER_CREDIT | missing | – | – | 107 |  |  |
| US_DEBT_SERVICE_RATIO | missing | – | – | 291 |  |  |
| US_CARD_DELINQUENCY | missing | – | – | 246 |  |  |
| US_MORTGAGE_DELINQUENCY | missing | – | – | 246 |  |  |
| US_HY_OAS_3M_CHG | missing | – | – | 10 |  |  |
| US_IG_OAS | missing | – | – | 10 |  |  |
| US_BAA_10Y | missing | – | – | 10 |  |  |
| US_NFCI | missing | – | – | 26 |  |  |
| US_ANFCI | missing | – | – | 26 |  |  |
| US_STLFSI | missing | – | – | 26 |  |  |
| US_SLOOS_TIGHTENING | missing | – | – | 231 | critical |  |
| US_WALCL | missing | – | – | 22 |  |  |
| US_TGA | missing | – | – | 22 |  |  |
| US_RRP | missing | – | – | 10 |  |  |
| US_NET_LIQUIDITY | missing | – | – | 22 |  |  |
| US_NET_LIQUIDITY_13W | missing | – | – | 22 |  |  |
| US_M2 | missing | – | – | 97 |  |  |
| EA_BLS_TIGHTENING | missing | – | – | 216 |  |  |
| EA_M3 | missing | – | – | 97 |  |  |
| US_CREDIT_GAP | missing | – | – | 381 |  |  |
| EA_CREDIT_GAP | missing | – | – | 381 |  |  |
| ES_CREDIT_GAP | missing | – | – | 381 |  |  |
| US_DEBT_GDP | missing | – | – | 281 |  |  |
| US_DEFICIT_GDP | missing | – | – | 859 |  |  |
| US_INTEREST_PAYMENTS | missing | – | – | 251 |  |  |
| US_FEDERAL_RECEIPTS | missing | – | – | 251 |  |  |
| US_INTEREST_TO_RECEIPTS | missing | – | – | 251 |  |  |
| EA_DEFICIT_GDP | missing | – | – | 849 |  |  |
| EA_DEBT_GDP | missing | – | – | 849 |  |  |
| ES_DEFICIT_GDP | missing | – | – | 849 |  |  |
| ES_DEBT_GDP | missing | – | – | 849 |  |  |
| SHILLER_PRICE | missing | – | – | 74 |  |  |
| SHILLER_EARNINGS | missing | – | – | 164 |  |  |
| SHILLER_CPI | missing | – | – | 84 |  |  |
| SHILLER_GS10 | missing | – | – | 74 |  |  |
| SP500_CAPE | missing | – | – | 74 | critical |  |
| SP500_EXCESS_CAPE_YIELD | missing | – | – | 74 |  |  |
| SP500_TRAILING_PE | missing | – | – | 164 |  |  |
| SP500_REAL_EPS_GROWTH | missing | – | – | 164 |  |  |
| SP500_NOMINAL_EPS_GROWTH | missing | – | – | 164 |  |  |
| US_CORP_EQUITIES | missing | – | – | 266 |  |  |
| US_GDP | missing | – | – | 221 |  |  |
| US_BUFFETT | missing | – | – | 266 |  |  |
| US_BUFFETT_VS_TREND | missing | – | – | 266 |  |  |
| US_CORP_PROFITS | missing | – | – | 251 |  |  |
| US_PROFITS_GDP | missing | – | – | 251 |  |  |
| COT_SP500_NET_SPEC | missing | – | – | 24 |  |  |
| COT_UST10Y_NET_SPEC | missing | – | – | 24 |  |  |
| COT_USD_NET_SPEC | missing | – | – | 24 |  |  |
| COT_GOLD_NET_SPEC | missing | – | – | 24 |  |  |
| SPX_VS_200D | missing | – | – | 10 |  |  |
| SPX_200D_SLOPE | missing | – | – | 10 |  |  |
| SPX_PCT_B | missing | – | – | 10 |  |  |
| SPX_BANDWIDTH | missing | – | – | 10 |  |  |
| SPX_REALVOL_20D | missing | – | – | 10 |  |  |
| SPX_DRAWDOWN | missing | – | – | 10 |  |  |
| VIX | missing | – | – | 10 |  |  |
| VIX_YAHOO | missing | – | – | 10 |  |  |
| VIX3M | missing | – | – | 10 |  |  |
| VIX_TERM | missing | – | – | 10 |  |  |
| MOVE | missing | – | – | 10 |  |  |
| RSP | missing | – | – | 10 |  |  |
| SPY | missing | – | – | 10 |  |  |
| BREADTH_RSP_SPY_3M | missing | – | – | 10 |  |  |
| BREADTH_ABOVE_200D_PCT | missing | – | – | 10 |  |  |
| BREADTH_NH_NL_PCT | missing | – | – | 10 |  |  |
| STOXX600 | missing | – | – | 10 |  |  |
| STOXX600_VS_200D | missing | – | – | 10 |  |  |
| STOXX600_DRAWDOWN | missing | – | – | 10 |  |  |
| ACWI | missing | – | – | 10 |  |  |
| ACWI_VS_200D | missing | – | – | 10 |  |  |
| NIKKEI | missing | – | – | 10 |  |  |



## Appendix

**Definitions**

| Term | Meaning |
|---|---|
| Regime label | Summarises which transparent rules fire in how many groups; stress needs three groups, late cycle two. It predicts nothing. |
| z-score | (latest minus mean) / standard deviation over the trailing window, ten years unless the catalog says otherwise. |
| Percentile | Mid-rank of the latest value within the whole history of the series. |
| Concern | The z-score signed in the direction of concern; positive is worrying. |
| Sahm rule | The 3-month average unemployment rate rises 0.5 points or more above its low of the previous 12 months. |
| Net liquidity | Fed balance sheet minus the Treasury General Account minus reverse repo. |
| Policy stance | Real Fed funds (Fisher, against core PCE) minus the Holston-Laubach-Williams r*; positive is restrictive. |
| Excess bond premium | The corporate spread left after removing the part explained by expected defaults. |
| CAPE | Real price over the 10-year average of real earnings. Excess CAPE yield: 1 / CAPE minus the real 10-year yield. |
| Buffett indicator | Nonfinancial corporate equities over nominal GDP, read against its own log-linear trend. |
| Earnings yield / ROIC | EBIT / EV and EBIT / (net working capital + net fixed assets), Greenblatt's two measures. |
| Margin of safety | 1 minus price / intrinsic value, against the lower of the earnings power value and a conservative DCF. |
| Drift | Actual bucket weight minus target; a trade is proposed beyond the absolute or relative band, whichever binds first. |
| Money-weighted return | The IRR of the dated cash flows closed with today's value. |
| 13F | Quarterly long US positions of large managers, filed up to 45 days after quarter end. No shorts, cash or foreign listings. |


**Sources**

| Indicator | Source | Key | Freq | Lag (days) |
|---|---|---|---|---|
| US 3-month Treasury yield | fred | DGS3MO | D | 1 |
| US 2-year Treasury yield | fred | DGS2 | D | 1 |
| US 10-year Treasury yield | fred | DGS10 | D | 1 |
| US curve: 10-year minus 3-month | fred | T10Y3M | D | 1 |
| US curve: 10-year minus 2-year | fred | T10Y2Y | D | 1 |
| US real 10-year yield (TIPS) | fred | DFII10 | D | 1 |
| US 10-year breakeven inflation | fred | T10YIE | D | 1 |
| Fed funds minus 2-year yield (cuts priced when positive) | derived | spread | D | 0 |
| ECB deposit facility rate | ecb | FM/B.U2.EUR.4F.KR.DFR.LEV | I | 0 |
| Euro area AAA 10-year spot yield | ecb | YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y | D | 1 |
| Euro area curve: 10-year minus 2-year | derived | spread | D | 0 |
| NY Fed recession probability, 12 months ahead (term spread) | nyfed_recprob | rec_prob | M | 10 |
| Smoothed US recession probability (Chauvet-Piger) | fred | RECPROUSM156N | M | 95 |
| Effective Fed funds rate (monthly) | fred | FEDFUNDS | M | 1 |
| US neutral real rate r* (Holston-Laubach-Williams) | hlw | rstar_us | Q | 75 |
| Euro area neutral real rate r* (Holston-Laubach-Williams) | hlw | rstar_ea | Q | 75 |
| US policy stance: real Fed funds minus r* | derived | policy_stance | M | 0 |
| Euro area real policy rate (deposit rate minus HICP) | derived | real_rate | M | 0 |
| Broad trade-weighted dollar, YoY | fred | DTWEXBGS | D | 4 |
| Dollar index (DXY) | yahoo | DX-Y.NYB | D | 1 |
| EUR/USD | yahoo | EURUSD=X | D | 1 |
| Copper/gold ratio | derived | ratio | D | 0 |
| Gold in EUR per ounce | derived | ratio | D | 0 |
| ISM Manufacturing PMI | release |  | M | 0 |
| ISM Manufacturing: New Orders | release |  | M | 0 |
| ISM Manufacturing: Prices Paid | release |  | M | 0 |
| ISM Manufacturing: Employment | release |  | M | 0 |
| ISM Manufacturing: New Orders minus Inventories | derived | spread | M | 0 |
| ISM Services PMI | release |  | M | 0 |
| ISM Services: Business Activity | release |  | M | 0 |
| S&P Global US Composite PMI | release |  | M | 0 |
| Empire State manufacturing: general business conditions | fred | GACDISA066MSFRBNY | M | 0 |
| Philadelphia Fed manufacturing: general activity | fred | GACDFSA066MSFRBPHI | M | 0 |
| Philadelphia Fed manufacturing: new orders | philly | NOC | M | 0 |
| Dallas Fed manufacturing: general business activity | fred | BACTSAMFRBDAL | M | 0 |
| Kansas City Fed manufacturing composite | release |  | M | 0 |
| Richmond Fed manufacturing composite | release |  | M | 0 |
| Regional Fed surveys, average of those available | derived | mean_available | M | 0 |
| HCOB euro area composite PMI | release |  | M | 0 |
| HCOB euro area manufacturing PMI | release |  | M | 0 |
| HCOB Germany composite PMI | release |  | M | 0 |
| HCOB Spain composite PMI | release |  | M | 0 |
| Ifo business climate (Germany) | release |  | M | 0 |
| ZEW economic expectations (Germany) | release |  | M | 0 |
| Euro area Economic Sentiment Indicator | eurostat | ei_bssi_m_r2?indic=BS-ESI-I&s_adj=SA&geo=EA21 | M | 0 |
| JPMorgan Global Manufacturing PMI | release |  | M | 0 |
| Caixin China Manufacturing PMI | release |  | M | 0 |
| Conference Board LEI, 6-month annualised change | release |  | M | 0 |
| Conference Board LEI, 6-month diffusion | release |  | M | 0 |
| Chicago Fed National Activity Index, 3-month average | fred | CFNAIMA3 | M | 25 |
| OECD composite leading indicator, United States | oecd | OECD.SDD.STES,DSD_STES@DF_CLI,/USA.M.LI...AA...H | M | 40 |
| OECD composite leading indicator, G20 | oecd | OECD.SDD.STES,DSD_STES@DF_CLI,/G20.M.LI...AA...H | M | 40 |
| Atlanta Fed GDPNow, current quarter | release |  | M | 0 |
| NY Fed Staff Nowcast, current quarter | release |  | M | 0 |
| Cleveland Fed CPI nowcast, YoY | release |  | M | 0 |
| Excess bond premium | ebp | ebp | M | 15 |
| EBP-implied recession probability, 12 months | ebp | est_prob | M | 15 |
| US CPI, YoY | fred | CPIAUCSL | M | 13 |
| US CPI, 3-month annualised | derived | ann3m | M | 0 |
| US core CPI, YoY | fred | CPILFESL | M | 13 |
| US core PCE prices, YoY | fred | PCEPILFE | M | 30 |
| Cleveland Fed median CPI, YoY | fred | MEDCPIM159SFRBCLE | M | 13 |
| Dallas Fed trimmed-mean PCE, 12-month | fred | PCETRIM12M159SFRBDAL | M | 30 |
| Atlanta Fed sticky-price core CPI, YoY | fred | CORESTICKM159SFRBATL | M | 13 |
| Michigan 1-year inflation expectations | fred | MICH | M | 30 |
| Cleveland Fed 10-year expected inflation | fred | EXPINF10YR | M | 15 |
| Atlanta Fed wage growth tracker | release |  | M | 0 |
| US real wage growth (hourly earnings deflated by CPI), YoY | derived | deflated_yoy | M | 0 |
| Euro area HICP, YoY | ecb | HICP/M.U2.N.000000.4D0.ANR | M | 17 |
| Euro area HICP ex energy and food, YoY | ecb | HICP/M.U2.N.XEF000.4D0.ANR | M | 17 |
| Spain HICP, YoY | ecb | HICP/M.ES.N.000000.4D0.ANR | M | 17 |
| WTI crude, YoY | fred | DCOILWTICO | D | 8 |
| Brent crude | fred | DCOILBRENTEU | D | 8 |
| US regular gasoline, YoY | fred | GASREGW | W | 1 |
| Initial claims, 4-week average | derived | ma4w | W | 0 |
| Initial claims 4-week average, YoY | derived | yoy | W | 0 |
| Initial claims 4-week average, % above 52-week low | derived | claims_off_low | W | 0 |
| Continuing claims, YoY | fred | CCSA | W | 12 |
| US unemployment rate | fred | UNRATE | M | 7 |
| Sahm rule (real-time) | fred | SAHMREALTIME | M | 7 |
| Payroll gains, 3-month average | derived | payrolls_3m_change | M | 0 |
| Temporary help employment, YoY | fred | TEMPHELPS | M | 7 |
| Manufacturing weekly hours | fred | AWHMAN | M | 7 |
| Openings per unemployed (V/U) | derived | ratio | M | 0 |
| Quits rate (JOLTS) | fred | JTSQUR | M | 35 |
| Euro area unemployment rate | eurostat | une_rt_m?geo=EA21&s_adj=SA&age=TOTAL&unit=PC_ACT&sex=T | M | 32 |
| Spain unemployment rate | eurostat | une_rt_m?geo=ES&s_adj=SA&age=TOTAL&unit=PC_ACT&sex=T | M | 32 |
| Building permits, YoY | fred | PERMIT | M | 19 |
| Housing starts, YoY | fred | HOUST | M | 19 |
| Existing home sales, YoY | fred | EXHOSLUSM495S | M | 25 |
| 30-year mortgage rate | fred | MORTGAGE30US | W | 1 |
| NAHB housing market index | release |  | M | 0 |
| Core capital goods orders, CPI-deflated, YoY | derived | deflated_yoy | M | 0 |
| Capacity utilisation | fred | TCU | M | 17 |
| Industrial production, YoY | fred | INDPRO | M | 17 |
| Cass Freight Index, shipments, YoY | release |  | M | 0 |
| South Korea exports, YoY | fred | XTEXVA01KRM667S | M | 45 |
| Euro area industrial production, YoY | eurostat | sts_inpr_m?indic_bt=PRD&nace_r2=B-D&s_adj=SCA&unit=I21&geo=EA21 | M | 45 |
| Euro area real GDP, YoY | eurostat | namq_10_gdp?geo=EA21&s_adj=SCA&unit=CLV_PCH_SM&na_item=B1GQ | Q | 45 |
| US real GDP | fred | GDPC1 | Q | 30 |
| Michigan consumer sentiment | fred | UMCSENT | M | 30 |
| Michigan consumer expectations | release |  | M | 0 |
| Euro area consumer confidence | eurostat | ei_bsco_m?indic=BS-CSMCI&s_adj=SA&unit=BAL&geo=EA21 | M | 0 |
| Euro area retail sales volume, YoY | eurostat | sts_trtu_m?indic_bt=VOL_SLS&nace_r2=G47&s_adj=SCA&unit=I21&geo=EA21 | M | 35 |
| Real retail sales, YoY | fred | RRSFS | M | 16 |
| Real disposable income, YoY | fred | DSPIC96 | M | 30 |
| Personal saving rate | fred | PSAVERT | M | 30 |
| Revolving consumer credit, YoY | fred | REVOLSL | M | 38 |
| Household debt service ratio | fred | TDSP | Q | 100 |
| Credit card delinquency rate | fred | DRCCLACBS | Q | 55 |
| Single-family mortgage delinquency rate | fred | DRSFRMACBS | Q | 55 |
| Auto loans 90+ days delinquent (NY Fed HHDC) | release |  | Q | 0 |
| US high-yield OAS | fred | BAMLH0A0HYM2 | D | 1 |
| US high-yield OAS, 3-month change | derived | diff3m | D | 0 |
| US investment-grade OAS | fred | BAMLC0A0CM | D | 1 |
| Baa corporate minus 10-year Treasury | fred | BAA10Y | D | 1 |
| Chicago Fed financial conditions (NFCI) | fred | NFCI | W | 5 |
| St. Louis Fed financial stress index | fred | STLFSI4 | W | 5 |
| Loan officer survey: net share tightening on C&I loans, large and mid firms | fred | DRTSCILM | Q | 40 |
| Net liquidity (Fed assets minus TGA minus RRP) | derived | net_liquidity | W | 0 |
| Net liquidity, 13-week change | derived | change_13w | W | 0 |
| M2 money stock, YoY | fred | M2SL | M | 28 |
| FINRA margin debt, YoY | release |  | M | 0 |
| ECB bank lending survey: net tightening on loans to firms | ecb | BLS/Q.U2.ALL.O.E.Z.B3.ST.S.WFNET | Q | 25 |
| Euro area M3, annual growth | ecb | BSI/M.U2.Y.V.M30.X.I.U2.2300.Z01.A | M | 28 |
| US credit-to-GDP gap (BIS) | bis | WS_CREDIT_GAP/Q.US.P.A.C | Q | 190 |
| Euro area credit-to-GDP gap (BIS) | bis | WS_CREDIT_GAP/Q.XM.P.A.C | Q | 190 |
| Spain credit-to-GDP gap (BIS) | bis | WS_CREDIT_GAP/Q.ES.P.A.C | Q | 190 |
| US federal debt / GDP | fred | GFDEGDQ188S | Q | 90 |
| US federal surplus or deficit / GDP (fiscal year) | fred | FYFSGDA188S | A | 120 |
| Federal interest payments / receipts | derived | interest_to_receipts | Q | 0 |
| Euro area government balance / GDP | eurostat | gov_10dd_edpt1?na_item=B9&sector=S13&unit=PC_GDP&geo=EA21 | A | 110 |
| Euro area government debt / GDP | eurostat | gov_10dd_edpt1?na_item=GD&sector=S13&unit=PC_GDP&geo=EA21 | A | 110 |
| Spain government balance / GDP | eurostat | gov_10dd_edpt1?na_item=B9&sector=S13&unit=PC_GDP&geo=ES | A | 110 |
| Spain government debt / GDP | eurostat | gov_10dd_edpt1?na_item=GD&sector=S13&unit=PC_GDP&geo=ES | A | 110 |
| S&P 500 CAPE (Shiller) | shiller | cape | M | 5 |
| Excess CAPE yield (Shiller) | shiller | excess_cape_yield | M | 5 |
| S&P 500 trailing P/E (Shiller) | derived | ratio | M | 0 |
| S&P 500 real trailing earnings growth, YoY | derived | real_earnings_growth | M | 0 |
| S&P 500 nominal trailing earnings growth, YoY | derived | yoy | M | 0 |
| Buffett indicator: corporate equities / GDP | derived | buffett | Q | 0 |
| Buffett indicator, % above its log-linear trend | derived | trend_deviation | Q | 0 |
| Corporate profits / GDP | derived | ratio_pct | Q | 0 |
| COT: large speculators net S&P 500 futures (contracts) | cot | S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE | W | 3 |
| COT: large speculators net 10-year Treasury futures | cot | UST 10Y NOTE - CHICAGO BOARD OF TRADE | W | 3 |
| COT: large speculators net dollar index futures | cot | USD INDEX - ICE FUTURES U.S. | W | 3 |
| COT: large speculators net gold futures | cot | GOLD - COMMODITY EXCHANGE INC. | W | 3 |
| AAII bull minus bear spread | release |  | W | 0 |
| NAAIM exposure index | release |  | W | 0 |
| CBOE equity put/call ratio | release |  | W | 0 |
| ICI weekly equity fund flows | release |  | W | 0 |
| S&P 500, % vs 200-day average | derived | pct_vs_ma200 | D | 0 |
| S&P 500 200-day average, 20-day slope | derived | ma200_slope | D | 0 |
| S&P 500 Bollinger %B (20-day, 2 sigma) | derived | bollinger_pct_b | D | 0 |
| S&P 500 Bollinger bandwidth | derived | bollinger_bandwidth | D | 0 |
| S&P 500 20-day realised volatility | derived | realised_vol_20d | D | 0 |
| S&P 500 drawdown from high | derived | drawdown | D | 0 |
| VIX | fred | VIXCLS | D | 1 |
| VIX / VIX3M (above 1 is an inverted term structure) | derived | ratio | D | 0 |
| MOVE index (Treasury volatility) | yahoo | ^MOVE | D | 1 |
| Breadth proxy: equal-weight vs cap-weight S&P 500, 3-month relative return | derived | relative_strength_3m | D | 0 |
| Breadth: share of the screener universe above its 200-day average | computed |  | D | 1 |
| Breadth: new 52-week highs minus new lows, % of the screener universe | computed |  | D | 1 |
| STOXX 600, % vs 200-day average | derived | pct_vs_ma200 | D | 0 |
| STOXX 600 drawdown from high | derived | drawdown | D | 0 |
| World (ACWI), % vs 200-day average | derived | pct_vs_ma200 | D | 0 |


