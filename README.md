# Stock News RSS — GitHub Pages edition

This version publishes a static dashboard and RSS feed with GitHub Pages. Feedly polls the static XML file, so Feedly does not consume Alpha Vantage API calls.

## API budget

The workflow runs three times per day. It makes one Alpha Vantage request per ticker and supports at most five tickers, so scheduled updates use at most 15 of the free plan's 25 daily requests. Avoid repeatedly pressing **Run workflow**, because manual runs also consume requests.

## Setup

1. Add the repository secret `ALPHA_VANTAGE_API_KEY`.
2. Add the repository variable `STOCK_TICKERS`, such as `AAPL,MSFT,NVDA`.
3. In **Settings → Pages**, select **Deploy from a branch**, branch **main**, folder **/docs**.
4. In **Actions**, run **Update stock news feed** once.
5. Your website becomes `https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/`.
6. Your Feedly URL becomes `https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/feed.xml`.

Sentiment labels come from Alpha Vantage and describe article language and context. They are not investment advice.
