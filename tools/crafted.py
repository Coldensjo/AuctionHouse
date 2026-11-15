"""Which profession crafts each item, for the Browse "Profession" filter.

Run by hand to rebuild crafted.json from a VMaNGOS world database dump (mangos.sql):

	python tools/crafted.py [path\\to\\mangos.sql]

It reads the crafting spells (skill_line_ability + spell_template, latest client build of each) and keeps
the item each spell creates. export.py adds what the recipe tooltips teach on top (see from_recipes), which
covers recipes this server added after 1.12.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "crafted.json")
DEFAULT_DUMP = r"C:\WoW\vmangos\db-dump\mysql-dump\mangos.sql"
CREATE_ITEM = 24 # spell effect
PROFESSIONS = {
	171: "Alchemy", 164: "Blacksmithing", 185: "Cooking", 333: "Enchanting", 202: "Engineering",
	129: "First Aid", 165: "Leatherworking", 186: "Mining", 197: "Tailoring",
}
SKILL_NAMES = {n.lower().replace(" ", "-"): n for n in PROFESSIONS.values()}

def _rows(values):
	"""The rows of one INSERT ... VALUES (...),(...); line, as lists of strings."""
	i, n = 0, len(values)
	while i < n:
		if values[i] != "(":
			i += 1
			continue
		i += 1
		row, cur, quoted = [], [], False
		while i < n:
			ch = values[i]
			if quoted:
				if ch == "\\":
					cur.append(values[i + 1])
					i += 2
					continue
				if ch == "'":
					if values[i + 1:i + 2] == "'":
						cur.append("'")
						i += 2
						continue
					quoted = False
				else:
					cur.append(ch)
				i += 1
				continue
			if ch == "'":
				quoted = True
			elif ch in ",)":
				row.append("".join(cur))
				cur = []
				if ch == ")":
					i += 1
					break
			else:
				cur.append(ch)
			i += 1
		yield row

def read_tables(path, wanted):
	"""{table: [row dict]} for the wanted tables of a mysqldump file."""
	cols, rows, table = {}, {t: [] for t in wanted}, None
	with open(path, encoding="utf-8", errors="replace") as f:
		for line in f:
			if m := re.match(r"CREATE TABLE `(\w+)`", line):
				table = m.group(1) if m.group(1) in wanted else None
				if table:
					cols[table] = []
			elif table and line.startswith("  `"):
				cols[table].append(line.split("`")[1])
			elif (m := re.match(r"INSERT INTO `(\w+)` VALUES ", line)) and m.group(1) in wanted:
				t = m.group(1)
				rows[t].extend(dict(zip(cols[t], r)) for r in _rows(line[m.end():]))
	return rows

def latest(rows, key):
	"""Keep the row of the newest client build for each key."""
	out = {}
	for r in rows:
		k = r[key]
		if k not in out or int(r["build"]) > int(out[k]["build"]):
			out[k] = r
	return out

def build(dump):
	t = read_tables(dump, {"skill_line_ability", "spell_template"})
	spells = latest(t["spell_template"], "entry")
	crafted = {}
	for r in latest(t["skill_line_ability"], "id").values():
		prof = PROFESSIONS.get(int(r["skill_id"]))
		sp = spells.get(r["spell_id"])
		if not prof or not sp:
			continue
		for n in (1, 2, 3):
			item = int(sp[f"effectItemType{n}"] or 0)
			if int(sp[f"effect{n}"] or 0) == CREATE_ITEM and item:
				crafted.setdefault(str(item), set()).add(prof)
	return {k: sorted(v) for k, v in sorted(crafted.items(), key=lambda kv: int(kv[0]))}

def load():
	"""{itemID string: [profession, ...]} from crafted.json (empty when it was never built)."""
	try:
		with open(OUT, encoding="utf-8") as f:
			return json.load(f)
	except FileNotFoundError:
		return {}

TEACHES = re.compile(r"Teaches you how to (?:craft|cook|make|create|sew|smelt|forge|brew|build|construct|engineer|enchant)?\s*(?:an? |the )?(.+?)\.?&quot;")
REQUIRES = re.compile(r'Requires <a href="/forever/skill=\d+/([a-z-]+)')

def from_recipes(items, cache_dir, known):
	"""{itemID string: profession} for items a recipe teaches, matched by name from the cached recipe tooltips.

	When several items share the name, the ones `known` (crafted.json) gives to another profession are left
	out, and a name that is still ambiguous is skipped.
	"""
	by_name = {}
	for i, m in items.items():
		if m and not m.get("missing") and m.get("c") != 9:
			by_name.setdefault(m["name"].lower(), []).append(i)
	out = {}
	for i, m in items.items():
		if not m or m.get("c") != 9:
			continue
		try:
			with open(os.path.join(cache_dir, i), encoding="utf-8") as f:
				tt = json.load(f).get("tooltip", "")
		except (OSError, ValueError):
			continue
		prof, what = REQUIRES.search(tt), TEACHES.search(tt)
		if not (prof and what and prof.group(1) in SKILL_NAMES):
			continue
		name = SKILL_NAMES[prof.group(1)]
		made = by_name.get(what.group(1).lower(), [])
		if len(made) > 1:
			made = [j for j in made if name in known.get(j, [name])]
		if len(made) == 1:
			out[made[0]] = name
	return out

if __name__ == "__main__":
	data = build(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DUMP)
	with open(OUT, "w", encoding="utf-8") as f:
		json.dump(data, f, separators=(",", ":"))
	print(f"{len(data)} crafted items written to {OUT}")
