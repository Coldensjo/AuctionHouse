# Auction House

A World of Warcraft Classic styled website for your Auctionator scan data, hosted on GitHub Pages.

## Uploading scans automatically

1. Run **`install-autostart.bat`** once. The sync program then runs hidden every time you log in to Windows.
   It also installs the **AuctionhouseSync** addon into WoW, so restart WoW or `/reload` once afterwards.
2. Scan the auction house with Auctionator as usual. When a full scan finishes, the addon asks
   **Reload & Upload**. Click it, and the scan is on the website a minute or two later.

WoW only writes addon data to disk on `/reload`, logout or exit, so that is when a scan can be uploaded.
If you click **Later**, the scan is kept and uploaded on your next reload, logout or exit.

| Script | What it does |
| --- | --- |
| `install-autostart.bat` | Start the sync hidden at every Windows login (and now) |
| `uninstall-autostart.bat` | Stop it and remove it from startup |
| `sync.bat` | Run the sync in a window instead (close the window to stop) |
| `upload.bat` | Export and upload once |
| `preview.bat` | Export without uploading and open the site locally |

The sync log is `state/sync.log`. In game, `/ahsync` shows how many scans are stored, `/ahsync popup`
turns the reload question on or off.

## What the site shows

- **Browse**: the Classic auction house browser. Category tree, name, level, rarity and price filters, sortable columns.
- **Item pages**: current price, auctions and median in the latest scan, 3/7/14/30 day and all-time averages,
  lowest and highest ever, volatility, price history per scan and per day, best time of day and best weekday
  to buy and sell, every scan and every day in tables, disenchant breakdown (era table and your own results
  from DisenchantValue), vendor prices and vendors, and your own postings.
- **Market**: items, listings, auctions, market value and a price index per scan and per day, recent scans,
  a category breakdown, the biggest risers and fallers, the most listed, valuable, expensive and volatile
  items, and items new to or gone from the market.
- **Deals**: items listed below their 30 day average.
- **Disenchant**: items worth more disenchanted than their buyout, enchanting material prices, and value per item level.
- **Vendor Flips**: items listed below vendor price, and vendor items listed above vendor price.
- **Vendor Recipes**: recipes a vendor sells that are also on the auction house, vendor price vs auction price,
  with the vendors' names, zones, faction and limited stock. Filter by faction and profession.

Hover any item for its in-game tooltip with auction, average, disenchant and vendor prices. Press `/` to search.

## How it works

- Auctionator keeps one low/high price and quantity per item per day, for 21 days. The **AuctionhouseSync**
  addon (`addon/`) hooks Auctionator's scan processing and records every scan with its time and, per item,
  the lowest price, quantity, number of auctions and median price.
- `tools/sync.py` watches WoW's saved variables and runs `tools/export.py` whenever they change.
- `tools/export.py` reads `WTF/Account/*/SavedVariables/` for every account and merges everything into
  `state/archive/<realm>/`. `daily.json` keeps every day forever. `scans/<day>.json` keeps per-scan detail for
  `scan_history_days`, and each day's scan average and count are kept in the daily history after that.
- Item names, tooltips, classes and icons come from Wowhead's `forever` database and are fetched once.
  They are stored in `state/items.json` and `state/icons/`. Which vendors sell each recipe comes from the
  recipe's Wowhead page. That is stored in `state/vendors.json` and rechecked every 30 days.
- The site is built into `site/` from `docs/` (plain HTML, CSS and JS, no build step) plus the data, split
  into small files so pages load fast. `site/` is published to the `gh-pages` branch as a single commit that
  is replaced on every upload, so frequent uploads do not grow the repository.
- `state/` is not in git. A copy is published with the site under `_state/`, and a fresh clone restores it
  from there automatically on its first export.

## Settings (`config.json`)

- `wow_path`: your WoW install folder (the one containing `WTF`).
- `realm_aliases`: merge realms, for example `{"ClassicBetaPvP": "PvP"}` if a realm was renamed.
- `hide_realms`: realm names to leave off the site.
- `realm_order`: order of the realm dropdown.
- `scan_history_days`: how long per-scan detail is kept (daily history is kept forever).
- `check_interval_seconds`: how often the sync looks for new data.
- `troll_filter`: joke listings (an item worth nothing put up for thousands or millions of gold) are left out
  of every price, average, chart and market total. The archive keeps them, so changing these settings
  recalculates everything. A price is ignored when it is
  - above `max_price_gold` (5,000g), or above the item's own cap in `item_max_gold` (`{"itemID": gold}`),
  - a grey item at `trash_min_gold` (10g) or more and over `trash_vendor_multiple` (200) times its vendor price,
  - more than `spike_factor` (20) times the item's usual price while `spike_max_quantity` (5) or fewer are
    listed, or `extreme_factor` (100) times its usual price whatever the quantity.
  The usual price is the lower median of all the item's prices, so a lasting real price change soon becomes
  the new usual price. Item pages list every ignored price and why.
- `site_title`: header title.
- `push`: set `false` to only export.

Requires Python 3.10+ and git. There are no other dependencies.
