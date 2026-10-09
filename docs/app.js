"use strict";
// Auction House: static site over Auctionator scan data. No dependencies.

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ESC[c]);
const PAGE = 50;
const CLASS_ORDER = [2, 4, 1, 0, 7, 6, 11, 9, 5, 3, 15, 12, 13, -1];
const QUALITY = ["Poor", "Common", "Uncommon", "Rare", "Epic", "Legendary", "Artifact", "Heirloom"];

const S = {
	meta: null, // realms.json
	items: null, // items.json lookup: id -> [name, q, icon, c, s, slot, ilvl, req, sell, buy]
	realm: null, // current realm summary
	data: {}, // slug -> { list, byId, today }
	hist: {}, // slug/shard -> { id: [[day, low, high, avail]] }
	tt: {}, // shard -> { id: html }
	posting: null,
	de: null,
	market: {},
	lists: {}, // per-view list state (sort, page)
};

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

const cacheKey = () => (S.meta ? S.meta.generated : Date.now());
async function getJSON(path) {
	const r = await fetch(`data/${path}?v=${cacheKey()}`);
	if (!r.ok) throw new Error(`${path}: ${r.status}`);
	return r.json();
}
const once = {};
function load(key, fn) {
	return once[key] || (once[key] = fn().catch(e => { delete once[key]; throw e; }));
}

async function loadRealm(slug) {
	return load("realm:" + slug, async () => {
		const idx = await getJSON(`${slug}/index.json`);
		const cols = idx.cols, list = [], byId = new Map();
		for (const row of idx.rows) {
			const it = {};
			cols.forEach((c, i) => (it[c] = row[i]));
			Object.assign(it, itemMeta(it.id));
			it.inScan = it.last === idx.today;
			it.deProfit = it.de && it.inScan ? it.de - it.cur : null;
			it.flip = it.sell && it.inScan ? it.sell - it.cur : null;
			list.push(it);
			byId.set(it.id, it);
		}
		return (S.data[slug] = { list, byId, today: idx.today });
	});
}

function itemMeta(id) {
	const m = S.items.items[id];
	if (!m) return { name: `Item #${id}`, q: 1, icon: "inv_misc_questionmark", c: -1, s: -1, slot: 0, ilvl: 0, req: 0, sell: 0, buy: 0 };
	const [name, q, icon, c, s, slot, ilvl, req, sell, buy] = m;
	return { name, q, icon, c, s, slot, ilvl, req, sell, buy, lname: name.toLowerCase() };
}

const shardOf = id => id % S.meta.shards;
async function itemHistory(id) {
	const key = `${S.realm.slug}/${shardOf(id)}`;
	if (!S.hist[key]) S.hist[key] = await load("h:" + key, () => getJSON(`${S.realm.slug}/h/${shardOf(id)}.json`));
	return S.hist[key][id] || [];
}
async function tooltipHtml(id) {
	const n = shardOf(id);
	if (!S.tt[n]) S.tt[n] = await load("tt:" + n, () => getJSON(`tt/${n}.json`));
	return S.tt[n][id];
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function money(c, cls = "") {
	if (c == null || isNaN(c)) return `<span class="muted">-</span>`;
	const neg = c < 0;
	c = Math.round(Math.abs(c));
	const g = Math.floor(c / 10000), s = Math.floor((c % 10000) / 100), k = c % 100;
	if (g >= 100000) { // joke listings (millions of gold): shorten so the column keeps its width
		const short = g >= 1e6 ? (g / 1e6).toFixed(g >= 1e7 ? 0 : 1) + "M" : Math.round(g / 1000) + "k";
		return `<span class="money ${neg ? "neg" : ""} ${cls}" title="${g.toLocaleString()} gold">${neg ? "-" : ""}${short}<i class="g"></i></span>`;
	}
	let h = "";
	if (g) h += `${g.toLocaleString()}<i class="g"></i>`;
	// Lower coins are always shown once a higher one is (5g 00s 00c), except that from 1,000g copper is
	// left out and from 10,000g silver too: small change at that size, and it keeps columns narrow.
	if (g < 10000 && (g || s)) h += `${g ? String(s).padStart(2, "0") : s}<i class="s"></i>`;
	if (g < 1000) h += `${(g || s) ? String(k).padStart(2, "0") : k}<i class="c"></i>`;
	return `<span class="money ${neg ? "neg" : ""} ${cls}">${neg ? "-" : ""}${h}</span>`;
}
function moneyText(c) {
	if (c == null) return "-";
	const a = Math.abs(c), sign = c < 0 ? "-" : "";
	if (a >= 1e10) return sign + (a / 1e10).toFixed(1) + "M g";
	if (a >= 1e7) return sign + (a / 1e7).toFixed(a >= 1e8 ? 0 : 1) + "k g";
	if (a >= 10000) return sign + (a / 1e4).toFixed(a >= 1e6 ? 0 : 1).replace(/\.0$/, "") + "g";
	if (a >= 100) return sign + (a / 100).toFixed(a >= 1000 ? 0 : 1).replace(/\.0$/, "") + "s";
	return sign + Math.round(a) + "c";
}
function pct(v, invert = false) {
	if (v == null) return `<span class="muted">-</span>`;
	const good = invert ? v < 0 : v > 0;
	const cls = v === 0 ? "" : good ? "up" : "down";
	return `<span class="${cls}">${v > 0 ? "+" : ""}${v.toFixed(Math.abs(v) >= 100 ? 0 : 1)}%</span>`;
}
const num = n => (n == null ? `<span class="muted">-</span>` : Math.round(n).toLocaleString());
const dayDate = d => new Date((S.meta.day0 + d * 86400 + 43200) * 1000);
const dayLabel = (d, long) => dayDate(d).toLocaleDateString(undefined, long ? { weekday: "short", year: "numeric", month: "short", day: "numeric" } : { month: "short", day: "numeric" });
function ago(day) {
	const n = S.data[S.realm.slug].today - day;
	return n <= 0 ? `<span class="up">Latest scan</span>` : n === 1 ? "1 day ago" : `${n} days ago`;
}
function timeAgo(ts) {
	const s = Date.now() / 1000 - ts;
	if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
	if (s < 86400) return `${Math.round(s / 3600)} h ago`;
	return `${Math.round(s / 86400)} days ago`;
}
const icon = (it, size = "") => `<span class="ic ${size} q${it.q}"><img loading="lazy" src="icons/${esc(it.icon)}.jpg" alt="" onerror="this.onerror=null;this.src='icons/inv_misc_questionmark.jpg'"></span>`;
const itemLink = (it, size = "") => `<a class="iname q${it.q}" href="${href("item", it.id)}" data-tip="${it.id}">${icon(it, size)}<span class="nm">${esc(it.name)}</span></a>`;
const className = it => (S.items.classes[it.c] || "Unknown");
const subName = it => S.items.subclasses[`${it.c}:${it.s}`] || "";

// ---------------------------------------------------------------------------
// Routing: #/<realm>/<view>/<arg>?<params>
// ---------------------------------------------------------------------------

function parseHash() {
	const [path, qs] = location.hash.replace(/^#\/?/, "").split("?");
	const segs = path.split("/").filter(Boolean).map(decodeURIComponent);
	let realm = S.meta.realms.reduce((a, b) => (b.lastScan > a.lastScan ? b : a)); // most recently scanned
	const r = S.meta.realms.find(x => x.slug === segs[0]);
	if (r) { realm = r; segs.shift(); }
	return { realm, view: segs[0] || "browse", arg: segs[1], params: new URLSearchParams(qs || "") };
}
function href(view, arg, params) {
	const qs = params && [...params].length ? "?" + params : "";
	return `#/${S.realm.slug}/${view}${arg != null ? "/" + encodeURIComponent(arg) : ""}${qs}`;
}

const VIEWS = { browse, item: itemView, market, deals, disenchant, flips, recipes };
let routeToken = 0;
async function route() {
	const { realm, view, arg, params } = parseHash();
	const token = ++routeToken;
	S.realm = realm;
	$("#realm").value = realm.slug;
	$("#scan-info").innerHTML = `Last scan <b>${timeAgo(realm.lastScan)}</b><br>${realm.items.toLocaleString()} items &middot; ${realm.days} days of history`;
	const tab = view === "item" ? null : view;
	$$("#tabs a").forEach(a => {
		a.classList.toggle("on", a.dataset.view === tab);
		a.href = href(a.dataset.view);
	});
	hideTip();
	const el = $("#view");
	try {
		await loadRealm(realm.slug);
		if (token !== routeToken) return;
		await (VIEWS[view] || browse)(el, params, arg, token);
	} catch (e) {
		console.error(e);
		el.innerHTML = `<div class="loading">Something went wrong: ${esc(e.message)}</div>`;
	}
	if (view !== "browse" || !params.toString()) window.scrollTo(0, 0);
}

// ---------------------------------------------------------------------------
// Sortable, paged list
// ---------------------------------------------------------------------------

const COLS = {
	item: { label: "Item", sort: it => it.name, cls: "item", fmt: it => itemLink(it) },
	req: { label: "Lvl", sort: it => it.req, cls: "r", fmt: it => it.req || "" },
	ilvl: { label: "iLvl", sort: it => it.ilvl, cls: "r", fmt: it => it.ilvl || "" },
	av: { label: "Avail", title: "Quantity listed in the latest scan it was seen in", sort: it => it.av, cls: "r", fmt: it => num(it.av) },
	cur: { label: "Price", title: "Lowest buyout in the most recent scan", sort: it => it.cur, cls: "r", fmt: it => money(it.cur) },
	a7: { label: "7d Avg", sort: it => it.a7, cls: "r", fmt: it => money(it.a7) },
	a30: { label: "30d Avg", sort: it => it.a30, cls: "r", fmt: it => money(it.a30) },
	all: { label: "All-time Avg", sort: it => it.all, cls: "r", fmt: it => money(it.all) },
	min: { label: "Lowest", sort: it => it.min, cls: "r", fmt: it => money(it.min) },
	max: { label: "Highest", sort: it => it.max, cls: "r", fmt: it => money(it.max) },
	vs30: { label: "vs 30d", title: "Current price compared to the 30 day average", sort: it => it.vs30, cls: "r", fmt: it => pct(it.vs30) },
	wk: { label: "7d Trend", title: "Price change over the last ~7 days", sort: it => it.wk, cls: "r", fmt: it => pct(it.wk) },
	chg: { label: "Day", title: "Change since the previous day it was seen", sort: it => it.chg, cls: "r", fmt: it => pct(it.chg) },
	vol: { label: "Volatility", title: "Standard deviation of daily prices, as % of the average", sort: it => it.vol, cls: "r", fmt: it => (it.seen > 1 ? it.vol.toFixed(0) + "%" : `<span class="muted">-</span>`) },
	seen: { label: "Days", title: "Days this item has been seen on the auction house", sort: it => it.seen, cls: "r", fmt: it => it.seen },
	last: { label: "Seen", sort: it => it.last, cls: "r", fmt: it => ago(it.last) },
	de: { label: "Disenchant", title: "Expected disenchant value at current material prices (era table)", sort: it => it.de, cls: "r", fmt: it => money(it.de) },
	deProfit: { label: "DE Profit", sort: it => it.deProfit, cls: "r", fmt: it => money(it.deProfit) },
	sell: { label: "Vendor", title: "Vendor sell price", sort: it => it.sell, cls: "r", fmt: it => (it.sell ? money(it.sell) : `<span class="muted">-</span>`) },
	flip: { label: "Profit", title: "Vendor price minus auction price", sort: it => it.flip, cls: "r", fmt: it => money(it.flip) },
	value: { label: "Listed Value", title: "Price x quantity available", sort: it => it.cur * it.av, cls: "r", fmt: it => money(it.cur * it.av) },
	discount: { label: "Discount", title: "Below the 30 day average", sort: it => -it.vs30, cls: "r", fmt: it => pct(it.vs30, true) },
	gain: { label: "Potential", title: "30 day average minus current price", sort: it => it.a30 - it.cur, cls: "r", fmt: it => money(it.a30 - it.cur) },
};

// Fixed column widths, so sorting or a very long price never shifts the columns. The item column takes the rest.
const MONEY_COLS = new Set(["cur", "a7", "a30", "all", "min", "max", "de", "deProfit", "sell", "flip", "value", "gain", "vprice", "vdiff", "buy", "price", "total"]);
const PCT_COLS = new Set(["vs30", "wk", "chg", "discount", "vmargin", "markup", "vsnow"]);
const COL_WIDTHS = { item: 0, req: 44, ilvl: 50, av: 64, seen: 56, last: 96, vol: 84, prof: 116, npcs: 270, posted: 176, qty: 52 };
function colWidth(c) {
	if (c in COL_WIDTHS) return COL_WIDTHS[c];
	if (MONEY_COLS.has(c)) return 132;
	if (PCT_COLS.has(c)) return 78;
	return 110;
}

function list(el, key, data, cols, opts = {}) {
	const st = S.lists[key] || (S.lists[key] = { sort: opts.sort || cols[0], dir: opts.dir ?? -1, page: 0 });
	if (opts.resetPage) st.page = 0;
	const col = COLS[st.sort] || COLS[cols[0]];
	const sorted = data.slice().sort((a, b) => {
		const x = col.sort(a), y = col.sort(b);
		if (x == null && y == null) return 0;
		if (x == null) return 1;
		if (y == null) return -1;
		return (typeof x === "string" ? x.localeCompare(y) : x - y) * st.dir;
	});
	const size = opts.pageSize || PAGE;
	const pages = Math.max(1, Math.ceil(sorted.length / size));
	st.page = Math.min(st.page, pages - 1);
	const rows = sorted.slice(st.page * size, st.page * size + size);
	const widths = cols.map(colWidth);
	const colgroup = `<colgroup>${widths.map(w => `<col${w ? ` style="width:${w}px"` : ""}>`).join("")}</colgroup>`;
	const minWidth = widths.reduce((a, w) => a + (w || 180), 0);
	const head = cols.map(c => {
		const d = COLS[c];
		const cls = [d.cls === "r" ? "r" : "", c === st.sort ? "sorted" : "", c === st.sort && st.dir > 0 ? "asc" : ""].join(" ");
		return `<th class="${cls}" data-sort="${c}" title="${esc(d.title || "")}">${d.label}</th>`;
	}).join("");
	const body = rows.length
		? rows.map(it => `<tr data-id="${it.id}">${cols.map(c => `<td class="${COLS[c].cls}">${COLS[c].fmt(it)}</td>`).join("")}</tr>`).join("")
		: `<tr class="empty"><td colspan="${cols.length}">${opts.empty || "No items match."}</td></tr>`;
	const pager = opts.noPager ? "" : `<div class="pager"><span>${sorted.length ? `Items ${(st.page * size + 1).toLocaleString()}-${Math.min(sorted.length, (st.page + 1) * size).toLocaleString()} of ${sorted.length.toLocaleString()}` : ""}</span>
		<span class="seg"><button class="btn small" data-page="-1" ${st.page ? "" : "disabled"}>&lt; Prev</button>
		<span style="padding:0 8px">Page ${st.page + 1} of ${pages}</span>
		<button class="btn small" data-page="1" ${st.page < pages - 1 ? "" : "disabled"}>Next &gt;</button></span></div>`;
	el.innerHTML = `<div class="tbl-wrap"><table class="list fixed" style="min-width:${minWidth}px">${colgroup}<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${pager}`;
	el.onclick = e => {
		const th = e.target.closest("th[data-sort]");
		if (th) {
			const c = th.dataset.sort;
			st.dir = st.sort === c ? -st.dir : (typeof COLS[c].sort(data[0] || {}) === "string" ? 1 : -1);
			st.sort = c;
			st.page = 0;
			opts.onChange?.(st);
			return list(el, key, data, cols, opts);
		}
		const pg = e.target.closest("[data-page]");
		if (pg) {
			st.page += +pg.dataset.page;
			opts.onChange?.(st);
			list(el, key, data, cols, opts);
			el.scrollIntoView({ block: "start", behavior: "smooth" });
			return;
		}
		const tr = e.target.closest("tr[data-id]");
		if (tr && !e.target.closest("a")) location.hash = href("item", tr.dataset.id);
	};
}

// ---------------------------------------------------------------------------
// Browse
// ---------------------------------------------------------------------------

async function browse(el, params) {
	const D = S.data[S.realm.slug];
	const st = S.lists.browse || (S.lists.browse = { sort: "value", dir: -1, page: 0 });
	if (params.get("sort")) { st.sort = params.get("sort"); st.dir = +params.get("dir") || -1; }
	st.page = +params.get("page") || 0;
	const f = {
		q: params.get("q") || "", c: params.get("c") ?? "", s: params.get("s") ?? "",
		lmin: params.get("lmin") || "", lmax: params.get("lmax") || "", qmin: params.get("qmin") || "",
		pmin: params.get("pmin") || "", pmax: params.get("pmax") || "", now: params.get("now") === "1",
	};

	// category tree with counts
	const counts = {};
	for (const it of D.list) {
		counts[it.c] = (counts[it.c] || 0) + 1;
		counts[`${it.c}:${it.s}`] = (counts[`${it.c}:${it.s}`] || 0) + 1;
	}
	let cats = `<a data-c="" class="${f.c === "" ? "on" : ""}">All Items<span class="count">${D.list.length}</span></a>`;
	for (const c of CLASS_ORDER) {
		if (!counts[c]) continue;
		cats += `<a data-c="${c}" class="${String(c) === f.c && f.s === "" ? "on" : ""}">${esc(S.items.classes[c] || "Unknown")}<span class="count">${counts[c]}</span></a>`;
		if (String(c) === f.c) {
			const subs = Object.keys(S.items.subclasses).filter(k => k.startsWith(c + ":") && counts[k])
				.sort((a, b) => S.items.subclasses[a].localeCompare(S.items.subclasses[b]));
			for (const k of subs) {
				const s = k.split(":")[1];
				cats += `<a class="sub ${f.s === s ? "on" : ""}" data-c="${c}" data-s="${s}">${esc(S.items.subclasses[k])}<span class="count">${counts[k]}</span></a>`;
			}
		}
	}

	el.innerHTML = `
		<form class="filters" id="filters" autocomplete="off">
			<div class="f name"><span>Name</span><input name="q" type="search" value="${esc(f.q)}" placeholder="Item name or ID"></div>
			<div class="f"><span>Level Range</span><div class="range"><input name="lmin" type="number" min="0" max="80" value="${esc(f.lmin)}"> - <input name="lmax" type="number" min="0" max="80" value="${esc(f.lmax)}"></div></div>
			<div class="f"><span>Rarity</span><select name="qmin"><option value="">All</option>${QUALITY.slice(0, 6).map((n, i) => `<option value="${i}" class="q${i}" ${f.qmin === String(i) ? "selected" : ""}>${n}${i < 5 ? " +" : ""}</option>`).join("")}</select></div>
			<div class="f"><span>Price (gold)</span><div class="range"><input name="pmin" type="number" min="0" step="any" value="${esc(f.pmin)}"> - <input name="pmax" type="number" min="0" step="any" value="${esc(f.pmax)}"></div></div>
			<div class="f"><label class="chk"><input name="now" type="checkbox" ${f.now ? "checked" : ""}> On the AH now</label></div>
			<div class="f"><button type="button" class="btn" id="reset">Reset</button></div>
		</form>
		<div class="browse">
			<div class="cats box" id="cats">${cats}</div>
			<div id="results"></div>
		</div>`;

	const form = $("#filters", el);
	function apply() {
		const qWords = f.q.toLowerCase().split(/\s+/).filter(Boolean);
		const qmin = f.qmin === "" ? -1 : +f.qmin, pmin = f.pmin === "" ? null : f.pmin * 10000, pmax = f.pmax === "" ? null : f.pmax * 10000;
		const lmin = f.lmin === "" ? null : +f.lmin, lmax = f.lmax === "" ? null : +f.lmax;
		const res = D.list.filter(it =>
			(f.c === "" || String(it.c) === f.c) && (f.s === "" || String(it.s) === f.s) &&
			(qmin < 0 || it.q >= qmin) && (!f.now || it.inScan) &&
			(lmin == null || it.req >= lmin) && (lmax == null || it.req <= lmax) &&
			(pmin == null || it.cur >= pmin) && (pmax == null || it.cur <= pmax) &&
			(!qWords.length || String(it.id) === f.q.trim() || qWords.every(w => it.lname?.includes(w))));
		list($("#results", el), "browse", res, ["item", "req", "av", "cur", "a7", "a30", "vs30", "wk", "de", "last"], { onChange: sync });
	}
	function sync() {
		const p = new URLSearchParams();
		for (const [k, v] of Object.entries(f)) if (v !== "" && v !== false) p.set(k, v === true ? "1" : v);
		if (st.sort !== "value" || st.dir !== -1) { p.set("sort", st.sort); p.set("dir", st.dir); }
		if (st.page) p.set("page", st.page);
		history.replaceState(null, "", href("browse", null, p));
	}
	let timer;
	form.oninput = () => {
		const fd = new FormData(form);
		for (const k of ["q", "lmin", "lmax", "qmin", "pmin", "pmax"]) f[k] = fd.get(k) || "";
		f.now = fd.get("now") === "on";
		clearTimeout(timer);
		timer = setTimeout(() => { st.page = 0; sync(); apply(); }, 120);
	};
	form.onsubmit = e => e.preventDefault();
	$("#reset", el).onclick = () => {
		delete S.lists.browse;
		history.replaceState(null, "", href("browse"));
		route();
	};
	$("#cats", el).onclick = e => {
		const a = e.target.closest("a[data-c]");
		if (!a) return;
		f.c = a.dataset.c;
		f.s = a.dataset.s ?? "";
		st.page = 0;
		sync();
		browse(el, new URLSearchParams(location.hash.split("?")[1] || ""));
	};
	apply();
	if (!params.toString()) $("input[name=q]", el).focus();
}

// ---------------------------------------------------------------------------
// Item page
// ---------------------------------------------------------------------------

// Uncommon to epic armor and weapons, except wands (they cannot be disenchanted in this version).
const disenchantable = it => (it.c === 2 || it.c === 4) && !(it.c === 2 && it.s === 19) && it.q >= 2 && it.q <= 4 && it.ilvl > 0;
function eraDrops(it) {
	if (!S.de || !disenchantable(it)) return null;
	const rows = S.de.era[it.c]?.[it.q] || [];
	const row = rows.find(r => it.ilvl >= r[0] && it.ilvl <= r[1]);
	return row ? row.slice(2).map(([chance, lo, hi, id]) => ({ id, chance, lo, hi, qty: chance / 100 * (lo + hi) / 2 })) : null;
}
function learnedDrops(it) {
	if (!S.de || !disenchantable(it)) return null;
	const b = it.ilvl <= 15 ? 15 : Math.ceil(it.ilvl / 5) * 5;
	const prefix = `${it.c}:${it.q}:`;
	let rec = S.de.learned[prefix + b], approx = false;
	if (!rec?.n) {
		let best = null;
		for (const [k, r] of Object.entries(S.de.learned)) {
			if (!r.n || !k.startsWith(prefix)) continue;
			const d = Math.abs(+k.slice(prefix.length) - b);
			if (d <= 10 && (!best || d < best[0])) best = [d, r];
		}
		if (!best) return null;
		rec = best[1];
		approx = true;
	}
	return { n: rec.n, approx, drops: Object.entries(rec.items).map(([id, q]) => ({ id: +id, qty: q / rec.n })) };
}
function matPrice(id, avg) {
	const m = S.data[S.realm.slug].byId.get(+id);
	if (!m) return null;
	return avg ? (m.a30 || m.all) : (m.last >= S.data[S.realm.slug].today - 7 ? m.cur : null);
}
function dropsTable(drops, showChance) {
	let now = 0, avg = 0;
	const rows = drops.map(d => {
		const m = S.data[S.realm.slug].byId.get(d.id) || { id: d.id, ...itemMeta(d.id) };
		const p = matPrice(d.id), pa = matPrice(d.id, true);
		now += (p || 0) * d.qty;
		avg += (pa || 0) * d.qty;
		return `<tr data-id="${d.id}"><td class="item">${itemLink(m, "sm")}</td>
			${showChance ? `<td class="r">${d.chance}%</td><td class="r">${d.lo === d.hi ? d.lo : d.lo + "-" + d.hi}</td>` : `<td class="r">${d.qty.toFixed(2)}</td>`}
			<td class="r">${money(p)}</td><td class="r">${money(p && p * d.qty)}</td></tr>`;
	}).join("");
	return {
		now, avg,
		html: `<table class="list"><thead><tr><th class="nosort">Material</th>${showChance ? `<th class="r nosort">Chance</th><th class="r nosort">Qty</th>` : `<th class="r nosort">Avg qty</th>`}<th class="r nosort">Price</th><th class="r nosort">Expected</th></tr></thead><tbody>${rows}</tbody></table>`,
	};
}

async function itemView(el, params, arg, token) {
	const id = +arg;
	const D = S.data[S.realm.slug];
	const it = D.byId.get(id) || { id, ...itemMeta(id), missing: true };
	const [hist, tt] = await Promise.all([itemHistory(id), tooltipHtml(id), load("de", async () => (S.de = await getJSON("disenchant.json"))), loadPosting(), loadVendors()]);
	if (token !== routeToken) return;
	const posts = (S.posting[id] || []).slice().reverse();
	const soldBy = S.vendors[id] || [];
	const sub = [it.ilvl ? `Item Level ${it.ilvl}` : "", it.req ? `Requires Level ${it.req}` : "", [className(it), subName(it)].filter(Boolean).join(" &rsaquo; "), S.items.slots[it.slot] || ""].filter(Boolean).join(" &middot; ");

	if (it.missing) {
		el.innerHTML = `<div class="item-head">${icon(it, "big")}<div><h1 class="q${it.q}">${esc(it.name)}</h1><div class="sub">${sub}</div></div></div>
			<div class="box">This item has never been seen on the ${esc(S.realm.name)} auction house.</div>`;
		return;
	}

	const era = eraDrops(it), learned = learnedDrops(it);
	const eraT = era && dropsTable(era, true), learnedT = learned && dropsTable(learned.drops, false);
	const fromAH = it.inScan ? it.cur : null;
	const card = (lbl, val, delta = "") => `<div class="card box"><div class="lbl">${lbl}</div><div class="val">${val}</div><div class="delta">${delta}</div></div>`;

	el.innerHTML = `
		<div class="item-head">
			${icon(it, "big")}
			<div><h1 class="q${it.q} iname">${esc(it.name)}</h1><div class="sub">${sub}</div></div>
			<div class="links">
				<a class="btn" href="https://www.wowhead.com/forever/item=${id}" target="_blank" rel="noopener">Wowhead</a>
				<a class="btn" href="javascript:history.back()">Back</a>
			</div>
		</div>
		<div class="cards" style="margin-bottom:16px">
			${card("Current Price", money(it.cur), `${pct(it.vs30)} <span class="muted">vs 30d avg</span>`)}
			${card("7 Day Average", money(it.a7), `${pct(it.wk)} <span class="muted">7d trend</span>`)}
			${card("30 Day Average", money(it.a30), `<span class="muted">all-time</span> ${money(it.all)}`)}
			${card("Lowest Ever", money(it.min), `<span class="muted">highest</span> ${money(it.max)}`)}
			${card("Available", num(it.av), `<span class="muted">avg</span> ${num(it.avAvg)} <span class="muted">listed</span>`)}
			${card("Seen", `${it.seen} <span class="muted" style="font-size:13px">days</span>`, ago(it.last))}
		</div>
		<div class="item-grid">
			<div class="stack">
				<div class="tooltip static">${tt || `<b class="q${it.q}">${esc(it.name)}</b>`}</div>
				<div class="box"><h3>Price Summary</h3><div class="kv">
					<span>Current (lowest buyout)</span><span>${money(it.cur)}</span>
					<span>Last 3 days avg</span><span>${money(it.a3)}</span>
					<span>Last 7 days avg</span><span>${money(it.a7)}</span>
					<span>Last 14 days avg</span><span>${money(it.a14)}</span>
					<span>Last 30 days avg</span><span>${money(it.a30)}</span>
					<span>All-time avg</span><span>${money(it.all)}</span>
					<div class="sep"></div>
					<span>Lowest ever</span><span>${money(it.min)}</span>
					<span>Highest ever</span><span>${money(it.max)}</span>
					<span>Day change</span><span>${pct(it.chg)}</span>
					<span>7 day trend</span><span>${pct(it.wk)}</span>
					<span>Volatility</span><span>${it.seen > 1 ? it.vol.toFixed(1) + "%" : "-"}</span>
					<div class="sep"></div>
					<span>First seen</span><span>${dayLabel(it.first, true)}</span>
					<span>Last seen</span><span>${dayLabel(it.last, true)}</span>
					<span>Days seen</span><span>${it.seen}</span>
					<div class="sep"></div>
					<span>Vendor sells for</span><span>${it.sell ? money(it.sell) : "-"}</span>
					<span>Vendor buy price</span><span>${it.buy ? money(it.buy) : "-"}</span>
					${it.sell && fromAH != null ? `<span>Vendor flip</span><span>${money(it.sell - fromAH)}</span>` : ""}
				</div></div>
				${soldBy.length ? `<div class="box"><h3>Sold by Vendors</h3>${vendorList(soldBy)}
					${fromAH != null ? `<div class="kv" style="margin-top:8px"><span>Auction house vs cheapest vendor</span><span>${pct((fromAH / Math.min(...soldBy.map(n => n.cost || Infinity)) - 1) * 100)}</span></div>` : ""}</div>` : ""}
				${posts.length ? `<div class="box"><h3>My Auctions</h3><table class="list"><thead><tr><th class="nosort">Posted</th><th class="r nosort">Each</th><th class="r nosort">Qty</th><th class="r nosort">vs now</th></tr></thead><tbody>
					${posts.map(([t, p, q]) => `<tr><td>${new Date(t * 1000).toLocaleDateString()}</td><td class="r">${money(p)}</td><td class="r">${q}</td><td class="r">${pct(it.cur ? (p / it.cur - 1) * 100 : null)}</td></tr>`).join("")}
				</tbody></table></div>` : ""}
			</div>
			<div class="stack">
				<div class="box">
					<div class="chart-head"><h3 style="margin:0">Price History</h3>
						<span class="seg" id="range">${[["7", "7D"], ["14", "14D"], ["30", "30D"], ["90", "90D"], ["0", "All"]].map(([v, l]) => `<button class="btn small" data-r="${v}">${l}</button>`).join("")}</span></div>
					<div class="chart" id="chart"></div>
					<div class="legend"><span><i style="background:rgba(255,209,0,.35);height:10px"></i>Lowest to highest minimum that day</span><span><i style="background:#ffd100"></i>Daily price</span><span><i style="background:#69ccf0"></i>30 day average</span><span><i class="bar" style="background:rgba(90,140,255,.45)"></i>Quantity listed</span></div>
				</div>
				${hist.length >= 7 ? `<div class="box"><h3>Best Day to Buy and Sell</h3><div class="chart short" id="weekday"></div><p class="note">Average price on each weekday relative to the item's overall average. Lower is a better day to buy, higher a better day to sell.</p></div>` : ""}
				${eraT || learnedT ? `<div class="box"><h3>Disenchanting</h3>
					${eraT ? `<div class="kv" style="margin-bottom:8px"><span>Expected value (era table, current prices)</span><span>${money(eraT.now)}</span>
						<span>Expected value (30 day avg prices)</span><span>${money(eraT.avg)}</span>
						${fromAH != null ? `<span>Profit buying at ${moneyText(fromAH)} and disenchanting</span><span>${money(eraT.now - fromAH)}</span>` : ""}</div>${eraT.html}` : ""}
					${learnedT ? `<h3 style="margin-top:14px">Your Disenchants <span class="muted" style="font-size:13px">(${learned.n} ${learned.approx ? "from nearby item levels" : "recorded"})</span></h3>
						<div class="kv" style="margin-bottom:8px"><span>Expected value from your results</span><span>${money(learnedT.now)}</span></div>${learnedT.html}` : ""}
				</div>` : ""}
				<div class="box"><h3>Daily History</h3><div id="days"></div></div>
			</div>
		</div>`;

	// chart with range buttons
	const rangeEl = $("#range", el);
	const all = hist.map(([d, lo, hi, av]) => ({ d, lo, hi, av: av || 0, mid: (lo + hi) / 2 }));
	function draw(r) {
		$$("button", rangeEl).forEach(b => b.classList.toggle("on", b.dataset.r === r));
		const rows = +r ? all.filter(p => p.d > D.today - +r) : all;
		const ma = rows.map(p => {
			const win = all.filter(q => q.d > p.d - 30 && q.d <= p.d);
			return win.reduce((s, q) => s + q.mid, 0) / win.length;
		});
		chart($("#chart", el), {
			x: rows.map(p => p.d),
			series: [
				{ type: "band", lo: rows.map(p => p.lo), hi: rows.map(p => p.hi), color: "rgba(255,209,0,.18)", name: "Range", fmt: (i) => `${moneyText(rows[i].lo)} - ${moneyText(rows[i].hi)}` },
				{ type: "bar", values: rows.map(p => p.av), color: "rgba(90,140,255,.38)", axis: "right", name: "Listed", fmt: v => Math.round(v).toLocaleString() },
				{ type: "line", values: ma, color: "#69ccf0", width: 1.5, dash: [5, 4], name: "30d avg", fmt: moneyText },
				{ type: "line", values: rows.map(p => p.mid), color: "#ffd100", width: 2.5, dots: true, name: "Price", fmt: moneyText },
			],
			yFmt: moneyText, y2Fmt: v => Math.round(v).toLocaleString(),
		});
	}
	rangeEl.onclick = e => { const b = e.target.closest("[data-r]"); if (b) draw(b.dataset.r); };
	draw(all.length > 30 ? "30" : "0");

	if (hist.length >= 7) {
		const sums = Array(7).fill(0), counts = Array(7).fill(0);
		const avg = all.reduce((s, p) => s + p.mid, 0) / all.length;
		for (const p of all) { const w = dayDate(p.d).getDay(); sums[w] += p.mid; counts[w]++; }
		const order = [1, 2, 3, 4, 5, 6, 0];
		const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
		chart($("#weekday", el), {
			labels: order.map(w => names[w]),
			x: order.map((_, i) => i),
			series: [{ type: "bar", values: order.map(w => (counts[w] ? (sums[w] / counts[w] / avg - 1) * 100 : 0)), color: v => (v < 0 ? "rgba(30,255,0,.55)" : "rgba(255,74,58,.55)"), name: "vs average", fmt: v => `${v > 0 ? "+" : ""}${v.toFixed(1)}%` }],
			yFmt: v => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`, zero: true,
		});
	}

	// daily history table (newest first)
	const dayRows = all.slice().reverse().map((p, i, arr) => {
		const prev = arr[i + 1];
		return `<tr><td>${dayLabel(p.d, true)}</td><td class="r">${money(p.lo)}</td><td class="r">${money(p.hi)}</td><td class="r">${p.hi > p.lo ? pct((p.hi / p.lo - 1) * 100) : "-"}</td><td class="r">${num(p.av)}</td><td class="r">${prev ? pct((p.mid / prev.mid - 1) * 100) : "-"}</td></tr>`;
	}).join("");
	$("#days", el).innerHTML = `<div class="tbl-wrap" style="max-height:420px;overflow:auto"><table class="list"><thead><tr><th class="nosort">Date</th><th class="r nosort">Lowest</th><th class="r nosort">Highest</th><th class="r nosort">Spread</th><th class="r nosort">Available</th><th class="r nosort">Change</th></tr></thead><tbody>${dayRows}</tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// Market overview
// ---------------------------------------------------------------------------

async function market(el, params, arg, token) {
	const D = S.data[S.realm.slug];
	const M = S.market[S.realm.slug] || (S.market[S.realm.slug] = await getJSON(`${S.realm.slug}/market.json`));
	if (token !== routeToken) return;
	const days = M.days.map(([d, items, listings, value, index, classes]) => ({ d, items, listings, value, index, classes }));
	const last = days[days.length - 1] || {}, prev = days[days.length - 2];
	const now = D.list.filter(it => it.inScan);
	const fresh = now.filter(it => it.first === D.today && it.seen === 1);
	const gone = D.list.filter(it => it.last < D.today && it.last >= D.today - 3 && it.seen >= 2);
	const card = (lbl, val, delta = "") => `<div class="card box"><div class="lbl">${lbl}</div><div class="val">${val}</div><div class="delta">${delta}</div></div>`;
	const change = (a, b) => (b ? pct((a / b - 1) * 100) + ` <span class="muted">vs previous day</span>` : "");

	const clsRows = Object.entries(last.classes || {}).map(([c, [qty, val]]) => ({ c: +c, qty, val })).sort((a, b) => b.val - a.val);
	const totalVal = clsRows.reduce((s, r) => s + r.val, 0) || 1;

	el.innerHTML = `
		<div class="cards" style="margin-bottom:16px">
			${card("Items on the AH", num(last.items), change(last.items, prev?.items))}
			${card("Total Listings", num(last.listings), change(last.listings, prev?.listings))}
			${card("Market Value", money(last.value), change(last.value, prev?.value))}
			${card("Price Index", last.index != null ? last.index.toFixed(1) : "-", prev?.index != null ? `${pct(last.index - prev.index)} <span class="muted">points</span>` : "")}
			${card("Items Tracked", num(D.list.length), `${S.realm.days} days of history`)}
			${card("New Today", num(fresh.length), `${num(gone.length)} <span class="muted">gone recently</span>`)}
		</div>
		<div class="grid2" style="margin-bottom:14px">
			<div class="box"><h3>Market Value</h3><div class="chart short" id="c-value"></div><p class="note">Sum of lowest buyout times quantity for every item seen that day.</p></div>
			<div class="box"><h3>Price Index</h3><div class="chart short" id="c-index"></div><p class="note">Median of every item's price relative to its own average. 100 = normal, above = expensive day.</p></div>
			<div class="box"><h3>Listings</h3><div class="chart short" id="c-listings"></div></div>
			<div class="box"><h3>Distinct Items</h3><div class="chart short" id="c-items"></div></div>
		</div>
		<div class="box" style="margin-bottom:14px"><h3>Categories (latest scan)</h3><table class="list"><thead><tr><th class="nosort">Category</th><th class="r nosort">Listings</th><th class="r nosort">Value</th><th class="nosort">Share</th></tr></thead><tbody>
			${clsRows.map(r => `<tr data-c="${r.c}"><td><a href="${href("browse", null, new URLSearchParams({ c: r.c }))}">${esc(S.items.classes[r.c] || "Unknown")}</a></td><td class="r">${num(r.qty)}</td><td class="r">${money(r.val)}</td><td class="bar-cell"><span class="b" style="width:${(r.val / totalVal * 100).toFixed(1)}%"></span><span>${(r.val / totalVal * 100).toFixed(1)}%</span></td></tr>`).join("")}
		</tbody></table></div>
		<div class="grid2">
			<div class="box"><h3>Biggest Risers (7 days)</h3><div id="l-up"></div></div>
			<div class="box"><h3>Biggest Fallers (7 days)</h3><div id="l-down"></div></div>
			<div class="box"><h3>Most Listed</h3><div id="l-listed"></div></div>
			<div class="box"><h3>Most Valuable Listings</h3><div id="l-value"></div></div>
			<div class="box"><h3>Most Expensive</h3><div id="l-exp"></div></div>
			<div class="box"><h3>Most Volatile</h3><div id="l-vol"></div></div>
			<div class="box"><h3>New on the Market</h3><div id="l-new"></div></div>
			<div class="box"><h3>Gone from the Market</h3><div id="l-gone"></div></div>
		</div>`;

	const x = days.map(p => p.d);
	const line = (id, values, color, fmt) => chart($(id, el), { x, series: [{ type: "line", values, color, width: 2.5, dots: true, fill: true, name: "", fmt }], yFmt: fmt });
	line("#c-value", days.map(p => p.value), "#ffd100", moneyText);
	line("#c-index", days.map(p => p.index), "#69ccf0", v => v?.toFixed(1));
	line("#c-listings", days.map(p => p.listings), "#a335ee", v => Math.round(v).toLocaleString());
	line("#c-items", days.map(p => p.items), "#1eff00", v => Math.round(v).toLocaleString());

	const movers = now.filter(it => it.wk != null && it.seen >= 3 && it.a7 >= 100);
	const top = (id, key, data, cols, sort, dir = -1) => list($(id, el), "m:" + key + S.realm.slug, data, cols, { sort, dir, pageSize: 10 });
	top("#l-up", "up", movers.filter(it => it.wk > 0), ["item", "cur", "wk"], "wk");
	top("#l-down", "down", movers.filter(it => it.wk < 0), ["item", "cur", "wk"], "wk", 1);
	top("#l-listed", "listed", now, ["item", "av", "cur"], "av");
	top("#l-value", "value", now, ["item", "av", "value"], "value");
	top("#l-exp", "exp", now, ["item", "cur", "a30"], "cur");
	top("#l-vol", "vol", now.filter(it => it.seen >= 4), ["item", "vol", "min", "max"], "vol");
	top("#l-new", "new", fresh, ["item", "cur", "av"], "cur");
	top("#l-gone", "gone", gone, ["item", "a30", "last"], "a30");
}

// ---------------------------------------------------------------------------
// Deals, disenchant, vendor flips, my auctions
// ---------------------------------------------------------------------------

function settings(el, key, fields, render) {
	const saved = S.lists[key + ":f"] || (S.lists[key + ":f"] = Object.fromEntries(fields.map(f => [f.k, f.v])));
	$(".toolbar", el).innerHTML = fields.map(f => f.type === "check"
		? `<label class="chk"><input type="checkbox" data-k="${f.k}" ${saved[f.k] ? "checked" : ""}> ${f.label}</label>`
		: `<label>${f.label} <input type="number" step="any" min="0" data-k="${f.k}" value="${saved[f.k]}"></label>`).join("") + `<span class="grow"></span>`;
	$(".toolbar", el).oninput = e => {
		const k = e.target.dataset.k;
		if (!k) return;
		saved[k] = e.target.type === "checkbox" ? e.target.checked : +e.target.value || 0;
		render(saved, true);
	};
	render(saved);
}

async function deals(el) {
	const D = S.data[S.realm.slug];
	el.innerHTML = `<h2>Deals</h2><p class="note" style="margin-bottom:12px">Items on the auction house right now, priced below their 30 day average.</p><div class="toolbar"></div><div id="res"></div>`;
	settings(el, "deals", [
		{ k: "disc", label: "Min discount %", v: 20 },
		{ k: "minavg", label: "Min 30d avg (gold)", v: 0.5 },
		{ k: "seen", label: "Min days seen", v: 3 },
	], (f, reset) => {
		const res = D.list.filter(it => it.inScan && it.vs30 != null && -it.vs30 >= f.disc && it.a30 >= f.minavg * 10000 && it.seen >= f.seen);
		list($("#res", el), "deals", res, ["item", "cur", "a30", "discount", "gain", "av", "seen", "vol"], { sort: "gain", resetPage: reset, empty: "No deals with these settings." });
	});
}

async function disenchant(el, params, arg, token) {
	if (!S.de) S.de = await load("de", () => getJSON("disenchant.json"));
	if (token !== routeToken) return;
	const D = S.data[S.realm.slug];
	const mats = S.de.materials.map(id => D.byId.get(id) || { id, ...itemMeta(id), cur: null });
	el.innerHTML = `<h2>Disenchanting</h2>
		<p class="note" style="margin-bottom:12px">Uncommon, rare and epic armor and weapons on the auction house that are worth more disenchanted than their buyout. Values use the Classic Era disenchant tables and current material prices.</p>
		<div class="grid2" style="grid-template-columns:minmax(0, 3fr) minmax(0, 2fr)">
			<div class="box"><h3>Opportunities</h3><div class="toolbar"></div><div id="res"></div></div>
			<div class="stack">
				<div class="box"><h3>Enchanting Materials</h3><div id="mats"></div></div>
				<div class="box"><h3>Value by Item Level</h3><div id="brackets"></div><p class="note">Expected disenchant value of one item in each item level range at current prices.</p></div>
			</div>
		</div>`;
	settings(el, "de", [{ k: "minp", label: "Min profit (silver)", v: 0 }], (f, reset) => {
		const res = D.list.filter(it => it.deProfit != null && it.deProfit > f.minp * 100);
		list($("#res", el), "de", res, ["item", "ilvl", "cur", "de", "deProfit", "av"], { sort: "deProfit", resetPage: reset, empty: "Nothing is worth disenchanting right now." });
	});
	list($("#mats", el), "mats", mats.filter(m => m.cur != null), ["item", "cur", "wk"], { sort: "cur", pageSize: 30, noPager: true });
	const brackets = [];
	for (const [cls, label] of [[4, "Armor"], [2, "Weapon"]]) {
		for (const q of [2, 3, 4]) {
			for (const row of S.de.era[cls][q]) {
				if (q > 2 && cls === 2) continue; // rare/epic tables are the same for weapons
				const drops = row.slice(2);
				const v = drops.reduce((s, [ch, lo, hi, id]) => s + (matPrice(id) || 0) * ch / 100 * (lo + hi) / 2, 0);
				brackets.push(`<tr><td class="q${q}">${QUALITY[q]} ${q === 2 ? label : ""}</td><td class="r">${row[0]}-${row[1] === 999 ? "+" : row[1]}</td><td>${drops.map(([, , , id]) => { const m = itemMeta(id); return `<a href="${href("item", id)}" data-tip="${id}">${icon(m, "sm")}</a>`; }).join(" ")}</td><td class="r">${money(v)}</td></tr>`);
			}
		}
	}
	$("#brackets", el).innerHTML = `<div class="tbl-wrap" style="max-height:420px;overflow:auto"><table class="list"><thead><tr><th class="nosort">Quality</th><th class="r nosort">iLvl</th><th class="nosort">Drops</th><th class="r nosort">Value</th></tr></thead><tbody>${brackets.join("")}</tbody></table></div>`;
}

async function flips(el) {
	const D = S.data[S.realm.slug];
	el.innerHTML = `<h2>Vendor Flips</h2><p class="note" style="margin-bottom:12px">Items listed for less than a vendor pays for them: buy them out and sell them to any vendor.</p><div class="toolbar"></div><div id="res"></div>
		<h2 style="margin-top:24px">Vendor Items Above Vendor Price</h2><p class="note" style="margin-bottom:12px">Items a vendor sells, listed on the auction house for more than the vendor asks.</p><div id="res2"></div>`;
	settings(el, "flips", [{ k: "minp", label: "Min profit (copper)", v: 1 }], (f, reset) => {
		list($("#res", el), "flips", D.list.filter(it => it.flip != null && it.flip >= f.minp), ["item", "cur", "sell", "flip", "av"], { sort: "flip", resetPage: reset, empty: "No vendor flips in the latest scan." });
	});
	COLS.buy = { label: "Vendor Price", sort: it => it.buy, cls: "r", fmt: it => money(it.buy) };
	COLS.markup = { label: "Markup", sort: it => it.cur / it.buy, cls: "r", fmt: it => pct((it.cur / it.buy - 1) * 100) };
	list($("#res2", el), "vbuy", D.list.filter(it => it.inScan && it.buy && it.cur > it.buy), ["item", "buy", "cur", "markup", "av"], { sort: "markup" });
}

// ---------------------------------------------------------------------------
// Vendor recipes: recipes an NPC sells, compared with their auction price
// ---------------------------------------------------------------------------

function loadVendors() {
	return load("vendors", async () => (S.vendors = await getJSON("vendors.json")));
}
// Neutral vendors (friendly to neither in the data) sell to both factions.
const sellsTo = (n, fac) => (fac === "A" ? n.a || !n.h : fac === "H" ? n.h || !n.a : true);
function vendorList(npcs) {
	return npcs.map(n => `<div class="vendor"><span class="money" style="float:right">${money(n.cost)}</span>
		<b>${esc(n.name)}</b> ${n.a && !n.h ? `<span class="pill ally">Alliance</span>` : n.h && !n.a ? `<span class="pill horde">Horde</span>` : ""}
		${n.stock > 0 ? `<span class="pill limited" title="The vendor only has ${n.stock} at a time, restocking over time">Limited ${n.stock}</span>` : ""}
		<div class="muted">${esc([n.tag, n.zone].filter(Boolean).join(" · "))}</div></div>`).join("");
}

async function recipes(el, params, arg, token) {
	await loadVendors();
	if (token !== routeToken) return;
	const D = S.data[S.realm.slug];
	const f = S.lists["recipes:f"] || (S.lists["recipes:f"] = { fac: "", prof: "", now: true, limited: false });
	const all = Object.keys(S.vendors).map(Number).filter(id => D.byId.has(id)).map(id => D.byId.get(id));
	const profs = [...new Set(all.map(subName))].filter(Boolean).sort();
	el.innerHTML = `<h2>Vendor Recipes</h2>
		<p class="note" style="margin-bottom:12px">Recipes a vendor sells that also show up on the auction house. A positive difference means the auction house asks more than the vendor: buy from the vendor (and resell). Negative means the auction house is the cheaper place to buy.</p>
		<div class="toolbar">
			<label>Faction <select data-k="fac"><option value="">Both</option><option value="A">Alliance</option><option value="H">Horde</option></select></label>
			<label>Profession <select data-k="prof"><option value="">All</option>${profs.map(p => `<option>${esc(p)}</option>`).join("")}</select></label>
			<label class="chk"><input type="checkbox" data-k="now"> On the AH now</label>
			<label class="chk"><input type="checkbox" data-k="limited"> Limited stock only</label>
		</div><div id="res"></div>`;
	const bar = $(".toolbar", el);
	for (const input of $$("[data-k]", bar)) {
		if (input.type === "checkbox") input.checked = f[input.dataset.k];
		else input.value = f[input.dataset.k];
	}
	COLS.prof = { label: "Profession", sort: it => subName(it), fmt: it => esc(subName(it)) };
	COLS.vprice = { label: "Vendor", title: "Cheapest vendor price for the chosen faction", sort: it => it.vprice, cls: "r", fmt: it => money(it.vprice) };
	COLS.vdiff = { label: "AH - Vendor", title: "Auction price minus vendor price", sort: it => it.cur - it.vprice, cls: "r", fmt: it => money(it.cur - it.vprice) };
	COLS.vmargin = { label: "Margin", title: "Auction price compared to the vendor price", sort: it => it.cur / it.vprice, cls: "r", fmt: it => pct((it.cur / it.vprice - 1) * 100) };
	const npcLine = n => `<span class="${n.a && !n.h ? "ally" : n.h && !n.a ? "horde" : ""}">${esc(n.name)}</span>${n.zone ? ` <span class="muted">(${esc(n.zone)})</span>` : ""}${n.stock > 0 ? ` <span class="pill limited">Limited</span>` : ""}`;
	COLS.npcs = { label: "Sold By", sort: it => it.npcs[0]?.name || "", fmt: it => {
		const more = it.npcs.length - 2;
		const title = it.npcs.map(n => `${n.name}${n.zone ? ` (${n.zone})` : ""}`).join("\n");
		return it.npcs.slice(0, 2).map(npcLine).join("<br>") + (more > 0 ? `<br><span class="muted" title="${esc(title)}">+${more} more</span>` : "");
	} };
	const render = reset => {
		const rows = [];
		for (const it of all) {
			const npcs = S.vendors[it.id].filter(n => sellsTo(n, f.fac) && n.cost);
			if (!npcs.length || (f.now && !it.inScan) || (f.prof && subName(it) !== f.prof) || (f.limited && !npcs.some(n => n.stock > 0))) continue;
			npcs.sort((a, b) => a.cost - b.cost);
			rows.push({ ...it, npcs, vprice: npcs[0].cost });
		}
		list($("#res", el), "recipes", rows, ["item", "prof", "vprice", "cur", "vdiff", "vmargin", "a30", "av", "npcs"], { sort: "vdiff", resetPage: reset, empty: "No vendor recipes match." });
	};
	bar.oninput = e => {
		const k = e.target.dataset.k;
		if (!k) return;
		f[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
		render(true);
	};
	render();
}

function loadPosting() {
	return load("posting", async () => (S.posting = await getJSON("posting.json")));
}

// ---------------------------------------------------------------------------
// Canvas chart: lines, a low/high band and bars on a second axis, with hover readout
// ---------------------------------------------------------------------------

function chart(box, cfg) {
	box.innerHTML = `<canvas></canvas>`;
	const cv = $("canvas", box);
	const state = { hover: -1 };
	const draw = () => {
		const dpr = window.devicePixelRatio || 1;
		const W = box.clientWidth, H = box.clientHeight;
		if (!W) return;
		cv.width = W * dpr; cv.height = H * dpr;
		const g = cv.getContext("2d");
		g.setTransform(dpr, 0, 0, dpr, 0, 0);
		g.font = "11px 'Friz Quadrata', Georgia, serif";
		const n = cfg.x.length;
		if (!n) { g.fillStyle = "#a59c86"; g.fillText("No data yet", W / 2 - 30, H / 2); return; }
		const left = cfg.series.filter(s => s.axis !== "right");
		const right = cfg.series.filter(s => s.axis === "right");
		const range = ss => {
			let lo = Infinity, hi = -Infinity;
			for (const s of ss) for (const v of [...(s.values || []), ...(s.lo || []), ...(s.hi || [])]) if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
			if (!isFinite(lo)) return [0, 1];
			if (cfg.zero || ss.some(s => s.type === "bar")) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
			if (hi === lo) { hi += Math.abs(hi) * 0.1 || 1; lo -= Math.abs(lo) * 0.1 || 0; }
			const pad = (hi - lo) * 0.08;
			return [lo === 0 || (lo > 0 && lo - pad < 0) ? 0 : lo - pad, hi + pad];
		};
		const [y0, y1] = range(left), [r0, r1] = range(right);
		const pl = 62, pr = right.length ? 52 : 14, pt = 10, pb = 24;
		const cw = W - pl - pr, ch = H - pt - pb;
		const xmin = cfg.x[0], xmax = cfg.x[n - 1];
		const step = n > 1 ? cw / (xmax - xmin + 1) : cw;
		const X = i => pl + (n > 1 ? ((cfg.x[i] - xmin) + 0.5) * step : cw / 2);
		const Y = v => pt + ch - ((v - y0) / (y1 - y0)) * ch;
		const Y2 = v => pt + ch - ((v - r0) / (r1 - r0)) * ch;
		// grid
		g.strokeStyle = "rgba(232,204,122,.08)"; g.fillStyle = "#a59c86"; g.lineWidth = 1;
		for (let k = 0; k <= 4; k++) {
			const v = y0 + (y1 - y0) * k / 4, y = Math.round(Y(v)) + 0.5;
			g.beginPath(); g.moveTo(pl, y); g.lineTo(W - pr, y); g.stroke();
			g.textAlign = "right"; g.fillText(cfg.yFmt ? cfg.yFmt(v) : v.toFixed(0), pl - 6, y + 4);
			if (right.length) { g.textAlign = "left"; g.fillText(cfg.y2Fmt ? cfg.y2Fmt(r0 + (r1 - r0) * k / 4) : "", W - pr + 6, y + 4); }
		}
		g.textAlign = "center";
		const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(cw / 64))));
		for (let i = 0; i < n; i += every) g.fillText(cfg.labels ? cfg.labels[i] : dayLabel(cfg.x[i]), X(i), H - 6);
		if (cfg.zero && y0 < 0) { g.strokeStyle = "rgba(255,255,255,.25)"; g.beginPath(); g.moveTo(pl, Y(0)); g.lineTo(W - pr, Y(0)); g.stroke(); }
		// series
		for (const s of cfg.series) {
			const Ys = s.axis === "right" ? Y2 : Y;
			if (s.type === "bar") {
				const bw = Math.max(2, Math.min(40, step * 0.6));
				s.values.forEach((v, i) => {
					if (v == null) return;
					g.fillStyle = typeof s.color === "function" ? s.color(v) : s.color;
					const y = Ys(Math.max(v, 0)), y2 = Ys(Math.min(v, 0));
					g.fillRect(X(i) - bw / 2, y, bw, Math.max(1, y2 - y));
				});
			} else if (s.type === "band") {
				g.fillStyle = s.color; g.beginPath();
				s.hi.forEach((v, i) => (i ? g.lineTo(X(i), Ys(v)) : g.moveTo(X(i), Ys(v))));
				for (let i = n - 1; i >= 0; i--) g.lineTo(X(i), Ys(s.lo[i]));
				g.closePath(); g.fill();
				if (n === 1) { g.fillRect(X(0) - 6, Ys(s.hi[0]), 12, Math.max(2, Ys(s.lo[0]) - Ys(s.hi[0]))); }
			} else {
				const pts = s.values.map((v, i) => (v == null ? null : [X(i), Ys(v)])).filter(Boolean);
				if (s.fill && pts.length > 1) {
					const grad = g.createLinearGradient(0, pt, 0, pt + ch);
					grad.addColorStop(0, s.color + "55"); grad.addColorStop(1, s.color + "00");
					g.fillStyle = grad; g.beginPath(); g.moveTo(pts[0][0], pt + ch);
					pts.forEach(p => g.lineTo(p[0], p[1])); g.lineTo(pts[pts.length - 1][0], pt + ch); g.fill();
				}
				g.strokeStyle = s.color; g.lineWidth = s.width || 2; g.setLineDash(s.dash || []); g.lineJoin = "round";
				g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1]))); g.stroke();
				g.setLineDash([]);
				if (s.dots || pts.length === 1) {
					g.fillStyle = s.color;
					pts.forEach(p => { g.beginPath(); g.arc(p[0], p[1], n > 60 ? 1.5 : 3, 0, 7); g.fill(); });
				}
			}
		}
		// hover
		const i = state.hover;
		if (i >= 0 && i < n) {
			g.strokeStyle = "rgba(255,255,255,.35)"; g.beginPath(); g.moveTo(Math.round(X(i)) + 0.5, pt); g.lineTo(Math.round(X(i)) + 0.5, pt + ch); g.stroke();
			const lines = [cfg.labels ? cfg.labels[i] : dayLabel(cfg.x[i], true), ...cfg.series.filter(s => s.name !== undefined).map(s => {
				const v = s.type === "band" ? s.fmt(i) : s.fmt(s.values[i]);
				return [s.name, v, typeof s.color === "function" ? s.color(s.values[i]) : s.color];
			})];
			g.font = "12px 'Friz Quadrata', Georgia, serif";
			const tw = Math.max(...lines.map(l => (typeof l === "string" ? g.measureText(l).width : g.measureText(l[0] + "  " + l[1]).width + 16))) + 20;
			const th = lines.length * 17 + 10;
			let tx = X(i) + 12; if (tx + tw > W) tx = X(i) - tw - 12;
			const ty = pt + 4;
			g.fillStyle = "rgba(4,6,22,.94)"; g.strokeStyle = "#b7b7b7"; g.lineWidth = 1.5;
			g.beginPath(); g.roundRect ? g.roundRect(tx, ty, tw, th, 5) : g.rect(tx, ty, tw, th); g.fill(); g.stroke();
			lines.forEach((l, k) => {
				const y = ty + 18 + k * 17;
				g.textAlign = "left";
				if (typeof l === "string") { g.fillStyle = "#ffd100"; g.fillText(l, tx + 10, y); return; }
				g.fillStyle = l[2]; g.fillRect(tx + 10, y - 8, 8, 8);
				g.fillStyle = "#e8e2d0"; g.fillText(l[0], tx + 24, y);
				g.fillStyle = "#fff"; g.textAlign = "right"; g.fillText(l[1] ?? "-", tx + tw - 10, y);
			});
		}
		state.X = X;
		state.n = n;
	};
	cv.onmousemove = e => {
		const r = cv.getBoundingClientRect(), mx = e.clientX - r.left;
		let best = -1, bd = Infinity;
		for (let i = 0; i < state.n; i++) { const d = Math.abs(state.X(i) - mx); if (d < bd) { bd = d; best = i; } }
		if (best !== state.hover) { state.hover = best; draw(); }
	};
	cv.onmouseleave = () => { state.hover = -1; draw(); };
	new ResizeObserver(draw).observe(box);
	draw();
}

// ---------------------------------------------------------------------------
// Item tooltips on hover (Wowhead markup + Auctionator-style price lines)
// ---------------------------------------------------------------------------

const tip = $("#tip");
let tipId = null;
function hideTip() { tip.hidden = true; tipId = null; }
function placeTip(e) {
	const pad = 16, w = tip.offsetWidth, h = tip.offsetHeight;
	let x = e.clientX + pad, y = e.clientY + pad;
	if (x + w > innerWidth - 4) x = e.clientX - w - pad;
	if (y + h > innerHeight - 4) y = Math.max(4, innerHeight - h - 4);
	tip.style.left = x + "px"; tip.style.top = y + "px";
}
async function showTip(id, e) {
	tipId = id;
	const it = S.data[S.realm.slug]?.byId.get(id) || { id, ...itemMeta(id) };
	const lines = it.cur != null ? `<div class="price-lines">
		<div class="pl">Auction${it.inScan ? "" : ` <span class="muted">(${S.data[S.realm.slug].today - it.last}d old)</span>`}<span>${money(it.cur)}</span></div>
		${it.a30 ? `<div class="pl">30 day avg<span>${money(it.a30)}</span></div>` : ""}
		${it.de ? `<div class="pl">Disenchant<span>${money(it.de)}</span></div>` : ""}
		${it.sell ? `<div class="pl">Vendor<span>${money(it.sell)}</span></div>` : ""}
		${it.av ? `<div class="pl">Available<span class="money">${num(it.av)}</span></div>` : ""}</div>` : "";
	tip.innerHTML = `<b class="q${it.q}">${esc(it.name)}</b>${lines}`;
	tip.hidden = false;
	placeTip(e);
	const html = await tooltipHtml(id).catch(() => null);
	if (tipId !== id || !html) return;
	tip.innerHTML = html + lines;
	placeTip(e);
}
if (matchMedia("(hover: hover)").matches) {
	document.addEventListener("mouseover", e => {
		const a = e.target.closest("[data-tip]");
		if (!a) { if (tipId != null) hideTip(); return; }
		const id = +a.dataset.tip;
		if (id !== tipId) showTip(id, e);
	});
	document.addEventListener("mousemove", e => { if (tipId != null) placeTip(e); });
}

// ---------------------------------------------------------------------------
// Quick search (header)
// ---------------------------------------------------------------------------

function quickSearch() {
	const input = $("#quick"), menu = $("#quick-results");
	let hits = [], sel = 0;
	const render = () => {
		menu.hidden = !hits.length;
		menu.innerHTML = hits.map((it, i) => `<a href="${href("item", it.id)}" class="${i === sel ? "on" : ""}"><span class="iname q${it.q}">${icon(it, "sm")} ${esc(it.name)}</span>${money(it.cur)}</a>`).join("");
	};
	input.oninput = () => {
		const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
		const D = S.data[S.realm.slug];
		hits = !words.length || !D ? [] : D.list.filter(it => words.every(w => it.lname?.includes(w)))
			.sort((a, b) => (b.lname.startsWith(words[0]) - a.lname.startsWith(words[0])) || (b.inScan - a.inScan) || a.name.localeCompare(b.name)).slice(0, 12);
		sel = 0;
		render();
	};
	input.onkeydown = e => {
		if (e.key === "ArrowDown") { sel = Math.min(hits.length - 1, sel + 1); render(); e.preventDefault(); }
		else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); render(); e.preventDefault(); }
		else if (e.key === "Enter") {
			if (hits[sel]) location.hash = href("item", hits[sel].id);
			else location.hash = href("browse", null, new URLSearchParams({ q: input.value }));
			input.value = ""; hits = []; render(); input.blur();
		} else if (e.key === "Escape") { hits = []; render(); }
	};
	menu.onclick = () => { input.value = ""; hits = []; render(); };
	input.onblur = () => setTimeout(() => { hits = []; render(); }, 150);
	document.addEventListener("keydown", e => {
		if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); input.focus(); }
	});
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

(async function start() {
	try {
		const r = await fetch(`data/realms.json?t=${Date.now()}`, { cache: "no-store" });
		S.meta = await r.json();
		S.items = await getJSON("items.json");
	} catch (e) {
		$("#view").innerHTML = `<div class="loading">No auction data uploaded yet.</div>`;
		return;
	}
	if (!S.meta.realms.length) {
		$("#view").innerHTML = `<div class="loading">No auction data uploaded yet.</div>`;
		return;
	}
	document.title = S.meta.title;
	$("#site-title").textContent = S.meta.title;
	$("#realm").innerHTML = S.meta.realms.map(r => `<option value="${esc(r.slug)}">${esc(r.name)}</option>`).join("");
	$("#realm").onchange = e => {
		const { view, arg } = parseHash();
		S.realm = S.meta.realms.find(r => r.slug === e.target.value);
		location.hash = href(view, arg);
	};
	quickSearch();
	window.addEventListener("hashchange", route);
	route();
})();
