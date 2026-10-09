"""Reads Auctionator's saved data (and every scan the AuctionhouseSync addon recorded), merges it into a
permanent archive and builds the website.

Usage:
	python tools/export.py            export, then publish the site to GitHub Pages (if "push" is on in config.json)
	python tools/export.py --no-push  export only (preview with: python -m http.server -d site)
	python tools/sync.py              keep running and upload every time WoW saves new scan data

Auctionator keeps one low/high price per item per day for 21 days. The addon adds every scan with its
time. state/archive/<realm>/daily.json keeps the daily history forever; per-scan detail is kept for
"scan_history_days" in state/archive/<realm>/scans/<day>.json and folded into the daily history.
"""
import argparse, datetime, glob, json, os, shutil, statistics, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import disenchant, luasv, publish, wowhead

APP = os.path.join(ROOT, "docs") # the site's own files (html, js, css, fonts, images)
SITE = publish.SITE # built site, also the gh-pages checkout
DATA = os.path.join(SITE, "data")
STATE = publish.STATE
ARCHIVE = os.path.join(STATE, "archive")
ICONS = os.path.join(STATE, "icons")
ITEMS_FILE = os.path.join(STATE, "items.json")
VENDORS_FILE = os.path.join(STATE, "vendors.json")
ZONES_FILE = os.path.join(HERE, "zones.json")
SHARDS = 32 # history and tooltip files are split by itemID % SHARDS so the site loads small pieces
SNAPSHOT_DAYS = 90 # days that can be compared on the market page (every kept scan can be)
SCAN_DAY_0 = datetime.datetime(2020, 1, 1).timestamp() # Auctionator's day 0 (local midnight)

DEFAULT_CONFIG = {
	"wow_path": "C:/Program Files (x86)/World of Warcraft/_classic_beta_",
	"site_title": "Auction House",
	"realm_aliases": {},
	"hide_realms": [],
	"realm_order": ["PvE", "PvP", "RP", "Hardcore"],
	"scan_history_days": 45,
	"check_interval_seconds": 10,
	# Joke listings (an item worth nothing put up for millions) are left out of all statistics.
	"troll_filter": {
		"max_price_gold": 5000, # no real listing costs more than this per item
		"spike_factor": 20, # ...or more than this many times the item's usual price,
		"spike_max_quantity": 5, # when no more than this many are listed (big real supply is never ignored)
		"extreme_factor": 100, # this many times the usual price is ignored whatever the quantity
		"trash_min_gold": 10, # grey items listed for at least this much
		"trash_vendor_multiple": 200, # ...and this many times their vendor price
		"item_max_gold": {}, # per item ID, e.g. {"19019": 2000}: anything above is ignored
	},
	"push": True,
}

def load_json(path, default):
	try:
		with open(path, encoding="utf-8") as f:
			return json.load(f)
	except FileNotFoundError:
		return default

def save_json(path, data, pretty=False):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8") as f:
		if pretty:
			json.dump(data, f, indent="\t", ensure_ascii=False, sort_keys=True)
		else:
			json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
	os.replace(tmp, path)

def load_config():
	path = os.path.join(ROOT, "config.json")
	cfg = dict(DEFAULT_CONFIG)
	cfg.update(load_json(path, {}))
	if set(cfg) - set(load_json(path, {})): # write new settings with their defaults
		save_json(path, cfg, pretty=True)
	return cfg

def slug(realm):
	return "".join(c if c.isalnum() else "-" for c in realm).strip("-").lower() or "realm"

def scan_day(t):
	return int((t - SCAN_DAY_0) // 86400)

def day_dict(v):
	# Lua tables with no entries come back from CBOR as [] instead of {}
	return v if isinstance(v, dict) else {}

def saved_variables(cfg, addon):
	return sorted(glob.glob(os.path.join(cfg["wow_path"], "WTF", "Account", "*", "SavedVariables", addon + ".lua")))

# ---------------------------------------------------------------------------
# Reading WoW saved variables
# ---------------------------------------------------------------------------

def read_accounts(cfg):
	"""Yields (accountName, savedVariables) for every account that has Auctionator data."""
	for path in saved_variables(cfg, "Auctionator"):
		account = os.path.basename(os.path.dirname(os.path.dirname(path)))
		try:
			yield account, luasv.load(path)
		except Exception as e:
			print(f"  skipping {path}: {e}", file=sys.stderr)

def read_sync_scans(cfg):
	"""Yields (realm, time, full, faction, {id: [min, qty, auctions, median]}) for every scan the addon stored."""
	for path in saved_variables(cfg, "AuctionhouseSync"):
		try:
			db = luasv.load(path).get("AuctionhouseSyncDB") or {}
		except Exception as e:
			print(f"  skipping {path}: {e}", file=sys.stderr)
			continue
		for s in (db.get("scans") or {}).values():
			if not isinstance(s, dict) or not s.get("t"):
				continue
			items = {}
			for part in (luasv.text(s.get("data")) or "").split(","):
				f = part.split(":")
				if len(f) == 5 and all(x.lstrip("-").isdigit() for x in f):
					items[f[0]] = [int(x) for x in f[1:]]
			if items:
				yield luasv.text(s.get("realm")) or "", int(s["t"]), bool(s.get("full")), luasv.text(s.get("faction")) or "", items

def read_disenchant_buckets(cfg):
	"""Learned disenchant results from the DisenchantValue addon, merged over all accounts."""
	merged = {}
	for path in saved_variables(cfg, "DisenchantValue"):
		try:
			db = luasv.load(path).get("DisenchantValueDB") or {}
		except Exception:
			continue
		for key, rec in (db.get("buckets") or {}).items():
			if not isinstance(rec, dict) or not rec.get("n"):
				continue
			m = merged.setdefault(key, {"n": 0, "items": {}})
			m["n"] += rec["n"]
			for item, qty in (rec.get("items") or {}).items():
				m["items"][str(item)] = m["items"].get(str(item), 0) + qty
	return merged

# ---------------------------------------------------------------------------
# Archive: daily history per item (forever) + per-scan detail (scan_history_days)
# ---------------------------------------------------------------------------

class Realm:
	def __init__(self, name):
		self.name = name
		self.dir = os.path.join(ARCHIVE, slug(name))
		self.daily = load_json(os.path.join(self.dir, "daily.json"), {"realm": name, "items": {}})
		self.items = self.daily["items"]
		self.scans = {} # day -> {str(t): {"full", "faction", "items"}}
		for path in glob.glob(os.path.join(self.dir, "scans", "*.json")):
			self.scans[int(os.path.basename(path)[:-5])] = load_json(path, {})
		self.dirty_days = set()

	def merge_auctionator(self, realm_data):
		"""Merges one Auctionator realm table (daily low/high/quantity). Returns the newest day seen."""
		newest = 0
		for key, rec in realm_data.items():
			if not isinstance(rec, dict) or "h" not in rec:
				continue
			h, l, a = day_dict(rec.get("h")), day_dict(rec.get("l")), day_dict(rec.get("a"))
			if not h:
				continue
			entry = self.items.setdefault(str(key), {"m": 0, "md": 0, "d": {}})
			for day, high in h.items():
				self.observe(entry, str(day), l.get(day, high), high, a.get(day))
			last = max(int(d) for d in h)
			newest = max(newest, last)
			# Auctionator's "m" has no time; a scan the addon recorded that day is at least as new
			if rec.get("m") and (last > entry["md"] or (last == entry["md"] and not entry.get("mt"))):
				entry["m"], entry["md"] = rec["m"], last
				entry.pop("mt", None)
		return newest

	@staticmethod
	def observe(entry, day, low, high, avail):
		old = entry["d"].get(day)
		if old:
			old[0] = min(old[0], low)
			old[1] = max(old[1], high)
			if avail is not None:
				old[2] = max(old[2] or 0, avail)
		else:
			entry["d"][day] = [low, high, avail]

	def merge_scan(self, t, full, faction, items):
		day = scan_day(t)
		store = self.scans.setdefault(day, {})
		key = str(t)
		if key in store:
			if full and not store[key]["full"]:
				store[key]["full"] = True
				self.dirty_days.add(day)
			return False
		store[key] = {"full": full, "faction": faction, "items": items}
		self.dirty_days.add(day)
		for item_id, (low, qty, _, _) in items.items():
			entry = self.items.setdefault(item_id, {"m": 0, "md": 0, "d": {}})
			self.observe(entry, str(day), low, low, qty)
			if day > entry["md"] or (day == entry["md"] and t >= entry.get("mt", 0)):
				entry["m"], entry["md"], entry["mt"] = low, day, t
		return True

	def scan_list(self):
		"""All kept scans, oldest first: (t, full, items)."""
		return sorted(((int(t), s["full"], s["items"]) for store in self.scans.values() for t, s in store.items()), key=lambda s: s[0])

	def fold_scans(self):
		"""Writes each day's mean scan price and scan count into the daily history (kept after the scans expire)."""
		for day, store in self.scans.items():
			sums = {}
			for s in store.values():
				for item_id, (low, *_rest) in s["items"].items():
					acc = sums.setdefault(item_id, [0, 0])
					acc[0] += low
					acc[1] += 1
			for item_id, (total, n) in sums.items():
				rec = self.items[item_id]["d"].get(str(day))
				if rec:
					rec[3:] = [round(total / n), n]

	def save(self, keep_days):
		today = max((e["md"] for e in self.items.values()), default=0)
		for day in [d for d in self.scans if d <= today - keep_days]:
			del self.scans[day]
			path = os.path.join(self.dir, "scans", f"{day}.json")
			if os.path.exists(path):
				os.remove(path)
		for day in self.dirty_days:
			if day in self.scans:
				save_json(os.path.join(self.dir, "scans", f"{day}.json"), self.scans[day])
		self.dirty_days.clear()
		save_json(os.path.join(self.dir, "daily.json"), self.daily)

# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def pct(a, b):
	return round((a / b - 1) * 100, 1) if a and b else None

def day_price(v):
	"""One price per day: the mean of that day's scans when there were any, else the low/high midpoint."""
	return v[3] if len(v) > 3 and v[3] else (v[0] + v[1]) / 2

def item_stats(entry, today, points):
	"""Summary numbers for one item. points: [(t, min, qty, auctions, median)] from recorded scans."""
	days = sorted((int(d), v) for d, v in entry["d"].items())
	daily = [(d, day_price(v)) for d, v in days]

	def avg(n):
		vals = [p for d, p in daily if d > today - n]
		return round(sum(vals) / len(vals)) if vals else None

	prices = [p for _, p in daily]
	cur = entry["m"] or None # 0: the current listing is a joke
	latest = points[-1] if points and points[-1][0] == entry.get("mt") else None
	if len(points) >= 2:
		chg = pct(points[-1][1], points[-2][1]) # since the previous scan it was in
	else:
		chg = pct(daily[-1][1], daily[-2][1]) if len(daily) > 1 else None
	week_ago = next((p for d, p in reversed(daily) if d <= today - 7), daily[0][1])
	avails = [v[2] for _, v in days if v[2] is not None]
	mean = sum(prices) / len(prices)
	return {
		"cur": cur,
		"curDay": entry["md"],
		"curT": entry.get("mt") or int(SCAN_DAY_0 + entry["md"] * 86400 + 43200),
		"a3": avg(3), "a7": avg(7), "a14": avg(14), "a30": avg(30), "all": round(mean),
		"min": min(v[0] for _, v in days),
		"max": max(v[1] for _, v in days),
		"chg": chg,
		"wk": pct(daily[-1][1], week_ago), # last seen day vs ~a week earlier
		"vs30": pct(cur, avg(30)), # current price vs 30 day average
		"vol": round(statistics.pstdev(prices) / mean * 100, 1) if len(prices) > 1 and mean else 0,
		"av": latest[2] if latest else (days[-1][1][2] or 0),
		"avAvg": round(sum(avails) / len(avails)) if avails else 0,
		"n": latest[3] if latest else None, # auctions in the latest scan
		"med": latest[4] if latest else None, # median unit price in the latest scan
		"pts": len(points),
		"seen": len(days),
		"first": days[0][0],
		"last": days[-1][0],
	}

# ---------------------------------------------------------------------------
# Troll filter: joke listings are kept in the archive but left out of every statistic
# ---------------------------------------------------------------------------

def troll_settings(cfg):
	t = dict(DEFAULT_CONFIG["troll_filter"])
	t.update(cfg.get("troll_filter") or {})
	return t

def clean_item(item_id, entry, points, meta, tcfg):
	"""Removes joke prices from one item's history.
	Returns (entry with cleaned daily rows, cleaned scan points, ignored [[time|day, price, reason]], troll price of the current listing or None).
	A price is a joke when it is above the price ceiling, a grey item far above its vendor price, or a spike far above
	the item's usual price (the lower median of all its prices) while only a few are listed, or extremely far above it."""
	ceiling = (tcfg["item_max_gold"].get(str(item_id)) or tcfg["max_price_gold"]) * 10000
	sell, quality = meta.get("sell") or 0, meta.get("q", 1)

	def absolute(p):
		if p > ceiling:
			return f"above {ceiling // 10000:,}g"
		if quality == 0 and p >= max(tcfg["trash_min_gold"] * 10000, tcfg["trash_vendor_multiple"] * sell):
			return "grey item far above its vendor price"
		return None

	usual = [v[0] for v in entry["d"].values() if not absolute(v[0])] + [p[1] for p in points if not absolute(p[1])]
	ref = statistics.median_low(usual) if len(usual) >= 2 else None

	def joke(p, qty):
		reason = absolute(p)
		if not reason and ref and p > tcfg["spike_factor"] * ref and (
				qty is None or qty <= tcfg["spike_max_quantity"] or p >= tcfg["extreme_factor"] * ref):
			reason = f"{p / ref:,.0f}x its usual price"
		return reason

	ignored, clean_points, by_day, troll_days = [], [], {}, set()
	for t, low, qty, n, med in points:
		reason = joke(low, qty)
		if reason:
			ignored.append([t, low, reason])
			troll_days.add(scan_day(t))
			continue
		if med and joke(med, qty):
			med = None
		clean_points.append((t, low, qty, n, med))
		by_day.setdefault(scan_day(t), []).append(low)

	days = {}
	for d, v in entry["d"].items():
		day = int(d)
		low, high, scans = v[0], v[1], by_day.get(day)
		if joke(low, v[2]):
			if not scans: # nothing but joke listings that day
				if day not in troll_days:
					ignored.append([day, v[0], joke(low, v[2])])
				continue
			low = min(scans)
		if joke(high, v[2]):
			high = max(scans) if scans else low
		high = max(high, low)
		mean = statistics.mean(scans) if scans else (v[3] if len(v) > 3 and v[3] and not joke(v[3], None) else None)
		count = len(scans) if scans else (v[4] if len(v) > 4 else 0)
		days[d] = [low, high, v[2], round(mean) if mean else None, count]

	# the current listing, with the quantity it was listed in (a big real supply is never a joke)
	latest = points[-1] if points and points[-1][0] == entry.get("mt") else None
	cur_qty = latest[2] if latest else (entry["d"].get(str(entry["md"])) or [0, 0, None])[2]
	troll = entry["m"] if entry["m"] and joke(entry["m"], cur_qty) else None
	clean = {"m": 0 if troll else entry["m"], "md": entry["md"], "d": days}
	if entry.get("mt"):
		clean["mt"] = entry["mt"]
	return clean, clean_points, ignored, troll

def blank_stats(entry):
	"""Stats for an item whose every listing was a joke: seen on the AH, but no real price."""
	days = sorted((int(d), v) for d, v in entry["d"].items())
	return {
		"cur": None, "curDay": entry["md"], "curT": entry.get("mt") or int(SCAN_DAY_0 + entry["md"] * 86400 + 43200),
		"a3": None, "a7": None, "a14": None, "a30": None, "all": None, "min": None, "max": None,
		"chg": None, "wk": None, "vs30": None, "vol": 0, "av": days[-1][1][2] or 0, "avAvg": 0,
		"n": None, "med": None, "pts": 0,
	}

def build_realm(realm, items, buckets, auctionator_scan, tcfg):
	"""Writes site/data/<realm>/ and returns the realm summary for realms.json."""
	arc = realm.items
	today = max((e["md"] for e in arc.values()), default=0)
	out = os.path.join(DATA, slug(realm.name))
	if os.path.isdir(out):
		shutil.rmtree(out)

	scans = realm.scan_list()
	points = {}
	for t, _, scan_items in scans:
		for item_id, (low, qty, n, med) in scan_items.items():
			points.setdefault(item_id, []).append((t, low, qty, n, med))
	full = [s for s in scans if s[1]]
	latest_full = full[-1] if full and scan_day(full[-1][0]) == today else None

	# Statistics only use cleaned data (joke listings removed); the archive keeps everything.
	clean, ignored, trolls = {}, {}, {}
	stats = {}
	for i, e in arc.items():
		if not e["d"]:
			continue
		ce, cp, ign, troll = clean_item(i, e, points.get(i, []), items.get(i) or {}, tcfg)
		clean[i], points[i], ignored[i] = ce, cp, ign
		s = item_stats(ce, today, cp) if ce["d"] else blank_stats(e)
		raw_days = sorted(int(d) for d in e["d"])
		s.update(seen=len(raw_days), first=raw_days[0], last=raw_days[-1], troll=troll, ign=len(ign))
		# "on the AH now": in the latest full scan when there is one from today, else seen today
		s["inScan"] = int(i in latest_full[2]) if latest_full else int(s["last"] == today)
		stats[i] = s
	ignored_scans = {(i, x[0]) for i, ign in ignored.items() for x in ign}
	if any(ignored.values()):
		print(f"  {sum(len(x) for x in ignored.values())} joke prices ignored on {sum(1 for x in ignored.values() if x)} items")

	def price_now(item_id):
		s = stats.get(str(item_id))
		return s and s["last"] >= today - 7 and s["cur"] or None

	def price_avg(item_id):
		s = stats.get(str(item_id))
		return s and (s["a30"] or s["all"]) or None

	# Index: one row per item, everything the list views need to filter and sort.
	cols = ["id", "cur", "curDay", "curT", "a3", "a7", "a14", "a30", "all", "min", "max", "chg", "wk", "vs30", "vol",
		"av", "avAvg", "n", "med", "pts", "seen", "first", "last", "inScan", "troll", "ign", "de", "deAvg", "deL", "deN"]
	rows = []
	for item_id, s in stats.items():
		meta = items.get(item_id) or {}
		de = de_avg = de_learned = None
		de_n = 0
		drops = disenchant.era_drops(meta)
		if drops:
			de = disenchant.value(drops, price_now)[0] or None
			de_avg = disenchant.value(drops, price_avg)[0] or None
		ldrops, de_n, _ = disenchant.learned_drops(meta, buckets)
		if ldrops:
			de_learned = disenchant.value(ldrops, price_now)[0] or None
		s.update(id=int(item_id), de=de, deAvg=de_avg, deL=de_learned, deN=de_n)
		rows.append([s[c] for c in cols])
	rows.sort(key=lambda r: r[0])
	save_json(os.path.join(out, "index.json"), {"cols": cols, "today": today, "rows": rows})

	# History shards: daily history [day, low, high, avail, scanMean, scans], scans [t, min, qty, auctions, median]
	# and the joke prices that were left out [time or day, price, reason] (newest 50).
	shards = {}
	for item_id, e in clean.items():
		days = sorted((int(d), v) for d, v in e["d"].items())
		shards.setdefault(int(item_id) % SHARDS, {})[item_id] = {
			"d": [[d, *v] for d, v in days],
			"s": [list(p) for p in points.get(item_id, [])],
			"x": sorted(ignored[item_id], key=lambda x: x[0] if x[0] > 1e6 else SCAN_DAY_0 + x[0] * 86400)[-50:],
		}
	for n, shard in shards.items():
		save_json(os.path.join(out, "h", f"{n}.json"), shard)

	# Market over time, per day and per full scan. Value = lowest price x quantity, capped at twice the
	# item's median (placeholder listings at 9999g would swamp the total). Price index = median of every
	# item's price relative to its own average (100 = normal).
	med = {i: statistics.median(v[0] for v in e["d"].values()) for i, e in clean.items() if e["d"]}
	per_day = {}
	for item_id, e in clean.items():
		s = stats.get(item_id)
		if not s:
			continue
		cls = (items.get(item_id) or {}).get("c", -1)
		for d, v in e["d"].items():
			p = per_day.setdefault(int(d), {"items": 0, "listings": 0, "value": 0, "rel": [], "cls": {}})
			p["items"] += 1
			qty = v[2] or 0
			p["listings"] += qty
			p["value"] += round(min(v[0], 2 * med[item_id]) * qty)
			if s["all"]:
				p["rel"].append(day_price(v) / s["all"])
			c = p["cls"].setdefault(str(cls), [0, 0])
			c[0] += qty
			c[1] += v[0] * qty
	market_days = [[d, p["items"], p["listings"], p["value"], round(statistics.median(p["rel"]) * 100, 1) if p["rel"] else None, p["cls"]]
		for d, p in sorted(per_day.items())]
	market_scans = []
	for t, _, scan_items in full:
		listings = auctions = value = 0
		rel = []
		for item_id, (low, qty, n, _) in scan_items.items():
			if (item_id, t) in ignored_scans:
				continue
			listings += qty
			auctions += n
			value += round(min(low, 2 * med.get(item_id, low)) * qty)
			s = stats.get(item_id)
			if s and s["all"]:
				rel.append(low / s["all"])
		market_scans.append([t, len(scan_items), listings, auctions, value, round(statistics.median(rel) * 100, 1) if rel else None])
	# Snapshots for comparing two moments on the market page (joke prices left out):
	# snap/s<time>.json per full scan {id: [lowest, qty, auctions]}, snap/d<day>.json per day {id: [lowest, qty]}.
	for t, _, scan_items in full:
		save_json(os.path.join(out, "snap", f"s{t}.json"),
			{i: [low, qty, n] for i, (low, qty, n, _) in scan_items.items() if (i, t) not in ignored_scans})
	snap_days = {}
	for item_id, e in clean.items():
		for d, v in e["d"].items():
			if int(d) > today - SNAPSHOT_DAYS:
				snap_days.setdefault(int(d), {})[item_id] = [v[0], v[2] or 0]
	for d, snap in snap_days.items():
		save_json(os.path.join(out, "snap", f"d{d}.json"), snap)

	save_json(os.path.join(out, "market.json"), {
		"cols": ["day", "items", "listings", "value", "index", "classes"], "days": market_days,
		"scanCols": ["t", "items", "listings", "auctions", "value", "index"], "scans": market_scans,
	})

	last_scan = max(([full[-1][0]] if full else []) + [auctionator_scan.get(realm.name, 0)]) or int(SCAN_DAY_0 + today * 86400)
	return {
		"name": realm.name, "slug": slug(realm.name), "items": len(rows), "today": today,
		"first": min((s["first"] for s in stats.values()), default=today),
		"days": len(per_day), "scans": len(full), "lastScan": int(last_scan),
	}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def copy_tree(src, dst, only_missing=False):
	for base, _, files in os.walk(src):
		rel = os.path.relpath(base, src)
		os.makedirs(os.path.join(dst, rel), exist_ok=True)
		for f in files:
			target = os.path.join(dst, rel, f)
			if only_missing and os.path.exists(target):
				continue
			shutil.copy2(os.path.join(base, f), target)

def export(cfg, fetch=True):
	t0 = time.time()
	if publish.restore_state():
		print("restored the archive and caches from the published site")
	accounts = list(read_accounts(cfg))
	if not accounts:
		sys.exit(f"No Auctionator data found under {cfg['wow_path']}\\WTF\\Account\\*\\SavedVariables")
	aliases, hidden = cfg.get("realm_aliases") or {}, set(cfg.get("hide_realms") or [])

	realms, posting, vendor, auctionator_scan = {}, {}, {}, {}

	def realm(raw):
		name = aliases.get(raw, raw)
		if name in hidden:
			return None
		if name not in realms:
			realms[name] = Realm(name)
		return realms[name]

	for account, sv in accounts:
		print(f"reading account {account}")
		saved = sv.get("AUCTIONATOR_SAVEDVARS") or {}
		scan_time = max(saved.get("TimeOfLastBrowseScan") or 0, saved.get("TimeOfLastGetAllScan") or 0)
		for raw, blob in (sv.get("AUCTIONATOR_PRICE_DATABASE") or {}).items():
			if raw == "__dbversion":
				continue
			try:
				data = luasv.cbor(blob) if isinstance(blob, bytes) else blob
			except Exception as e:
				print(f"  {raw}: could not decode ({e})", file=sys.stderr)
				continue
			r = realm(raw) if isinstance(data, dict) else None
			if not r:
				continue
			newest = r.merge_auctionator(data)
			if newest and scan_time and scan_day(scan_time) == newest:
				auctionator_scan[r.name] = max(auctionator_scan.get(r.name, 0), scan_time)
			print(f"  {raw}{' -> ' + r.name if r.name != raw else ''}: {sum(1 for k in data if k != 'version')} items")

		# Your own postings (Auctionator does not record the realm, so these are shared).
		for item_id, posts in (sv.get("AUCTIONATOR_POSTING_HISTORY") or {}).items():
			if item_id == "__dbversion" or not isinstance(posts, dict):
				continue
			seen = posting.setdefault(str(item_id), {})
			for p in posts.values():
				if isinstance(p, dict) and p.get("time"):
					seen[(p["time"], p.get("price"))] = [p["time"], p.get("price") or 0, p.get("quantity") or 1]
		for item_id, price in (sv.get("AUCTIONATOR_VENDOR_PRICE_CACHE") or {}).items():
			if item_id != "__dbversion" and isinstance(price, (int, float)):
				vendor[str(item_id)] = int(price)

	new_scans = 0
	for raw, t, full, faction, scan_items in read_sync_scans(cfg):
		r = realm(raw)
		if r and r.merge_scan(t, full, faction, scan_items):
			new_scans += 1
	if new_scans:
		print(f"  {new_scans} new scans from AuctionhouseSync")

	realms = {n: r for n, r in realms.items() if r.items}
	for r in realms.values():
		r.fold_scans()
		r.save(cfg.get("scan_history_days") or 45)

	# Item metadata, icons and recipe vendors
	items = load_json(ITEMS_FILE, {})
	vendors = load_json(VENDORS_FILE, {})
	ids = set(disenchant.MATERIALS)
	for r in realms.values():
		ids |= {int(i) for i in r.items}
	if fetch:
		changed = wowhead.update_items(items, sorted(ids))
		save_json(ITEMS_FILE, items) # before classifying, so a failure there keeps the fetched tooltips
		if wowhead.update_classes(items) or changed:
			save_json(ITEMS_FILE, items)
		wowhead.update_icons(items, ICONS)
		mine = {str(i): items.get(str(i)) for i in ids}
		if wowhead.update_vendors(mine, vendors, load_json(ZONES_FILE, {})):
			save_json(VENDORS_FILE, vendors)

	# Build the site: app files, icons, data, and a backup of state/
	os.makedirs(SITE, exist_ok=True)
	if os.path.isdir(DATA):
		shutil.rmtree(DATA)
	copy_tree(APP, SITE)
	if os.path.isdir(ICONS):
		copy_tree(ICONS, os.path.join(SITE, "icons"), only_missing=True)
	backup = os.path.join(SITE, publish.BACKUP)
	if os.path.isdir(backup):
		shutil.rmtree(backup)
	for f in ("items.json", "vendors.json"):
		if os.path.exists(os.path.join(STATE, f)):
			os.makedirs(backup, exist_ok=True)
			shutil.copy2(os.path.join(STATE, f), backup)
	if os.path.isdir(ARCHIVE):
		copy_tree(ARCHIVE, os.path.join(backup, "archive"))

	buckets = read_disenchant_buckets(cfg)
	summaries = [build_realm(r, items, buckets, auctionator_scan, troll_settings(cfg)) for r in realms.values()]
	order = [n.lower() for n in cfg.get("realm_order") or []]
	summaries.sort(key=lambda r: (order.index(r["name"].lower()) if r["name"].lower() in order else len(order), r["name"]))
	for h in hidden:
		if os.path.isdir(os.path.join(ARCHIVE, slug(h))):
			shutil.rmtree(os.path.join(ARCHIVE, slug(h)))

	# Item list for the site: compact rows plus lookup tables for class/subclass/slot names.
	classes, subclasses, slots = {}, {}, {}
	rows = {}
	for i in sorted(ids):
		m = items.get(str(i))
		if not m or m.get("missing"):
			rows[i] = [f"Item #{i}", 1, "inv_misc_questionmark", -1, -1, 0, 0, 0, 0, vendor.get(str(i), 0)]
			continue
		# names from the current tables, so fixes there apply without fetching items again
		classes[m["c"]] = wowhead.CLASSES.get(m["c"]) or m["cn"]
		subclasses[f"{m['c']}:{m['s']}"] = wowhead.SUBCLASSES.get(m["c"], {}).get(m["s"]) or m["sn"]
		if m["slot"]:
			slots[m["slot"]] = m["slotn"]
		rows[i] = [m["name"], m["q"], m["icon"], m["c"], m["s"], m["slot"], m["ilvl"], m["req"], m["sell"], m["buy"] or vendor.get(str(i), 0)]
	save_json(os.path.join(DATA, "items.json"), {
		"cols": ["name", "q", "icon", "c", "s", "slot", "ilvl", "req", "sell", "buy"],
		"items": rows, "classes": classes, "subclasses": subclasses, "slots": slots,
	})

	tooltips = {}
	for i in ids:
		m = items.get(str(i))
		if m and m.get("tt"):
			tooltips.setdefault(i % SHARDS, {})[str(i)] = m["tt"]
	for n in range(SHARDS):
		save_json(os.path.join(DATA, "tt", f"{n}.json"), tooltips.get(n, {}))

	# NPCs selling each recipe (only recipes a vendor sells)
	save_json(os.path.join(DATA, "vendors.json"), {i: v["sold"] for i, v in vendors.items() if v.get("sold") and int(i) in ids})
	save_json(os.path.join(DATA, "posting.json"), {i: sorted(p.values()) for i, p in posting.items()})
	save_json(os.path.join(DATA, "disenchant.json"), {
		"materials": disenchant.MATERIALS,
		"era": {str(c): {str(q): rows_ for q, rows_ in t.items()} for c, t in disenchant.ERA.items()},
		"learned": buckets,
	})
	save_json(os.path.join(DATA, "realms.json"), {
		"title": cfg.get("site_title") or "Auction House",
		"generated": int(time.time()), "day0": int(SCAN_DAY_0), "shards": SHARDS, "realms": summaries,
	})
	print(f"exported {', '.join(f'{r['name']} ({r['items']} items, {r['scans']} scans)' for r in summaries)} in {time.time() - t0:.1f}s")

def run(cfg, fetch=True, push=True):
	export(cfg, fetch)
	if push:
		if publish.publish():
			print("uploaded; GitHub Pages updates in a minute or two")
		else:
			print("nothing new to upload")

def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--no-push", action="store_true", help="export only, do not upload")
	ap.add_argument("--no-fetch", action="store_true", help="do not ask Wowhead about new items (they show as Item #id)")
	args = ap.parse_args()
	cfg = load_config()
	run(cfg, fetch=not args.no_fetch, push=cfg.get("push") and not args.no_push)

if __name__ == "__main__":
	main()
