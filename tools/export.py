"""Reads Auctionator's saved scan data, merges it into a permanent archive and builds the website data.

Usage:
	python tools/export.py            export, then commit and push the site (if "push" is on in config.json)
	python tools/export.py --no-push  export only (preview locally with: python -m http.server -d docs)
	python tools/export.py --watch    stay running and export + push every time WoW saves new scan data

Auctionator only keeps 21 days of daily prices; archive/<realm>.json keeps everything ever seen.
"""
import argparse, datetime, glob, json, math, os, shutil, statistics, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import disenchant, luasv, wowhead

DOCS = os.path.join(ROOT, "docs")
DATA = os.path.join(DOCS, "data")
ARCHIVE = os.path.join(ROOT, "archive")
ITEMS_FILE = os.path.join(ROOT, "data", "items.json")
SHARDS = 32 # history and tooltip files are split by itemID % SHARDS so the site loads small pieces
SCAN_DAY_0 = datetime.datetime(2020, 1, 1).timestamp() # Auctionator's day 0 (local midnight)

DEFAULT_CONFIG = {
	"wow_path": "C:/Program Files (x86)/World of Warcraft/_classic_beta_",
	"site_title": "Auction House",
	"realm_aliases": {},
	"hide_realms": [],
	"realm_order": ["PvE", "PvP", "RP", "Hardcore"],
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
	if not os.path.exists(path):
		save_json(path, cfg, pretty=True)
	return cfg

def slug(realm):
	return "".join(c if c.isalnum() else "-" for c in realm).strip("-").lower() or "realm"

def day_dict(v):
	# Lua tables with no entries come back from CBOR as [] instead of {}
	return v if isinstance(v, dict) else {}

# ---------------------------------------------------------------------------
# Reading Auctionator
# ---------------------------------------------------------------------------

def read_accounts(cfg):
	"""Yields (accountName, savedVariables) for every account that has Auctionator data."""
	pattern = os.path.join(cfg["wow_path"], "WTF", "Account", "*", "SavedVariables", "Auctionator.lua")
	for path in sorted(glob.glob(pattern)):
		account = os.path.basename(os.path.dirname(os.path.dirname(path)))
		try:
			yield account, luasv.load(path), os.path.getmtime(path)
		except Exception as e:
			print(f"  skipping {path}: {e}", file=sys.stderr)

def read_disenchant_buckets(cfg):
	"""Learned disenchant results from the DisenchantValue addon, merged over all accounts."""
	merged = {}
	pattern = os.path.join(cfg["wow_path"], "WTF", "Account", "*", "SavedVariables", "DisenchantValue.lua")
	for path in glob.glob(pattern):
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

def merge_realm(archive, realm_data):
	"""Merges one Auctionator realm table into the archive. Returns the newest day seen."""
	items = archive.setdefault("items", {})
	newest = 0
	for key, rec in realm_data.items():
		if not isinstance(rec, dict) or "h" not in rec:
			continue
		h, l, a = day_dict(rec.get("h")), day_dict(rec.get("l")), day_dict(rec.get("a"))
		if not h:
			continue
		entry = items.setdefault(str(key), {"m": 0, "md": 0, "d": {}})
		days = entry["d"]
		for day, high in h.items():
			day = str(day)
			low = l.get(day, high)
			avail = a.get(day)
			old = days.get(day)
			if old:
				old[0] = min(old[0], low)
				old[1] = max(old[1], high)
				if avail is not None:
					old[2] = max(old[2] or 0, avail)
			else:
				days[day] = [low, high, avail]
		last = max(int(d) for d in h)
		newest = max(newest, last)
		if last >= entry["md"] and rec.get("m"):
			entry["m"], entry["md"] = rec["m"], last
	return newest

# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def pct(a, b):
	return round((a / b - 1) * 100, 1) if a and b else None

def item_stats(entry, today):
	"""Summary numbers for one item. Daily price = midpoint of the lowest and highest minimum seen that day."""
	days = sorted((int(d), v) for d, v in entry["d"].items())
	daily = [(d, (v[0] + v[1]) / 2) for d, v in days]

	def avg(n):
		vals = [p for d, p in daily if d > today - n]
		return round(sum(vals) / len(vals)) if vals else None

	prices = [p for _, p in daily]
	cur = entry["m"]
	prev = daily[-2][1] if len(daily) > 1 else None
	last = daily[-1][1]
	week_ago = next((p for d, p in reversed(daily) if d <= today - 7), daily[0][1])
	avails = [v[2] for _, v in days if v[2] is not None]
	mean = sum(prices) / len(prices)
	vol = round(statistics.pstdev(prices) / mean * 100, 1) if len(prices) > 1 and mean else 0
	return {
		"cur": cur,
		"curDay": entry["md"],
		"a3": avg(3), "a7": avg(7), "a14": avg(14), "a30": avg(30), "all": round(mean),
		"min": min(v[0] for _, v in days),
		"max": max(v[1] for _, v in days),
		"chg": pct(last, prev), # last seen day vs the one before
		"wk": pct(last, week_ago), # last seen day vs ~a week earlier
		"vs30": pct(cur, avg(30)), # current price vs 30 day average
		"vol": vol,
		"av": days[-1][1][2] or 0,
		"avAvg": round(sum(avails) / len(avails)) if avails else 0,
		"seen": len(days),
		"first": days[0][0],
		"last": days[-1][0],
	}

def build_realm(name, archive, items, buckets):
	"""Writes docs/data/<realm>/ and returns the realm summary for realms.json."""
	today = max((e["md"] for e in archive["items"].values()), default=0)
	out = os.path.join(DATA, slug(name))
	if os.path.isdir(out):
		shutil.rmtree(out)

	stats = {i: item_stats(e, today) for i, e in archive["items"].items() if e["d"]}

	def price_now(item_id):
		s = stats.get(str(item_id))
		return s and s["last"] >= today - 7 and s["cur"] or None

	def price_avg(item_id):
		s = stats.get(str(item_id))
		return s and (s["a30"] or s["all"]) or None

	# Index: one row per item, everything the list views need to filter and sort.
	cols = ["id", "cur", "curDay", "a3", "a7", "a14", "a30", "all", "min", "max", "chg", "wk", "vs30", "vol",
		"av", "avAvg", "seen", "first", "last", "de", "deAvg", "deL", "deN"]
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

	# History shards: full daily history per item.
	shards = {}
	for item_id, e in archive["items"].items():
		days = sorted((int(d), v) for d, v in e["d"].items())
		shards.setdefault(int(item_id) % SHARDS, {})[item_id] = [[d, v[0], v[1], v[2]] for d, v in days]
	for n, shard in shards.items():
		save_json(os.path.join(out, "h", f"{n}.json"), shard)

	# Market over time: listings, distinct items and value (lowest price x quantity, capped at twice
	# the item's median) per day,
	# plus a price index (median of each item's price relative to its own all-time average).
	per_day = {}
	med = {i: statistics.median(v[0] for v in e["d"].values()) for i, e in archive["items"].items() if e["d"]}
	for item_id, e in archive["items"].items():
		s = stats.get(item_id)
		if not s:
			continue
		cls = (items.get(item_id) or {}).get("c", -1)
		for d, v in e["d"].items():
			p = per_day.setdefault(int(d), {"items": 0, "listings": 0, "value": 0, "rel": [], "cls": {}})
			p["items"] += 1
			qty = v[2] or 0
			p["listings"] += qty
			p["value"] += min(v[0], 2 * med[item_id]) * qty # placeholder listings (9999g) would swamp the total
			if s["all"]:
				p["rel"].append((v[0] + v[1]) / 2 / s["all"])
			c = p["cls"].setdefault(str(cls), [0, 0])
			c[0] += qty
			c[1] += v[0] * qty
	market = [[d, p["items"], p["listings"], p["value"], round(statistics.median(p["rel"]) * 100, 1) if p["rel"] else None, p["cls"]]
		for d, p in sorted(per_day.items())]
	save_json(os.path.join(out, "market.json"), {"cols": ["day", "items", "listings", "value", "index", "classes"], "days": market})

	last_scan = archive.get("lastScan") or (today * 86400 + SCAN_DAY_0)
	return {
		"name": name, "slug": slug(name), "items": len(rows), "today": today,
		"first": min((s["first"] for s in stats.values()), default=today),
		"days": len(per_day), "lastScan": int(last_scan),
	}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def export(cfg, fetch=True):
	t0 = time.time()
	accounts = list(read_accounts(cfg))
	if not accounts:
		sys.exit(f"No Auctionator data found under {cfg['wow_path']}\\WTF\\Account\\*\\SavedVariables")
	aliases, hidden = cfg.get("realm_aliases") or {}, set(cfg.get("hide_realms") or [])

	archives, posting, vendor = {}, {}, {}
	for account, sv, mtime in accounts:
		print(f"reading account {account}")
		scan_time = (sv.get("AUCTIONATOR_SAVEDVARS") or {}).get("TimeOfLastBrowseScan") or 0
		for realm, blob in (sv.get("AUCTIONATOR_PRICE_DATABASE") or {}).items():
			if realm == "__dbversion":
				continue
			try:
				data = luasv.cbor(blob) if isinstance(blob, bytes) else blob
			except Exception as e:
				print(f"  {realm}: could not decode ({e})", file=sys.stderr)
				continue
			if not isinstance(data, dict):
				continue
			name = aliases.get(realm, realm)
			if name in hidden:
				continue
			if name not in archives:
				archives[name] = load_json(os.path.join(ARCHIVE, slug(name) + ".json"), {"realm": name, "items": {}})
			arc = archives[name]
			newest = merge_realm(arc, data)
			if newest and scan_time and int((scan_time - SCAN_DAY_0) // 86400) == newest:
				arc["lastScan"] = max(arc.get("lastScan") or 0, scan_time)
			print(f"  {realm}{' -> ' + name if name != realm else ''}: {sum(1 for k in data if k != 'version')} items")

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

	archives = {n: a for n, a in archives.items() if a["items"]}
	for name, arc in archives.items():
		save_json(os.path.join(ARCHIVE, slug(name) + ".json"), arc)

	# Item metadata and icons
	items = load_json(ITEMS_FILE, {})
	ids = set(disenchant.MATERIALS)
	for arc in archives.values():
		ids |= {int(i) for i in arc["items"]}
	if fetch:
		changed = wowhead.update_items(items, sorted(ids))
		save_json(ITEMS_FILE, items) # before classifying, so a failure there keeps the fetched tooltips
		if wowhead.update_classes(items) or changed:
			save_json(ITEMS_FILE, items)
		wowhead.update_icons(items, os.path.join(DOCS, "icons"))
	else:
		wowhead.update_items(items, [i for i in sorted(ids) if os.path.exists(os.path.join(wowhead.CACHE, "xml", f"{i}.xml"))])

	buckets = read_disenchant_buckets(cfg)
	realms = [build_realm(n, a, items, buckets) for n, a in archives.items()]
	order = [n.lower() for n in cfg.get("realm_order") or []]
	realms.sort(key=lambda r: (order.index(r["name"].lower()) if r["name"].lower() in order else len(order), r["name"]))

	# Remove site data and archives of realms that are no longer exported (hidden or renamed).
	keep = {r["slug"] for r in realms}
	for entry in os.listdir(DATA):
		if os.path.isdir(os.path.join(DATA, entry)) and entry not in ("tt",) and entry not in keep:
			shutil.rmtree(os.path.join(DATA, entry))
	for h in hidden:
		path = os.path.join(ARCHIVE, slug(h) + ".json")
		if os.path.exists(path):
			os.remove(path)

	# Item list for the site: compact rows plus lookup tables for class/subclass/slot names.
	classes, subclasses, slots = {}, {}, {}
	rows = {}
	for i in sorted(ids):
		m = items.get(str(i))
		if not m or m.get("missing"):
			rows[i] = [f"Item #{i}", 1, "inv_misc_questionmark", -1, -1, 0, 0, 0, 0, vendor.get(str(i), 0)]
			continue
		classes[m["c"]] = m["cn"]
		subclasses[f"{m['c']}:{m['s']}"] = m["sn"]
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

	save_json(os.path.join(DATA, "posting.json"), {i: sorted(p.values()) for i, p in posting.items()})
	save_json(os.path.join(DATA, "disenchant.json"), {
		"materials": disenchant.MATERIALS,
		"era": {str(c): {str(q): rows_ for q, rows_ in t.items()} for c, t in disenchant.ERA.items()},
		"learned": buckets,
	})
	save_json(os.path.join(DATA, "realms.json"), {
		"title": cfg.get("site_title") or "Auction House",
		"generated": int(time.time()), "day0": int(SCAN_DAY_0), "shards": SHARDS, "realms": realms,
	})
	print(f"exported {', '.join(f'{r['name']} ({r['items']} items)' for r in realms)} in {time.time() - t0:.1f}s")

def git(*args, check=True):
	return subprocess.run(["git", "-C", ROOT, *args], check=check, capture_output=True, text=True)

def push():
	git("add", "-A", "docs", "archive", "data")
	if not git("diff", "--cached", "--quiet", check=False).returncode:
		print("nothing new to upload")
		return
	stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
	git("commit", "-m", f"Auction data {stamp}")
	r = git("push", check=False)
	if r.returncode:
		print("push failed:\n" + r.stderr, file=sys.stderr)
	else:
		print("uploaded; GitHub Pages updates in a minute or two")

def saved_files(cfg):
	pattern = os.path.join(cfg["wow_path"], "WTF", "Account", "*", "SavedVariables", "Auctionator.lua")
	return {p: os.path.getmtime(p) for p in glob.glob(pattern)}

def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--no-push", action="store_true", help="export only, do not commit/push")
	ap.add_argument("--no-fetch", action="store_true", help="do not ask Wowhead about new items (they show as Item #id)")
	ap.add_argument("--watch", action="store_true", help="keep running and export whenever WoW saves Auctionator data")
	args = ap.parse_args()
	cfg = load_config()
	do_push = cfg.get("push") and not args.no_push

	def run():
		export(cfg, fetch=not args.no_fetch)
		if do_push:
			push()

	run()
	if args.watch:
		print("watching for new scans (WoW writes them on /reload, logout or exit)... Ctrl+C to stop")
		seen = saved_files(cfg)
		while True:
			time.sleep(10)
			now = saved_files(cfg)
			if now != seen:
				time.sleep(3) # let WoW finish writing
				seen = saved_files(cfg)
				print(f"\n{datetime.datetime.now():%H:%M:%S} new scan data")
				try:
					run()
				except Exception as e:
					print(f"export failed: {e}", file=sys.stderr)

if __name__ == "__main__":
	main()
