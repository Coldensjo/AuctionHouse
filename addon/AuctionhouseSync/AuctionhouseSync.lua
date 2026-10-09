--[[
AuctionhouseSync
----------------
Auctionator only keeps one low/high price per item per day. This addon records every scan
separately so the auction house website can show more than one scan a day:

	* Hooks Auctionator's price database. Every time Auctionator processes auction data
	  (a full scan or a normal search) a snapshot is stored with the time and, per item:
	  lowest unit price, quantity listed, number of auctions and median unit price.
	* Full scans are marked as such (the website uses them for "on the AH now" and market totals).
	* WoW only writes saved variables on /reload, logout or exit. After a full scan the addon
	  offers a reload, so the sync program on your PC can upload the scan right away.

/ahsync          status
/ahsync popup    toggle the reload popup after full scans
/ahsync clear    forget stored scans
]]

local ADDON = ...
local MAX_SCANS = 60 -- kept in the saved variables until the sync program has had a chance to read them
local MAX_AGE = 4 * 86400

local db
local pending -- the newest snapshot, flagged full when Auctionator reports its full scan complete
local hooked = {}

local function Print(msg)
	print("|cff33ff99Auctionhouse Sync|r: " .. msg)
end

local function RealmKey()
	local ok, realm = pcall(Auctionator.Variables.GetConnectedRealmRoot)
	return ok and realm or GetRealmName()
end

-- "id:min:qty:auctions:median,..." for every plain item ID key in Auctionator's scan data
local function Summarize(itemIndexes)
	local parts = {}
	for key, entries in pairs(itemIndexes) do
		local id = tonumber(key)
		if id and #entries > 0 then
			local sorted, qty = {}, 0
			for i, e in ipairs(entries) do
				sorted[i] = e
				qty = qty + (e.available or 0)
			end
			table.sort(sorted, function(a, b) return a.price < b.price end)
			local median, seen = sorted[1].price, 0
			for _, e in ipairs(sorted) do
				seen = seen + (e.available or 0)
				if seen * 2 >= qty then
					median = e.price
					break
				end
			end
			parts[#parts + 1] = id .. ":" .. sorted[1].price .. ":" .. qty .. ":" .. #entries .. ":" .. median
		end
	end
	return table.concat(parts, ","), #parts
end

local function Prune()
	local now = time()
	for i = #db.scans, 1, -1 do
		if now - db.scans[i].t > MAX_AGE then table.remove(db.scans, i) end
	end
	while #db.scans > MAX_SCANS do table.remove(db.scans, 1) end
end

StaticPopupDialogs["AUCTIONHOUSESYNC_RELOAD"] = {
	text = "Auctionhouse Sync\n\nFull scan saved (%s items).\nReload the UI now so it can be uploaded to the website?",
	button1 = "Reload & Upload",
	button2 = "Later",
	OnAccept = function() ReloadUI() end,
	timeout = 0,
	whileDead = true,
	hideOnEscape = true,
	preferredIndex = 3,
}

local function FullScanDone()
	if not pending or pending.full then return end
	pending.full = true
	Print(("full scan recorded: %d items. It is uploaded on your next /reload, logout or exit."):format(pending.n))
	if db.popup then
		StaticPopup_Show("AUCTIONHOUSESYNC_RELOAD", pending.n)
	end
end

-- Calls FullScanDone once Auctionator has finished writing the scan into its own price database
local function FullScanDoneWhenProcessed(database)
	local waited = 0
	local ticker
	ticker = C_Timer.NewTicker(0.5, function()
		waited = waited + 0.5
		if not database.ticker or database.ticker:IsCancelled() or waited >= 30 then
			ticker:Cancel()
			FullScanDone()
		end
	end)
end

local function OnProcessScan(database, itemIndexes)
	if type(itemIndexes) ~= "table" then return end
	local data, count = Summarize(itemIndexes)
	if count == 0 then return end
	pending = { t = time(), realm = RealmKey(), faction = UnitFactionGroup("player"), full = false, n = count, data = data }
	table.insert(db.scans, pending)
	Prune()
	-- Auctionator's scan complete event does not always reach us, so a scan processed from
	-- Auctionator's full scan code counts as a full scan too
	if (debugstack(2) or ""):find("FullScan") then
		FullScanDoneWhenProcessed(database)
	end
end

local function HookDatabase()
	local database = Auctionator and Auctionator.Database
	if database and not hooked[database] and database.ProcessScan then
		hooksecurefunc(database, "ProcessScan", OnProcessScan)
		hooked[database] = true
	end
end

local listener = {
	ReceiveEvent = function(_, eventName)
		if Auctionator.FullScan and eventName == Auctionator.FullScan.Events.ScanComplete then
			FullScanDone()
		end
	end,
}

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:SetScript("OnEvent", function(_, event, name)
	if event == "ADDON_LOADED" and name == ADDON then
		AuctionhouseSyncDB = AuctionhouseSyncDB or {}
		db = AuctionhouseSyncDB
		db.scans = db.scans or {}
		if db.popup == nil then db.popup = true end
		-- Auctionator (re)creates its database in InitializeDatabase: hook every instance it makes
		if Auctionator and Auctionator.Variables and Auctionator.Variables.InitializeDatabase then
			hooksecurefunc(Auctionator.Variables, "InitializeDatabase", HookDatabase)
		end
		HookDatabase()
	elseif event == "PLAYER_LOGIN" then
		HookDatabase()
		if Auctionator and Auctionator.EventBus and Auctionator.FullScan then
			Auctionator.EventBus:Register(listener, { Auctionator.FullScan.Events.ScanComplete })
		end
	end
end)

SLASH_AUCTIONHOUSESYNC1 = "/ahsync"
SlashCmdList["AUCTIONHOUSESYNC"] = function(msg)
	local cmd = (msg or ""):lower():match("^(%S*)")
	if cmd == "popup" then
		db.popup = not db.popup
		Print("reload popup after full scans: " .. (db.popup and "on" or "off"))
	elseif cmd == "clear" then
		wipe(db.scans)
		Print("stored scans cleared")
	else
		local full = 0
		for _, s in ipairs(db.scans) do if s.full then full = full + 1 end end
		Print(("%d scans stored (%d full), hooked: %s, popup: %s"):format(#db.scans, full, tostring(next(hooked) ~= nil), db.popup and "on" or "off"))
		Print("/ahsync popup | /ahsync clear")
	end
end
