"""What the Screener's screens, filters and columns mean, for the help bubbles on that page.

The app is meant to teach as well as screen, so each text says what the number is, how
this app computes it, how to read it, and where it misleads. The formulas follow
``invest/fundamentals/metrics.py`` and ``invest/fundamentals/screens.py``: change one
there, and the text here with it. Streamlit renders the texts as Markdown.
"""

from __future__ import annotations

#: The help on the Screen dropdown: every screen it offers.
SCREEN_HELP = """\
A **screen** sorts the companies of the universe by a rule, so that the ones worth a closer \
look come first. None is a buy list: each is a starting point for reading the accounts.

**Magic formula (earnings yield + ROIC).** Joel Greenblatt's rule, from *The Little Book \
That Beats the Market* (2005): good businesses at bargain prices. Every company is ranked \
twice, once on how cheap it is (EBIT/EV, highest first) and once on how good it is (ROIC, \
highest first). The two ranks are added, and the lowest sum comes first: the best mix of \
both, not the best at either. Greenblatt meant it for a portfolio of 20 to 30 of the top \
names held for a year, and warned that it can lag the market for years at a stretch.

**Quality-value composite.** A blend of six measures, three of value and three of quality: \
EBIT/EV, FCF yield, ROIC, the Piotroski F-score, accruals (lower is better) and the trend \
of the gross margin. Each is turned into a z-score (how far above or below the average of \
the table a company is, in standard deviations) and the six are averaged. A company needs \
at least four of the six to be ranked. It follows research showing that cheapness works \
better when combined with quality, such as Novy-Marx on gross profitability and Asness on \
"quality minus junk".

**Deep value (P/B < 1, F-score >= 7).** Joseph Piotroski's setting (2000): among the \
cheapest stocks by price to book, those whose finances were improving (a high F-score) \
did much better than those whose finances were deteriorating. Here: price below the \
accounting value of equity, and at least 7 of the 9 checks passed, sorted by F-score and \
then by P/B. In a universe of large companies the list is often short or empty; \
Piotroski's study was mostly of small ones.

**Shareholder yield.** Meb Faber's measure (2013): the cash a company hands back to its \
owners, as dividends and share buybacks, as a share of its market value, highest first. \
It counts buybacks, which have replaced much of the dividend in the US. Only companies \
whose free cash flow is not negative are kept, so the payout is funded by the business \
rather than by borrowing.
"""

MIN_CAP_HELP = """\
Leaves out companies whose market value is below this, in billions of the currency of \
their accounts. Smaller companies are often cheaper and less followed, but their data \
is patchier and their shares harder to trade. 0 keeps every company.
"""

EXCLUDE_FINANCIALS_HELP = """\
Leaves out banks, insurers and other financial companies, as Greenblatt does. Their \
accounts do not fit these measures: for a bank, debt and deposits are the raw material \
of the business rather than its financing, so EV, ROIC and debt ratios mean something \
else. Applies to the magic formula and the quality-value composite.
"""

VALUE_TRAP_HELP = """\
A **value trap** is a stock that looks cheap because the business is shrinking or in \
trouble, and keeps getting cheaper. Before ranking, this leaves out companies with any of \
these signs, and lists them with the reason under the table:

- revenue fell in each of the last three years;
- free cash flow was negative in 3 or more of the last 5 years;
- net debt above 4 times EBITDA;
- a Piotroski F-score of 3 or less;
- the share count rose more than 10% in three years (dilution).
"""

SECTOR_NEUTRAL_HELP = """\
Computes each z-score against the companies of the same sector rather than the whole \
table, so a company is compared with its peers. Without it, whole sectors dominate the \
top: software always has a high ROIC, utilities a low one. A sector with fewer than 5 \
companies keeps the whole-table z-score.
"""

#: The help on the company detail's figures.
PRICE_HELP = """\
The latest close stored by the screener build, in the currency the shares are quoted in \
(for a foreign company listed in the US, dollars). As of {date}.
"""

MARKET_CAP_HELP = """\
What the market values the whole company at: the latest close times the shares \
outstanding, in billions of the currency of the accounts (the close converted into it \
first). The EV, P/E, P/B and yields in the list all start from it.
"""

EPV_HELP = """\
**Earnings power value**: what the business is worth if it simply keeps earning what it \
earns today, with no growth at all. Bruce Greenwald's method (*Value Investing: From \
Graham to Buffett and Beyond*).

1. Average the operating profit (EBIT) of the last five fiscal years, to smooth out one \
good or bad year.
2. Take tax off, at the company's own rate when it is between 10% and 35%, else at 21%.
3. Divide by the cost of capital, 9% here: a profit of 9 a year forever is worth 100 \
today to an owner who wants a 9% return.
4. Subtract net debt (debt less cash), which comes before the shareholders, and divide \
by the number of shares.

Example: average EBIT 1,000m, after 21% tax 790m, ÷ 9% = 8,778m, less 1,000m of net debt \
= 7,778m, over 100m shares = 77.8 a share.

Because it assumes no growth, it is the value of the business as it stands. A price \
below it means the market expects the business to shrink. In the currency of the \
accounts.
"""

DCF_HELP = """\
**Discounted cash flow**: what the cash the business will produce in the future is \
worth today, under deliberately modest assumptions.

1. Start from the latest free cash flow: operating cash flow less capital spending.
2. Grow it for five years at the company's revenue growth over the last three years, \
but never below 0% or above 5% a year.
3. After that, assume 2% a year forever, about the long-run growth of the economy (the \
"terminal value").
4. Bring every year back to today's money at 9% a year: 100 in five years is worth 65 \
today. Subtract net debt, divide by the number of shares.

The terminal value is usually most of the result, so the answer leans heavily on the 9% \
and the 2%: with 8% instead of 9%, it rises by about a sixth. No value when free cash \
flow is zero or negative. In the currency of the accounts.
"""

MARGIN_OF_SAFETY_HELP = """\
1 minus the price divided by the **lower** of the EPV and the DCF per share, with the \
price converted into the currency of the accounts first.

+30% means the price is 30% below the more cautious of the two estimates; a negative \
value, that it is above it. Benjamin Graham's idea: buy only with room for error, \
because any estimate of value will be wrong. Taking the lower estimate counts only the \
value that both methods support.
"""

#: The help on each column of the ranked list from EBIT/EV onwards, by column label.
COLUMN_HELP = {
    "EBIT/EV": """\
**Earnings yield**, the "cheap" half of Greenblatt's magic formula.

**EBIT** is operating profit: earnings before interest and taxes. **EV** (enterprise \
value) is what buying the whole business would cost: the market value of the shares, \
plus its debt, minus its cash and short-term investments.

So 10% means the business earns, before interest and tax, a tenth of what it would cost \
to buy outright. Higher is cheaper. Unlike P/E, it is not flattered by debt or by a low \
tax rate, so companies with different borrowing compare fairly.

Watch for: one year of profit. A cyclical company at the top of its cycle looks cheapest \
just before its profits fall.
""",
    "ROIC": """\
**Return on invested capital**, the "good business" half of Greenblatt's magic formula.

Operating profit (EBIT) divided by the capital the operations need: net working capital \
(current assets other than cash, less current liabilities other than short-term debt, \
floored at zero) plus property, plant and equipment.

40% means each 100 of equipment and working capital produces 40 of operating profit a \
year. A business that keeps earning a high return on its capital usually has something \
competitors cannot copy: a brand, a network, a cost advantage.

Watch for: goodwill and intangibles are left out, as Greenblatt does, so a company that \
grew by acquisitions looks better here than on what it paid. Asset-light businesses \
(software, services) can show very high values.
""",
    "FCF yield": """\
**Free cash flow yield**: free cash flow divided by the market value of the shares.

Free cash flow is the cash the operations bring in (operating cash flow) less what is \
spent on equipment and buildings (capital expenditure). It is the cash left over for the \
owners: for dividends, buybacks, paying debt or acquisitions.

6% means a year's free cash flow is 6% of the share price. Higher is cheaper. Cash is \
harder to dress up than accounting profit, which makes this a useful check on P/E.

Watch for: capital spending comes in lumps, so one year can be high or low by chance. \
Debt is not counted, so a heavily indebted company can look cheap here; check ND/EBITDA.
""",
    "F": """\
**Piotroski F-score**, from 0 to 9: how many of nine checks of financial health pass, \
comparing the latest fiscal year with the one before.

- *Profitability*: return on assets is positive; operating cash flow is positive; return \
on assets improved; operating cash flow exceeds net income.
- *Debt and liquidity*: long-term debt fell as a share of assets; the current ratio \
(current assets over current liabilities) rose; no new shares were issued.
- *Efficiency*: the gross margin rose; asset turnover (revenue over assets) rose.

8 or 9 is strong, 0 to 3 weak. Joseph Piotroski (2000) used it to tell the cheap stocks \
whose business is recovering from those that are cheap for good reason.

A check without the data counts as failed; the score is blank when fewer than six could \
be made. The company detail lists each check.
""",
    "Accruals": """\
**Accruals ratio**: net income minus operating cash flow, divided by average total assets.

Profit is an accounting figure: sales made on credit, inventory built up and estimates \
all count before any cash arrives. When profit runs well ahead of the cash coming in, \
the ratio is positive, and the profit is of lower quality.

Lower is better; a negative value means cash came in faster than profit was reported. \
Richard Sloan (1996) found that companies with high accruals did worse afterwards, as \
their profits fell back towards their cash.

Example: net income 120, operating cash flow 100, assets 1,000 gives +0.02.
""",
    "GM slope/yr": """\
**Gross margin trend**: by how much the gross margin changed per year, on average, over \
the fiscal years on file (the slope of a straight line through them; at least three \
years).

Gross margin is revenue less the direct cost of what was sold, as a share of revenue. \
+0.50% means it rose by about half a percentage point a year, for example from 40.0% to \
42.0% over four years.

A rising margin suggests pricing power or a better mix of products; a falling one, \
competition or rising costs that cannot be passed on. Robert Novy-Marx (2013) showed \
gross profitability to be among the best measures of a company's quality.
""",
    "P/B": """\
**Price to book**: the market value of the shares divided by the equity on the balance \
sheet (assets less liabilities, the "book value").

Below 1, the market values the company at less than its accounts say its net assets are \
worth: the classic deep-value signal of Benjamin Graham, and the value factor of Fama \
and French.

Watch for: book value leaves out what the accounts do not record as assets, such as \
brands, software and research spent as costs. Buybacks also shrink equity. Many fine \
businesses therefore show a high P/B without being expensive. Blank when equity is \
negative.
""",
    "P/E": """\
**Price to earnings**: the market value of the shares divided by net income, the profit \
left for shareholders after interest and tax.

20 means the price is 20 years of current profit; the inverse, 5%, is the earnings yield \
on the shares. Lower is cheaper. The most quoted multiple, and the one in the news.

Watch for: net income moves with one-off gains and losses, the tax rate and the amount \
of debt, so two companies with the same business can show different P/Es. EBIT/EV avoids \
most of that. Blank when the company made a loss.
""",
    "EV/EBITDA": """\
**Enterprise value to EBITDA**. EV is what buying the whole business would cost (shares \
plus debt less cash); EBITDA is operating profit before depreciation and amortisation, \
the charges for wear of equipment and for intangibles.

10 means the whole business costs ten years of EBITDA. Lower is cheaper. It is the \
multiple acquirers and private equity use most, because it compares companies regardless \
of debt and of how they depreciate.

Watch for: equipment does wear out. Adding depreciation back flatters capital-heavy \
businesses (telecoms, airlines), which is why Buffett distrusts it. EBIT/EV keeps the \
charge.
""",
    "ND/EBITDA": """\
**Net debt to EBITDA**: debt less cash and short-term investments, divided by EBITDA.

Roughly, how many years of operating cash profit it would take to repay the debt. Below \
1 is light; above 3 is heavy for most businesses; above 4, the value-trap filter leaves \
the company out. A negative value means the company holds more cash than debt (net cash).

Watch for: stable businesses such as utilities can carry more debt safely than cyclical \
ones, whose EBITDA can halve in a recession and double this ratio.
""",
    "Sh. yield": """\
**Shareholder yield**: dividends plus share buybacks in the latest year, divided by the \
market value of the shares.

It counts all the cash returned to the owners, not just the dividend. Buying back shares \
returns cash too: the remaining owners each hold a larger part of the company. Since the \
1980s US companies have returned more through buybacks than through dividends.

Watch for: buybacks are counted gross. Shares issued to employees as pay are not \
subtracted, so a company can buy back shares and still have more of them; check the \
share count in the company detail.
""",
    "Margin of safety": """\
**Margin of safety**: 1 minus the price divided by a conservative estimate of what a \
share is worth.

The estimate is the lower of two: the earnings power value (average operating profit \
after tax, valued as if it never grew, at a 9% cost of capital, less net debt) and a \
discounted cash flow (free cash flow growing at most 5% for five years, then 2% a year, \
discounted at 9%, less net debt).

+30% means the price is 30% below the estimate; a negative value, that it is above it. \
Benjamin Graham's idea: buy only with room for error, because the estimate will be wrong. \
The company detail shows every input of both models.
""",
    "Rank sum": """\
**Magic formula score**: the company's rank on EBIT/EV plus its rank on ROIC, where 1 is \
the highest in the table.

A company 3rd on cheapness and 10th on quality scores 13, and comes ahead of one that is \
1st on cheapness but 40th on quality (41). Lower is better. Ties go to the higher \
earnings yield.
""",
    "Composite z": """\
**Quality-value score**: the average of six z-scores, for EBIT/EV, FCF yield, ROIC, \
F-score, accruals (reversed, so lower accruals score higher) and the gross margin trend.

A z-score says how far a company is from the average of the table, in standard \
deviations: +1 is clearly better than average, 0 average, -1 clearly worse. Each is \
capped at ±3, so one extreme number cannot carry a company to the top.

+0.8 means the company is, on balance, well above average across value and quality. \
With "Sector-neutral" ticked, each z-score compares the company with its own sector.
""",
    "Data to": """\
**Data to**: the end of the latest period whose filings the figures use.

The price is today's; the accounts are as old as this date. Companies file their annual \
report up to two to three months after the year ends, and quarterly ones about six \
weeks after the quarter. A date much older than that means a filing is missing, and \
the measures compare a fresh price with stale accounts.
""",
    "Basis": """\
**Basis** of the profit and cash-flow figures:

- **ttm**, trailing twelve months: the last fiscal year, plus this year's quarters so \
far, less the same quarters a year earlier. The most recent full year of business.
- **annual**: the last fiscal year alone, when the quarters needed for a TTM figure are \
not in the filings.

Balance-sheet figures (debt, cash, equity) are always the latest reported. Two companies \
on different bases compare periods up to a year apart.
""",
}
