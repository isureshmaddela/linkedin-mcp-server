# Stocks

Follow the markets, learn to invest without risk, keep track of the shares you own, and earn from the stock ideas you
publish. Open it from the app switcher (Everyday > Stocks), or go to `/stocks`. The app's id is `stocks`
(`/api/stocks/*`).

**Nothing in Stocks is financial advice.** Forecasts are estimates from past prices and are often wrong; every
forecast shows its track record so you can see how often. **Aira never buys or sells real shares**: the practice
portfolio uses virtual money, and your real holdings are a record you keep of what you did with your own broker.

## In the Aira frame

Stocks lives at `/stocks`, with its own logo and tab icon, inside the frame every Aira app shares.

* **+ New** offers **Buy with practice money**, **Price alert** and **My prediction**, and for adults **Record a real
  trade** and **Publish a stock idea**. Each opens the same form as the button on its tab (with no practice portfolio
  yet, the form that starts one comes first). On a stock's page they are about that stock: the alert, the prediction
  and the trade start with its symbol.
* **The search across the top** suggests stocks and indices as you type, by symbol or name, and says which ones are
  on one of your watchlists; choosing one opens its page. **Enter** lists every match from both markets with today's
  price and change (`#/stocks?q=bank`), or says nothing matched with a way back to every stock.
* **The left menu's folders** are the tabs: Markets, Watchlist, Portfolio, Predictions, Ideas (earn) and Alerts, each
  with its icon. The page is named after the open folder, or after the stock on a stock's page (its symbol, exchange
  and currency underneath). On a wide screen the tab bar under the title is hidden, since the folders say the same;
  phones and tablets keep it.

## Where the prices come from

* Prices come from Yahoo Finance's public charts (no account or key needed). They may be delayed by a few minutes.
* A price is fetched at most every five minutes and a stock's two-year history once a day; they are saved, so when
  the internet is down Stocks shows the saved prices (and says so).
* Two markets are built in: **United States** (about 30 large companies: Apple, Microsoft, NVIDIA, Amazon, Alphabet,
  Meta, Tesla, JPMorgan, Visa, ...) and **India (NSE)** (about 30 NIFTY 50 companies: Reliance, TCS, HDFC Bank,
  Infosys, ...), plus the S&P 500, Nasdaq, Dow Jones, NIFTY 50, Sensex and NIFTY Bank indices. Any other symbol Yahoo
  knows can be searched by its symbol (Indian shares end in `.NS`, for example `TATAPOWER.NS`).
* For tests and offline demos an administrator can set `AIRA_MARKET_PROVIDER=stub`: made-up prices that are the same
  on every computer (the screens then say "Practice data").

## Markets

* Index cards with today's change, how many stocks rose and fell, and when the prices are from.
* **Top gainers** and **top losers** today, the **most traded** (by volume), and stocks **near their 52-week high**
  or **low** (within 2%).
* **Every stock** in the market, sortable by name, biggest rise, biggest fall or volume.
* **Find a stock** by name or symbol (Apple, AAPL, Reliance, TCS.NS) with the search box across the top.
* If you own something, **Your money today** shows each portfolio's value and today's change.

Rises are shown with ▲ and a + sign, falls with ▼ and a - sign (colour only helps).

## A stock's page

* The price, today's change, open, day high and low, previous close, volume and the 52-week range.
* A **chart** for 1 month, 3, 6 months, 1 or 2 years, with the 20-, 50- and 200-day averages. Move over it, or focus it
  and use the arrow keys, to read any day.
* **The period in numbers**: the change over the period, the highest and lowest price and their dates, days up and
  down, and the **best days** (biggest gains) and **worst days** (biggest falls).
* The **daily log**: every trading day's open, high, low, close, change and volume, 30 days at a time;
  **Export CSV** downloads it.
* Buttons to add it to a watchlist, set an alert, buy or sell with practice money, record a real trade (adults) and
  see the prediction. Your position and your alerts on this stock are shown too.

## Predictions

* **Indicators**: the 20-, 50- and 200-day averages, the 20-day exponential average, RSI (14 days), MACD with its
  signal line, Bollinger bands and yearly volatility, each with a line on what it means.
* **Forecast** for 5 and 20 trading days from two simple models, a **trend line** through the recent prices and
  **smoothing** (Holt's method), and their **ensemble** (average). The 80% band shows where the price would end 8
  times in 10 if the past were a guide.
* **Signal**: bullish, neutral or bearish, with every reason listed (the averages, RSI, MACD, the bands, the forecast).
* **Track record**: Aira replays the last year. On about 50 past days it forecast with only what was known then and
  compared with what happened: how often each model got the **direction** right, and its **average miss** in %. A
  "no change" baseline is shown next to them; a model is only useful if it misses by less.
* **Explain in plain words** asks the writing model for a short paragraph (a template is used when no model runs).
* **Make your own prediction**: say a stock goes up or down by a date (and a target price if you like). Aira checks it
  after that date and shows how often you were right, next to how often the models were right on the same
  predictions. **Export CSV** downloads them.

## Portfolio

### Practice (virtual money)

* Start with virtual cash: 100,000 US dollars for US stocks, 10,00,000 rupees for Indian stocks (or another amount
  between 1,000 and 10 crore units). You can have one practice portfolio in each currency.
* **Buy** and **Sell** fill at the latest price, without fees. Part of a share is fine (down to 0.000001), or enter
  an **amount of money** instead and Aira buys as many shares as it pays for. A trade worth less than one cent is
  refused.
* You see what it is worth (cash and shares), the change since you started, today's change, profit not yet sold
  (unrealised) and already sold (realised), each holding with its average cost, value, profit or loss and share of the
  portfolio, the holdings that **earned the most** and **the least**, and the **transaction log**.
* **Reset** starts again with fresh cash.
* **Practice league**: everyone's practice return in the household (or your organization, hosted), best first. Only
  names and returns are shown, never amounts.

Children and teenagers can use practice mode; everything about real money is for adults.

### My real holdings (adults)

* **Record a real trade**: a buy or a sell (date, number of shares, price, fees and taxes) or a dividend received.
  The date is not filled in for you: enter the day of the trade from your broker's statement. Change or delete a
  trade later; Aira refuses a change that would leave a sell without the shares to sell.
* **Today's change**: shares bought today count from the price you paid; older shares count from yesterday's close.
  A buy dated today at a price far outside today's trading (an old holding recorded with today's date) counts like
  an older share, so its whole gain is not shown as today's.
* Prices are typed as text ("182.50"); a typo such as "abc" is shown as an error, never saved as 0.
* Profit and loss use the **average cost** method: fees are part of what you paid; selling part of a holding realises
  the difference between what you got (less fees) and the average cost of the shares sold.
* Holdings in each currency: value, invested, unrealised and realised profit or loss, dividends, fees, today's change,
  share of the portfolio, the best and worst earners and the transaction log. **Export CSV** downloads it.

## Watchlist

Follow stocks without buying them: the price, today's change and the day's range. Keep several lists (Banks, Long
term, ...). **Alert** sets a price alert; open a stock to see its log.

## Alerts

* **The price rises above** or **falls below** a number (fires once, then switches off; **Turn on again** re-arms it).
* **It moves a lot in one day** (a change of X% or more; fires at most once a day and stays on).
* Aira checks alerts every few minutes. A fired alert appears on **Today** and as a message on screen, with
  **Got it** to dismiss it. **Check now** checks at once.

## Ideas: earn from your stock ideas (adults)

1. **Publish ideas**: a stock, buy or sell, the entry (today's price, or within 10% of it), a target, a stop-loss, a
   time frame and your reasoning; free for everyone or for paying subscribers only.
2. Aira **tracks every idea** each day: **target hit**, **stopped out** (on a day that touched both the stop and the
   target, the stop counts), **expired** at the end of its time frame, or **closed** by you at today's price. Ideas
   stay on your record after the first hour, so it stays honest: in that first hour **Delete** removes one. **Edit
   why** changes the reasoning at any time; the prices stay as published.
3. Your **track record**: ideas published, win rate, average return, best and worst.
4. **Your analyst page** (a public link, **Create link**): your name, bio, track record, open free ideas, how many paid
   ideas are open, and every closed idea (paid ones too). Nobody sees your subscribers or payments.
5. **Paid subscriptions**: set a monthly price and your own payment link (Stripe, PayPal, Razorpay, UPI) or payment
   instructions. A follower asks to subscribe on your page and gets a payment reference. When their money arrives,
   press **Payment received** (months and amount): Aira extends their subscription, makes an **access code** (shown
   to you once, and emailed to them through the Business hub's Outbox) and counts the money as income. With their
   email and code they open your paid ideas on the page until the paid period ends. **Undo** a payment marked by
   mistake (the period shortens and it stops counting), **New code** replaces a code, **Cancel** ends access.
   Confirming the same payment twice within two minutes (two tabs, a retry) records it once.
6. On the public page, asking to subscribe gives the same answer for every email address, so nobody can find out who
   already pays you, and a second request with the same email never changes the name or note you see.

Aira does not move money: it records the payments you confirm. On your own computer the income also joins the
Business hub's revenue; in an organization it is yours, not the organization's.

## Today, the AI app and background checks

* **Today** shows each portfolio's value and today's change, fired alerts, and people waiting for you to confirm
  their payment.
* The **StocksAgent** in the AI app answers questions about your portfolios, watchlist movers and alerts, and can add
  a stock to your watchlist or create an alert (small actions) or buy and sell in your practice portfolio (bigger
  actions that wait for your approval unless you allow it). It never touches real money.
* Every few minutes Aira checks alerts, follows ideas, checks your predictions whose date has passed and ends
  subscriptions whose paid period is over.

## Privacy

Everything is yours alone: other people in the household (or members of your organization) never see your
watchlists, alerts, holdings, predictions, ideas or subscribers. The practice league shows names and returns only.

## Hosted build: public links

The hosted server builds its organization index for public links, then imports the single-user build, whose
start-up used to put the single-user index back, so every new public link answered 404. Until `aira/suite/public.py`
is fixed (`set_default_index` should not replace an index installed with `set_index`),
`aira/suite/stocks/links.py` keeps the hosted index in place. `tests/test_suite_stocks_hosted_links.py` starts the
server's own import order in a fresh interpreter and opens an analyst page as a visitor for every role.
