"""Market data for the GoldScout addon: estimated sales per item per day, and who sells what.

GoldScout (C:\\Servers\\Software\\addons\\GoldScout) only sees its own sales in the game. This writes
the estimates from sales.py (every item's sales, per day) and the newest seller scan per item into
the addon's folder as MarketData.lua, which the game loads on /reload. export.py calls write() after
every sync; run this file directly to write it once.
"""
import json
import os
import time

DAYS = 14 # days of sales history to hand over
MAX_ROWS = 15 # seller scan rows per item (cheapest first)

def lua_str(s):
	return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'

def load(path, default):
	try:
		with open(path, encoding="utf-8") as f:
			return json.load(f)
	except (OSError, ValueError):
		return default

def realm_block(folder, today):
	"""One realm's archive folder as a Lua table body, and the realm's name."""
	daily = load(os.path.join(folder, "daily.json"), {})
	sales = load(os.path.join(folder, "sales.json"), {})
	crawl = load(os.path.join(folder, "crawl.json"), {})
	name = daily.get("realm")
	if not name:
		return None, None
	sold = {}
	for day, items in (sales.get("days") or {}).items():
		if not day.isdigit() or int(day) < today - DAYS:
			continue
		for item_id, vals in items.items():
			units, value = vals[0], vals[1]
			if units > 0:
				sold.setdefault(item_id, []).append(f"{day}:{round(units, 2)}:{round(value)}")
	cover = {day: max(f.values()) for day, f in (sales.get("cover") or {}).items()
		if day.isdigit() and int(day) >= today - DAYS and f}
	lines = [f"\t[{lua_str(name)}] = {{"]
	lines.append("\t\tcover = { " + ", ".join(f"[{d}] = {c}" for d, c in sorted(cover.items())) + " },")
	lines.append("\t\tsold = {")
	for item_id in sorted(sold, key=int):
		lines.append(f"\t\t\t[{item_id}] = {lua_str(','.join(sorted(sold[item_id])))},")
	lines.append("\t\t},")
	lines.append("\t\tsellers = {")
	for item_id in sorted(crawl, key=int):
		t, _, rows = crawl[item_id]
		parts = []
		for qty, unit, owners, total in sorted(rows, key=lambda r: r[1])[:MAX_ROWS]:
			names = "/".join(o.replace(":", " ").replace(";", " ").replace("/", " ") for o in owners)
			parts.append(f"{qty}:{unit}:{names}:{total}")
		lines.append(f"\t\t\t[{item_id}] = {{ {t}, {lua_str(';'.join(parts))} }},")
	lines.append("\t\t},")
	lines.append("\t},")
	return name, "\n".join(lines)

def write(cfg, state_dir, day0):
	"""Writes MarketData.lua into the installed GoldScout addon, if there is one. Returns the path or None."""
	addon = os.path.join(cfg["wow_path"], "Interface", "AddOns", "GoldScout")
	if not os.path.isdir(addon):
		return None
	today = int((time.time() - day0) // 86400)
	blocks = []
	archive = os.path.join(state_dir, "archive")
	for slug in sorted(os.listdir(archive)) if os.path.isdir(archive) else []:
		name, block = realm_block(os.path.join(archive, slug), today)
		if name:
			blocks.append(block)
	body = "\n".join([
		"-- Written by auctionhouse/tools/goldscout.py after every sync. Do not edit: it is replaced.",
		"-- sold[itemID] = \"day:units:value,...\" (estimated auction house sales, all sellers; day = days since day0)",
		"-- sellers[itemID] = { scan time, \"qty:unit price:seller/seller:sellers;...\" } (newest seller scan)",
		"GoldScoutMarket = {",
		f"\tgenerated = {int(time.time())},",
		f"\tday0 = {int(day0)},",
		"\trealms = {",
		*["\t" + line for block in blocks for line in block.split("\n")],
		"\t},",
		"}",
		"",
	])
	path = os.path.join(addon, "MarketData.lua")
	tmp = path + ".tmp"
	with open(tmp, "w", encoding="utf-8", newline="\n") as f:
		f.write(body)
	os.replace(tmp, path)
	return path

if __name__ == "__main__":
	import export
	path = write(export.load_config(), export.STATE, export.SCAN_DAY_0)
	print(f"wrote {path}" if path else "GoldScout is not installed in the configured WoW folder")
