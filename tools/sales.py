"""Auction lists, estimated sales and sellers.

Every full scan from the AuctionhouseSync addon carries its auction list: item, stack size, buyout,
seller, time left and current bid per auction. Two scans of the same auction house (faction) in a
row are compared:

	* Auctions are matched on item, stack size, buyout and seller (or without the seller when the
	  name was not known in one of the scans). What is left of the first scan is gone; what is left
	  of the second scan is new.
	* A gone auction was bought out, cancelled or expired. Its time left in the first scan tells how
	  likely it still had time to run: gone while it could not have expired yet counts as bought,
	  partly so when it might have expired. Gone with a bid: if it expired, the bidder won it.
	* The same seller listing the same item again (a repost or undercut) means cancelled, not sold.
	* Joke prices (the troll filter) are left out.

Auctions that are listed and bought between two scans are never seen, so the estimate is a lower
bound and gets better with more scans.

Archive per realm: auctions/<time>-<faction>.json (the auction lists, kept AUCTION_KEEP_DAYS),
sales.json (estimates per day and per scan pair, kept forever) and sellers.json (what each seller
listed, per scan and per item).
"""
import glob
import os
from collections import defaultdict

AUCTION_KEEP_DAYS = 3 # auction lists are big; only needed until the next scan has been compared
MAX_GAP = 24 * 3600 # scans further apart are not compared (everything could have expired)
# time left codes: (shortest, longest) remaining seconds
TIME_LEFT = {1: (0, 1800), 2: (1800, 7200), 3: (7200, 28800), 4: (28800, 86400)}

def parse(auc, owners):
	"""The addon's "id:count:buyout:seller:timeLeft:bid,..." into [[id, count, buyout, seller, timeLeft, bid]]."""
	names = owners.split(",") if owners else []
	out = []
	for part in (auc or "").split(","):
		f = part.split(":")
		if len(f) != 6 or not all(x.isdigit() for x in f):
			continue
		o = int(f[3])
		out.append([int(f[0]), int(f[1]), int(f[2]), names[o - 1] if 0 < o <= len(names) else "", int(f[4]), int(f[5])])
	return out

def still_running(time_left, dt):
	"""Chance an auction with this time left had not expired dt seconds later."""
	lo, hi = TIME_LEFT.get(time_left, (0, 86400))
	if dt <= lo:
		return 1.0
	if dt >= hi:
		return 0.0
	return (hi - dt) / (hi - lo)

def match(A, B):
	"""Auctions of A not in B (gone) and of B not in A (new)."""
	def groups(lst, key):
		g = defaultdict(list)
		for a in lst:
			g[key(a)].append(a)
		return g
	full = lambda a: (a[0], a[1], a[2], a[3])
	ga, gb = groups(A, full), groups(B, full)
	gone, new = [], []
	for k, la in ga.items():
		lb = gb.get(k, [])
		la.sort(key=lambda a: -a[4]) # identical auctions: the ones with the least time left are gone
		gone += la[len(lb):]
		new += lb[len(la):]
	new += [a for k, lb in gb.items() if k not in ga for a in lb]
	# second pass without the seller, where the name was unknown on one side
	short = lambda a: (a[0], a[1], a[2])
	ga, gb = groups(gone, short), groups(new, short)
	gone, new = [], []
	for k in set(ga) | set(gb):
		la, lb = ga.get(k, []), gb.get(k, [])
		la.sort(key=lambda a: -a[4])
		used_b = set()
		left_a = []
		for a in la:
			j = next((j for j, b in enumerate(lb) if j not in used_b and (not a[3] or not b[3])), None)
			if j is None:
				left_a.append(a)
			else:
				used_b.add(j)
		gone += left_a
		new += [b for j, b in enumerate(lb) if j not in used_b]
	return gone, new

def estimate(A, B, dt, ok_price):
	"""Estimated sales from auction list A to B, dt seconds later.
	Returns ({itemID: [units, value, auctions, gone]}, {seller: {itemID: [units, value, auctions]}}, new auctions).
	ok_price(itemID, unit price) is False for joke prices."""
	A = [a for a in A if ok_price(a[0], (a[2] or a[5]) / max(1, a[1]))]
	B = [b for b in B if ok_price(b[0], (b[2] or b[5]) / max(1, b[1]))]
	gone, new = match(A, B)
	reposts = defaultdict(int)
	for b in new:
		if b[3]:
			reposts[(b[3], b[0])] += 1
	items, sellers = {}, defaultdict(dict)
	# a seller listing the item again: the gone auctions most likely to have still been running were cancelled
	gone.sort(key=lambda a: -still_running(a[4], dt))
	for item_id, count, buyout, seller, time_left, bid in gone:
		rec = items.setdefault(item_id, [0.0, 0.0, 0.0, 0])
		rec[3] += 1
		p = still_running(time_left, dt)
		if bid:
			# bought out while running, or won by the bidder when it expired (no buyout: only that)
			w = 1.0 if buyout else 1 - p
			value = p * buyout + (1 - p) * bid if buyout else (1 - p) * bid
		elif buyout and seller and reposts[(seller, item_id)] > 0:
			reposts[(seller, item_id)] -= 1
			continue
		elif buyout:
			w, value = p, p * buyout
		else:
			continue
		if w <= 0:
			continue
		rec[0] += w * count
		rec[1] += value
		rec[2] += w
		if seller:
			s = sellers[seller].setdefault(item_id, [0.0, 0.0, 0.0])
			s[0] += w * count
			s[1] += value
			s[2] += w
	return items, sellers, len(new)

def add(acc, vals):
	for i, v in enumerate(vals):
		acc[i] = round(acc[i] + v, 2)

class Store:
	"""Auction lists, sales and sellers of one realm (in its archive folder)."""
	def __init__(self, folder, load_json, save_json, scan_day):
		self.dir = folder
		self.load_json, self.save_json, self.scan_day = load_json, save_json, scan_day
		self.sales = load_json(os.path.join(folder, "sales.json"), {"pairs": {}, "days": {}, "cover": {}, "sellers": {}})
		self.sellers = load_json(os.path.join(folder, "sellers.json"), {})
		self.dirty = False

	def lists(self):
		"""Stored auction lists, oldest first: [(time, faction, path)]."""
		out = []
		for path in glob.glob(os.path.join(self.dir, "auctions", "*.json")):
			t, _, faction = os.path.basename(path)[:-5].partition("-")
			if t.isdigit():
				out.append((int(t), faction, path))
		return sorted(out)

	def add_list(self, t, faction, auctions, ok_price):
		"""Stores a scan's auction list and what each seller listed. False when it was stored before."""
		path = os.path.join(self.dir, "auctions", f"{t}-{faction or 'none'}.json")
		if os.path.exists(path) or any(x[0] == t for x in self.lists()):
			return False
		self.save_json(path, auctions)
		key = str(t)
		for item_id, count, buyout, seller, _, bid in auctions:
			if not seller:
				continue
			s = self.sellers.setdefault(seller, {"first": t, "last": t, "hist": {}, "items": {}})
			s["first"], s["last"] = min(s["first"], t), max(s["last"], t)
			h = s["hist"].setdefault(key, [0, 0]) # per scan: [auctions, listed value]
			h[0] += 1
			unit = (buyout or bid) / max(1, count)
			it = s["items"].setdefault(str(item_id), [0, t, 0]) # [scans seen in, last seen, last unit price]
			if it[1] != t or it[0] == 0:
				it[0] += 1
			it[1] = max(it[1], t)
			if ok_price(item_id, unit):
				h[1] += buyout or bid
				it[2] = round(unit)
		self.dirty = True
		return True

	def update(self, ok_price):
		"""Compares every stored auction list with the previous one of the same faction (once)."""
		by_faction = defaultdict(list)
		for t, faction, path in self.lists():
			by_faction[faction].append((t, path))
		pairs = self.sales["pairs"]
		for faction, scans in by_faction.items():
			for (t0, p0), (t1, p1) in zip(scans, scans[1:]):
				key = f"{t0}-{t1}"
				if key in pairs or t1 - t0 > MAX_GAP:
					continue
				items, sellers, new = estimate(self.load_json(p0, []), self.load_json(p1, []), t1 - t0, ok_price)
				day = str(self.scan_day(t1))
				days = self.sales["days"].setdefault(day, {})
				tot = [0.0, 0.0, 0.0, 0]
				for item_id, vals in items.items():
					add(days.setdefault(str(item_id), [0, 0, 0, 0]), vals)
					add(tot, vals)
				for seller, its in sellers.items():
					sd = self.sales["sellers"].setdefault(seller, {}).setdefault(day, [0, 0, 0])
					for vals in its.values():
						add(sd, vals)
				cover = self.sales["cover"].setdefault(day, {})
				cover[faction] = min(86400, cover.get(faction, 0) + t1 - t0)
				pairs[key] = [faction, t0, t1, round(tot[0], 2), round(tot[1]), round(tot[2], 2), tot[3], new]
				self.dirty = True

	def prune(self, keep_scan_days):
		"""Drops old auction lists and per-scan seller history (sales per day are kept forever)."""
		lists = self.lists()
		if lists:
			newest = lists[-1][0]
			# keep the newest list per faction, it is compared with the next scan
			newest_per = {}
			for t, faction, path in lists:
				newest_per[faction] = path
			for t, faction, path in lists:
				if t < newest - AUCTION_KEEP_DAYS * 86400 and path not in newest_per.values():
					os.remove(path)
			cutoff = newest - keep_scan_days * 86400
			for s in self.sellers.values():
				old = [t for t in s["hist"] if int(t) < cutoff]
				for t in old:
					del s["hist"][t]
				self.dirty = self.dirty or bool(old)

	def save(self):
		if self.dirty:
			self.save_json(os.path.join(self.dir, "sales.json"), self.sales)
			self.save_json(os.path.join(self.dir, "sellers.json"), self.sellers)
			self.dirty = False

	def latest(self, max_age=2 * 86400):
		"""The newest auction list of each faction (no older than max_age before the newest): [(time, faction, auctions)]."""
		lists = self.lists()
		if not lists:
			return []
		newest = lists[-1][0]
		per = {}
		for t, faction, path in lists:
			if t >= newest - max_age:
				per[faction] = (t, path)
		return [(t, f, self.load_json(path, [])) for f, (t, path) in per.items()]
