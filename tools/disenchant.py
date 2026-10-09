"""Disenchant tables, ported from the DisenchantValue addon so the site and the tooltip agree.

era_table(cls, quality, ilvl) gives the Classic Era drop table; learned buckets come from
DisenchantValueDB (what you actually got from your own disenchants).
"""
WEAPON, ARMOR = 2, 4
BRACKET_SIZE, MAX_BRACKET_DISTANCE = 5, 2

STRANGE_DUST, SOUL_DUST, VISION_DUST, DREAM_DUST, ILLUSION_DUST = 10940, 11083, 11137, 11176, 16204
LESSER_MAGIC, GREATER_MAGIC = 10938, 10939
LESSER_ASTRAL, GREATER_ASTRAL = 10998, 11082
LESSER_MYSTIC, GREATER_MYSTIC = 11134, 11135
LESSER_NETHER, GREATER_NETHER = 11174, 11175
LESSER_ETERNAL, GREATER_ETERNAL = 16202, 16203
SMALL_GLIMMERING, LARGE_GLIMMERING = 10978, 11084
SMALL_GLOWING, LARGE_GLOWING = 11138, 11139
SMALL_RADIANT, LARGE_RADIANT = 11177, 11178
SMALL_BRILLIANT, LARGE_BRILLIANT = 14343, 14344
NEXUS_CRYSTAL = 20725

MATERIALS = [
	STRANGE_DUST, SOUL_DUST, VISION_DUST, DREAM_DUST, ILLUSION_DUST,
	LESSER_MAGIC, GREATER_MAGIC, LESSER_ASTRAL, GREATER_ASTRAL, LESSER_MYSTIC, GREATER_MYSTIC,
	LESSER_NETHER, GREATER_NETHER, LESSER_ETERNAL, GREATER_ETERNAL,
	SMALL_GLIMMERING, LARGE_GLIMMERING, SMALL_GLOWING, LARGE_GLOWING,
	SMALL_RADIANT, LARGE_RADIANT, SMALL_BRILLIANT, LARGE_BRILLIANT, NEXUS_CRYSTAL,
]

# { minIlvl, maxIlvl, dust%, essence%, shard%, dust, dustMin, dustMax, essence, essMin, essMax, shard }
UNCOMMON = [
	(1, 15, 80, 20, 0, STRANGE_DUST, 1, 2, LESSER_MAGIC, 1, 2, None),
	(16, 20, 75, 20, 5, STRANGE_DUST, 2, 3, GREATER_MAGIC, 1, 2, SMALL_GLIMMERING),
	(21, 25, 75, 15, 10, STRANGE_DUST, 4, 6, LESSER_ASTRAL, 1, 2, SMALL_GLIMMERING),
	(26, 30, 75, 20, 5, SOUL_DUST, 1, 2, GREATER_ASTRAL, 1, 2, LARGE_GLIMMERING),
	(31, 35, 75, 20, 5, SOUL_DUST, 2, 5, LESSER_MYSTIC, 1, 2, SMALL_GLOWING),
	(36, 40, 75, 20, 5, VISION_DUST, 1, 2, GREATER_MYSTIC, 1, 2, LARGE_GLOWING),
	(41, 45, 75, 20, 5, VISION_DUST, 2, 5, LESSER_NETHER, 1, 2, SMALL_RADIANT),
	(46, 50, 75, 20, 5, DREAM_DUST, 1, 2, GREATER_NETHER, 1, 2, LARGE_RADIANT),
	(51, 55, 75, 20, 5, DREAM_DUST, 2, 5, LESSER_ETERNAL, 1, 2, SMALL_BRILLIANT),
	(56, 60, 75, 20, 5, ILLUSION_DUST, 1, 2, GREATER_ETERNAL, 1, 2, LARGE_BRILLIANT),
	(61, 999, 75, 20, 5, ILLUSION_DUST, 2, 5, GREATER_ETERNAL, 2, 3, LARGE_BRILLIANT),
]

def _uncommon(is_weapon):
	rows = []
	for u in UNCOMMON:
		dust, ess = (u[3], u[2]) if is_weapon else (u[2], u[3])
		row = [u[0], u[1], (dust, u[6], u[7], u[5]), (ess, u[9], u[10], u[8])]
		if u[4] > 0:
			row.append((u[4], 1, 1, u[11]))
		rows.append(row)
	return rows

# Rows: [minIlvl, maxIlvl, (chance%, minQty, maxQty, itemID), ...]
RARE = [
	[1, 25, (100, 1, 1, SMALL_GLIMMERING)],
	[26, 30, (100, 1, 1, LARGE_GLIMMERING)],
	[31, 35, (100, 1, 1, SMALL_GLOWING)],
	[36, 40, (100, 1, 1, LARGE_GLOWING)],
	[41, 45, (100, 1, 1, SMALL_RADIANT)],
	[46, 50, (100, 1, 1, LARGE_RADIANT)],
	[51, 55, (100, 1, 1, SMALL_BRILLIANT)],
	[56, 999, (99.5, 1, 1, LARGE_BRILLIANT), (0.5, 1, 1, NEXUS_CRYSTAL)],
]
EPIC = [
	[1, 45, (100, 2, 4, SMALL_RADIANT)],
	[46, 50, (100, 2, 4, LARGE_RADIANT)],
	[51, 55, (100, 2, 4, SMALL_BRILLIANT)],
	[56, 60, (100, 1, 1, NEXUS_CRYSTAL)],
	[61, 999, (100, 1, 2, NEXUS_CRYSTAL)],
]
ERA = {
	ARMOR: {2: _uncommon(False), 3: RARE, 4: EPIC},
	WEAPON: {2: _uncommon(True), 3: RARE, 4: EPIC},
}

def disenchantable(meta):
	return bool(meta) and meta.get("c") in (WEAPON, ARMOR) and 2 <= meta.get("q", 0) <= 4 and meta.get("ilvl", 0) > 0

def bracket(ilvl):
	return 15 if ilvl <= 15 else -(-ilvl // BRACKET_SIZE) * BRACKET_SIZE

def era_drops(meta):
	"""[(itemID, expectedQty, chance%, minQty, maxQty)] or None."""
	if not disenchantable(meta):
		return None
	for row in ERA[meta["c"]][meta["q"]]:
		if row[0] <= meta["ilvl"] <= row[1]:
			return [(i, chance / 100 * (lo + hi) / 2, chance, lo, hi) for chance, lo, hi, i in row[2:]]
	return None

def learned_drops(meta, buckets):
	"""[(itemID, expectedQty)], samples, approximate — from DisenchantValueDB buckets (nearest bracket fallback)."""
	if not disenchantable(meta) or not buckets:
		return None, 0, False
	b = bracket(meta["ilvl"])
	prefix = f"{meta['c']}:{meta['q']}:"
	rec = buckets.get(prefix + str(b))
	approx = False
	if not rec or not rec.get("n"):
		best = None
		for k, r in buckets.items():
			if r.get("n") and k.startswith(prefix):
				d = abs(int(k[len(prefix):]) - b)
				if d <= MAX_BRACKET_DISTANCE * BRACKET_SIZE and (best is None or d < best[0]):
					best = (d, r)
		if not best:
			return None, 0, False
		rec, approx = best[1], True
	n = rec["n"]
	return [(int(i), q / n) for i, q in rec.get("items", {}).items()], n, approx

def value(drops, price):
	"""Expected copper from drops [(itemID, qty, ...)] with price(itemID) -> copper or None."""
	total, partial = 0.0, False
	for d in drops:
		p = price(d[0])
		if p:
			total += d[1] * p
		else:
			partial = True
	return int(total), partial
