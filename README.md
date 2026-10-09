# Auction House

A World of Warcraft Classic styled website for your Auctionator scan data, hosted on GitHub Pages.

Scan the auction house in game with Auctionator, then run **`upload.bat`**. It reads the scan data
WoW saved, merges it into a permanent price archive, builds the site data and pushes it to GitHub.
Pages updates a minute or two later.

| Script | What it does |
| --- | --- |
| `upload.bat` | Export and upload once |
| `watch.bat` | Stay running and upload every time WoW saves new scan data |
| `preview.bat` | Export without uploading and open the site locally |

WoW only writes saved variables on `/reload`, logout or exit, so do one of those after a scan.

## What the site shows

- **Browse**: the Classic auction house browser. Category tree, name, level, rarity and price filters, sortable columns.
- **Item pages**: current price, 3/7/14/30 day and all-time averages, lowest and highest ever, volatility,
  price history chart (daily range, price, 30 day average, quantity listed), best weekday to buy and sell,
  daily history, disenchant breakdown (era table and your own results from DisenchantValue), vendor prices,
  and your own postings.
- **Market**: market value, listings, distinct items and a price index over time. Also a category breakdown,
  the biggest risers and fallers, the most listed, valuable, expensive and volatile items, and items new to or gone from the market.
- **Deals**: items listed below their 30 day average.
- **Disenchant**: items worth more disenchanted than their buyout, enchanting material prices, and value per item level.
- **Vendor Flips**: items listed below vendor price, and vendor items listed above vendor price.
- **Vendor Recipes**: recipes a vendor sells that are also on the auction house, vendor price vs auction price,
  with the vendors' names, zones, faction and limited stock. Filter by faction and profession.
- **My Auctions**: your Auctionator posting history compared with current prices.

Hover any item for its in-game tooltip with auction, average, disenchant and vendor prices. Press `/` to search.

## How it works

- `tools/export.py` reads `WTF/Account/*/SavedVariables/Auctionator.lua` for every account. Auctionator
  stores each realm as CBOR with daily low/high prices and quantities, and keeps only 21 days of it.
- `archive/<realm>.json` keeps every day ever seen, so history grows past Auctionator's limit. Commit it.
- Item names, tooltips, classes and icons come from Wowhead's `forever` database and are fetched once.
  They are stored in `data/items.json` and `docs/icons/`. Which vendors sell each recipe comes from the
  recipe's Wowhead page. That is stored in `data/vendors.json` and rechecked every 30 days.
- `docs/` is the static site (plain HTML, CSS and JS, no build step). Data is split into small files so pages load fast.

## Settings (`config.json`)

- `wow_path`: your WoW install folder (the one containing `WTF`).
- `realm_aliases`: merge realms, for example `{"ClassicBetaPvP": "PvP"}` if a realm was renamed.
- `hide_realms`: realm names to leave off the site.
- `site_title`: header title.
- `push`: set `false` to only export.

Requires Python 3.10+ and git. There are no other dependencies.
