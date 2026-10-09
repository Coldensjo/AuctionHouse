"""Item metadata (name, quality, icon, class, levels, vendor prices, tooltip) and icons from Wowhead.

Results are kept in data/items.json (committed) so each item is only fetched once.
Raw XML responses are cached in tools/cache/xml/ (not committed).
"""
import html, json, os, re, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) auctionhouse-exporter"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
RETRY_MISSING_DAYS = 3 # items Wowhead did not know are asked for again after this many days

_rate_lock = threading.Lock()
_rate = {"delay": 0.35, "next": 0.0}

def fetch(url, binary=False, tries=8, polite=True):
	for i in range(tries):
		if polite:
			with _rate_lock:
				wait = _rate["next"] - time.time()
				if wait > 0:
					time.sleep(wait)
				_rate["next"] = time.time() + _rate["delay"]
		try:
			req = urllib.request.Request(url, headers={"User-Agent": UA})
			with urllib.request.urlopen(req, timeout=30) as r:
				data = r.read()
				return data if binary else data.decode("utf-8", "replace")
		except urllib.error.HTTPError as e:
			if e.code == 404:
				return None
			if i == tries - 1:
				raise
			if e.code in (403, 429):
				with _rate_lock:
					_rate["delay"] = min(_rate["delay"] * 1.5, 5)
					print(f"  wowhead rate limit, pausing 60s (now 1 request per {_rate['delay']:.2f}s)", file=sys.stderr)
					time.sleep(60)
			else:
				time.sleep(3 * (i + 1))
		except Exception:
			if i == tries - 1:
				raise
			time.sleep(3 * (i + 1))

def _tag(xml, name):
	m = re.search(r"<%s\b([^>]*)>(.*?)</%s>" % (name, name), xml, re.S)
	if not m:
		return None, None
	attrs, body = m.group(1), m.group(2)
	body = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", body.strip(), flags=re.S)
	idm = re.search(r'id="(-?\d+)"', attrs)
	return (int(idm.group(1)) if idm else None), html.unescape(body) if "CDATA" not in m.group(2) else body

def _json_fragment(xml, name):
	_, body = _tag(xml, name)
	if not body:
		return {}
	try:
		return json.loads("{" + body + "}")
	except ValueError:
		return {}

# Wowhead tooltips link to its own pages and carry scripts hooks; keep only the markup.
def clean_tooltip(t):
	t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
	t = re.sub(r"<script.*?</script>", "", t, flags=re.S | re.I)
	t = re.sub(r"<a\b[^>]*>", "<span class=\"lnk\">", t, flags=re.I)
	t = re.sub(r"</a>", "</span>", t, flags=re.I)
	t = re.sub(r"\s(?:on\w+|href|target|id|rel)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)", "", t, flags=re.I)
	return t

def parse_xml(xml):
	if not xml or "<error>" in xml or "<item " not in xml:
		return None
	_, name = _tag(xml, "name")
	quality, _ = _tag(xml, "quality")
	cls, cls_name = _tag(xml, "class")
	sub, sub_name = _tag(xml, "subclass")
	slot, slot_name = _tag(xml, "inventorySlot")
	_, icon = _tag(xml, "icon")
	_, level = _tag(xml, "level")
	_, tooltip = _tag(xml, "htmlTooltip")
	j = _json_fragment(xml, "json")
	eq = _json_fragment(xml, "jsonEquip")
	return {
		"name": name,
		"q": quality or 0,
		"icon": (icon or "inv_misc_questionmark").strip().lower(),
		"c": cls if cls is not None else -1,
		"cn": cls_name or "",
		"s": sub if sub is not None else -1,
		"sn": sub_name or "",
		"slot": slot or 0,
		"slotn": slot_name or "",
		"ilvl": int(level or j.get("level") or 0),
		"req": int(j.get("reqlevel") or 0),
		"sell": int(eq.get("sellprice") or 0),
		"buy": int(eq.get("buyprice") or 0),
		"tt": clean_tooltip(tooltip or ""),
	}

# Item classes and subclasses as the Classic auction house shows them.
CLASSES = {0: "Consumable", 1: "Container", 2: "Weapon", 3: "Gem", 4: "Armor", 5: "Reagent", 6: "Projectile",
	7: "Trade Goods", 9: "Recipe", 11: "Quiver", 12: "Quest", 13: "Key", 15: "Miscellaneous", -1: "Unknown"}
SUBCLASSES = {
	0: {0: "Consumable", 1: "Potion", 2: "Elixir", 3: "Flask", 4: "Scroll", 5: "Food & Drink", 6: "Item Enhancement", 7: "Bandage", 8: "Other"},
	1: {0: "Bag", 1: "Soul Bag", 2: "Herb Bag", 3: "Enchanting Bag", 4: "Engineering Bag", 5: "Gem Bag", 6: "Mining Bag", 7: "Leatherworking Bag"},
	2: {0: "One-Handed Axes", 1: "Two-Handed Axes", 2: "Bows", 3: "Guns", 4: "One-Handed Maces", 5: "Two-Handed Maces", 6: "Polearms",
		7: "One-Handed Swords", 8: "Two-Handed Swords", 10: "Staves", 13: "Fist Weapons", 14: "Miscellaneous", 15: "Daggers", 16: "Thrown",
		17: "Spears", 18: "Crossbows", 19: "Wands", 20: "Fishing Poles"},
	4: {0: "Miscellaneous", 1: "Cloth", 2: "Leather", 3: "Mail", 4: "Plate", 5: "Bucklers", 6: "Shields", 7: "Librams", 8: "Idols", 9: "Totems", 11: "Relics"},
	6: {2: "Arrow", 3: "Bullet"},
	7: {0: "Trade Goods", 1: "Parts", 2: "Explosives", 3: "Devices", 4: "Jewelcrafting", 5: "Cloth", 6: "Leather", 7: "Metal & Stone",
		8: "Meat", 9: "Herb", 10: "Elemental", 11: "Other", 12: "Enchanting", 13: "Materials"},
	9: {0: "Book", 1: "Leatherworking", 2: "Tailoring", 3: "Engineering", 4: "Blacksmithing", 5: "Cooking", 6: "Alchemy", 7: "First Aid",
		8: "Enchanting", 9: "Fishing", 10: "Jewelcrafting"},
	11: {2: "Quiver", 3: "Ammo Pouch"},
	15: {0: "Junk", 1: "Reagent", 2: "Pet", 3: "Holiday", 4: "Other", 5: "Mount"},
}
SLOTS = {1: "Head", 2: "Neck", 3: "Shoulder", 4: "Shirt", 5: "Chest", 6: "Waist", 7: "Legs", 8: "Feet", 9: "Wrist", 10: "Hands",
	11: "Finger", 12: "Trinket", 13: "One-Hand", 14: "Off Hand", 15: "Ranged", 16: "Back", 17: "Two-Hand", 18: "Bag", 19: "Tabard",
	20: "Chest", 21: "Main Hand", 22: "Off Hand", 23: "Held In Off-hand", 24: "Projectile", 25: "Thrown", 26: "Ranged", 28: "Relic"}
SLOT_IDS = {}
for _k, _v in SLOTS.items():
	SLOT_IDS.setdefault(_v, _k)

def set_class(meta, c, s, slot=None):
	meta["c"], meta["s"] = c, s
	meta["cn"] = CLASSES.get(c, meta.get("cn") or "Unknown")
	meta["sn"] = SUBCLASSES.get(c, {}).get(s) or meta.get("sn") or meta["cn"]
	if slot:
		meta["slot"], meta["slotn"] = slot, SLOTS.get(slot, meta.get("slotn", ""))

def parse_tooltip_json(text):
	"""Metadata from the nether.wowhead.com tooltip API (class/subclass only for gear; the rest comes from list pages)."""
	try:
		j = json.loads(text)
	except ValueError:
		return None
	if not j or not j.get("name"):
		return None
	tt = j.get("tooltip") or ""
	num = lambda pat: int(m.group(1)) if (m := re.search(pat, tt)) else 0
	sell_html = re.search(r'whtt-sellprice">(.*?)</div>', tt, re.S)
	sell = 0
	if sell_html:
		for unit, mult in (("gold", 10000), ("silver", 100), ("copper", 1)):
			m = re.search(r'money%s">(\d+)' % unit, sell_html.group(1))
			sell += int(m.group(1)) * mult if m else 0
	slot_m = re.search(r'<table width="100%"><tr><td>([^<]+)</td>', tt)
	slotn = slot_m.group(1).strip() if slot_m else ""
	meta = {
		"name": j["name"], "q": int(j.get("quality") or 0), "icon": (j.get("icon") or "inv_misc_questionmark").lower(),
		"c": -1, "cn": "Unknown", "s": -1, "sn": "Unknown", "slot": SLOT_IDS.get(slotn, 0), "slotn": slotn,
		"ilvl": num(r"<!--ilvl-->(\d+)"), "req": num(r"<!--rlvl-->(\d+)"), "sell": sell, "buy": 0,
		"tt": clean_tooltip(tt),
	}
	sc = re.search(r"<!--scstart(\d+):(\d+)-->", tt)
	if sc:
		set_class(meta, int(sc.group(1)), int(sc.group(2)))
	return meta

def _cached(kind, item_id, url):
	path = os.path.join(CACHE, kind, f"{item_id}")
	if os.path.exists(path):
		with open(path, encoding="utf-8") as f:
			return f.read()
	text = fetch(url, polite=kind != "tt")
	if text is None:
		text = ""
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w", encoding="utf-8") as f:
		f.write(text)
	return text

def item_meta(item_id):
	xml_path = os.path.join(CACHE, "xml", f"{item_id}.xml")
	if os.path.exists(xml_path):
		with open(xml_path, encoding="utf-8") as f:
			meta = parse_xml(f.read())
		if meta:
			set_class(meta, meta["c"], meta["s"], meta["slot"])
			meta["classed"] = True
			return meta
	return parse_tooltip_json(_cached("tt", item_id, f"https://nether.wowhead.com/forever/tooltip/item/{item_id}"))

def update_items(items, ids, workers=8):
	"""Fills `items` ({id: meta}) for every id not known yet. Returns the number fetched."""
	now = int(time.time())
	todo = []
	for i in ids:
		meta = items.get(str(i))
		if meta is None or (meta.get("missing") and now - meta.get("checked", 0) > RETRY_MISSING_DAYS * 86400):
			todo.append(str(i))
	if not todo:
		return 0
	print(f"fetching metadata for {len(todo)} items from Wowhead (one time, cached)...")
	done = [0]
	lock = threading.Lock()

	def one(i):
		if items.get(i, {}).get("missing"):
			p = os.path.join(CACHE, "tt", i)
			if os.path.exists(p):
				os.remove(p)
		meta = item_meta(i)
		with lock:
			items[i] = meta or {"missing": True, "checked": now}
			done[0] += 1
			if done[0] % 250 == 0 or done[0] == len(todo):
				print(f"  {done[0]}/{len(todo)}")

	with ThreadPoolExecutor(workers) as ex:
		list(ex.map(one, todo))
	return len(todo)

def _listview_rows(page):
	"""Rows of Wowhead's `var listviewitems = [...]` (a JS literal: mostly JSON plus a few bare keys)."""
	m = re.search(r"var listviewitems\s*=\s*\[", page)
	if not m:
		return []
	i = j = m.end() - 1
	depth, instr, esc = 0, False, False
	while j < len(page):
		c = page[j]
		if instr:
			if esc: esc = False
			elif c == "\\": esc = True
			elif c == '"': instr = False
		elif c == '"': instr = True
		elif c == "[": depth += 1
		elif c == "]":
			depth -= 1
			if depth == 0:
				break
		j += 1
	text = re.sub(r'([{,])\s*([A-Za-z_]\w*)\s*:', r'\1"\2":', page[i:j + 1])
	try:
		return json.loads(text)
	except ValueError as e:
		print(f"  could not read a Wowhead list page ({e})", file=sys.stderr)
		return []

def update_classes(items):
	"""Class, subclass and slot for items the tooltip API could not classify, from Wowhead's item list
	pages (one request covers up to 1000 item IDs). Returns the number of items classified."""
	need = sorted(int(i) for i, m in items.items() if m and not m.get("missing") and not m.get("classed"))
	if not need:
		return 0
	print(f"classifying {len(need)} items from Wowhead item lists...")

	def rows_for(lo, hi):
		page = fetch(f"https://www.wowhead.com/forever/items?filter=151:151;2:4;{lo}:{hi}", tries=2)
		rows = _listview_rows(page or "")
		if len(rows) >= 1000 and hi > lo: # Wowhead shows at most 1000 rows: split the range
			mid = (lo + hi) // 2
			return rows_for(lo, mid) + rows_for(mid + 1, hi)
		return rows

	got = 0
	while need:
		lo = need[0]
		hi = lo + 999
		chunk = [i for i in need if i <= hi]
		need = need[len(chunk):]
		try:
			rows = {r.get("id"): r for r in rows_for(lo, hi)}
		except Exception as e:
			print(f"  Wowhead list pages unavailable ({e}); will retry on the next export", file=sys.stderr)
			return got
		if not rows:
			continue # page did not parse; try again next time
		for i in chunk:
			r = rows.get(i)
			m = items[str(i)]
			if r:
				set_class(m, int(r.get("classs", -1)), int(r.get("subclass", -1)), int(r.get("slot") or 0) or None)
				if not m.get("req") and r.get("reqlevel"):
					m["req"] = int(r["reqlevel"])
				got += 1
			m["classed"] = True # not listed at all: keep what the tooltip gave
	return got

def update_icons(items, icon_dir):
	"""Downloads every icon the items use into icon_dir (56x56 jpg). Returns the number downloaded."""
	os.makedirs(icon_dir, exist_ok=True)
	wanted = {m["icon"] for m in items.values() if m and m.get("icon")} | {"inv_misc_questionmark", "inv_misc_coin_01"}
	todo = [n for n in sorted(wanted) if re.fullmatch(r"[a-z0-9_\-]+", n) and not os.path.exists(os.path.join(icon_dir, n + ".jpg"))]
	if not todo:
		return 0
	print(f"downloading {len(todo)} icons...")

	def one(n):
		data = fetch(f"https://wow.zamimg.com/images/wow/icons/large/{n}.jpg", binary=True, polite=False)
		if data:
			with open(os.path.join(icon_dir, n + ".jpg"), "wb") as f:
				f.write(data)

	with ThreadPoolExecutor(8) as ex:
		list(ex.map(one, todo))
	return len(todo)
