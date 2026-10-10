--[[
AuctionhouseSync
----------------
Records full auction house scans for the auction house website.

	* A Scan button on the auction house window (or /ahsync scan) runs a full scan: one "replicate"
	  request on the modern auction house, "get all" on the classic one (allowed every 15 minutes),
	  then every auction is read: item, stack size, buyout,
	  current bid, time left and seller. Seller names the client has not loaded yet are read again
	  a few seconds later.
	* Per item the scan keeps the lowest unit price, quantity listed, number of auctions and median
	  unit price. The auction list itself lets the website show who sells what and estimate what
	  sold between two scans.
	* The prices are also handed to Auctionator, so its tooltips stay current.
	* Auctionator's own searches are recorded too (hooked on its price database). When its Full Scan
	  button is used instead of ours, the result is read the same way.
	* WoW only writes saved variables on /reload, logout or exit. After a full scan the addon
	  offers a reload, so the sync program on your PC can upload the scan right away.

/ahsync          status
/ahsync scan     full scan (auction house window open)
/ahsync popup    toggle the reload popup after full scans
/ahsync clear    forget stored scans
]]

local ADDON = ...
local MAX_SCANS = 60 -- kept in the saved variables until the sync program has had a chance to read them
local MAX_AGE = 4 * 86400
local FULL_SCAN_MIN_ITEMS = 1000 -- searches cover a few items, a full scan thousands
local MAX_AUCTION_SCANS = 6 -- scans that keep their auction list (big; the sync reads it on the next reload)
local READ_STEP = 1000 -- auctions read per frame
local NAME_RETRIES = 3 -- times auctions without a seller name are read again
local NAME_RETRY_DELAY = 3 -- seconds between those reads
local GETALL_COOLDOWN = 15 * 60

local db
local hooked = {}
local ahOpen = false
local scanning = false -- our own scan is running
local reading = false -- ...and the server has answered, the auctions are being read
local feeding = false -- we are handing our scan to Auctionator (not a scan to record)
local mutedFrames -- frames whose AUCTION_ITEM_LIST_UPDATE is switched off during our scan
local button

local function Print(msg)
	print("|cff33ff99Auctionhouse Sync|r: " .. msg)
end

-- The modern auction house (C_AuctionHouse, full scan = "replicate") or the classic one (full scan = "get all" query)
local MODERN = C_AuctionHouse ~= nil and C_AuctionHouse.ReplicateItems ~= nil
local LIST_EVENT = MODERN and "REPLICATE_ITEM_LIST_UPDATE" or "AUCTION_ITEM_LIST_UPDATE"

local function NumAuctions()
	if MODERN then return C_AuctionHouse.GetNumReplicateItems() or 0 end
	return GetNumAuctionItems("list")
end

-- itemID, count, buyout, bid, seller, timeLeft (1 short .. 4 very long), hasAllInfo of auction i (from 1)
local function AuctionInfo(i)
	local _, count, buyout, bid, owner, ownerFull, itemID, hasAll, timeLeft
	if MODERN then
		_, _, count, _, _, _, _, _, _, buyout, bid, _, _, owner, ownerFull, _, itemID, hasAll = C_AuctionHouse.GetReplicateItemInfo(i - 1)
		timeLeft = C_AuctionHouse.GetReplicateItemTimeLeft(i - 1)
		timeLeft = timeLeft and timeLeft + 1 -- Enum.AuctionHouseTimeLeftBand starts at 0
	else
		_, _, count, _, _, _, _, _, _, buyout, bid, _, _, owner, ownerFull, _, itemID, hasAll = GetAuctionItemInfo("list", i)
		timeLeft = GetAuctionItemTimeLeft("list", i)
	end
	return itemID, count, buyout, bid, ownerFull or owner, timeLeft, hasAll
end

local function RealmKey()
	local ok, realm = pcall(Auctionator.Variables.GetConnectedRealmRoot)
	return ok and realm or GetRealmName()
end

-- "id:min:qty:auctions:median,..." from {itemID = {{price = unit price, available = quantity}, ...}}
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
	local kept = 0
	for i = #db.scans, 1, -1 do
		local s = db.scans[i]
		if s.auc then
			kept = kept + 1
			if kept > MAX_AUCTION_SCANS then s.auc, s.owners = nil, nil end
		end
	end
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

-- ---------------------------------------------------------------------------
-- Reading the auction list
-- ---------------------------------------------------------------------------

-- Reads every auction of the last full scan (the result stays available until the next one or search).
-- done(list, total) with list[i] = {itemID, count, buyout, seller or nil, timeLeft, bid}, or done(nil)
-- when the auction house closed or the list changed meanwhile.
local function ReadAuctions(done, progress)
	local total = NumAuctions()
	if not ahOpen or total == 0 then return done(nil) end
	local list = {}
	local function changed()
		return not ahOpen or NumAuctions() ~= total
	end
	local function read(i)
		local itemID, count, buyout, bid, owner, timeLeft, hasAll = AuctionInfo(i)
		if itemID and itemID > 0 then
			if owner == "" then owner = nil end
			list[i] = { itemID, count or 1, buyout or 0, owner, timeLeft or 0, bid or 0 }
			-- the seller is often only filled in once the client has the item's data
			if not owner and not hasAll and C_Item and C_Item.RequestLoadItemDataByID then pcall(C_Item.RequestLoadItemDataByID, itemID) end
		end
	end
	local function missing()
		local n = 0
		for i = 1, total do
			if not list[i] or not list[i][4] then n = n + 1 end
		end
		return n
	end
	local function retry(left)
		if left == 0 or missing() == 0 then return done(list, total) end
		if progress then progress(("Names (%d)"):format(missing())) end
		C_Timer.After(NAME_RETRY_DELAY, function()
			if changed() then return done(nil) end
			for i = 1, total do
				if not list[i] or not list[i][4] then read(i) end
			end
			retry(left - 1)
		end)
	end
	local first = 1
	local ticker
	ticker = C_Timer.NewTicker(0, function()
		if changed() then
			ticker:Cancel()
			return done(nil)
		end
		for i = first, math.min(first + READ_STEP - 1, total) do read(i) end
		first = first + READ_STEP
		if progress then progress(("Reading %d%%"):format(math.min(100, math.floor((first - 1) / total * 100)))) end
		if first > total then
			ticker:Cancel()
			retry(NAME_RETRIES)
		end
	end)
end

-- Builds and stores a full scan from a read auction list. Returns the scan and the per item prices.
local function RecordAuctions(list, total, src)
	local byItem, owners, ownerIndex, parts, named = {}, {}, {}, {}, 0
	for i = 1, total do
		local a = list[i]
		if a then
			local id, count, buyout, owner, timeLeft, bid = a[1], a[2], a[3], a[4], a[5], a[6]
			if buyout > 0 and count > 0 then
				local entries = byItem[id] or {}
				byItem[id] = entries
				entries[#entries + 1] = { price = math.ceil(buyout / count), available = count }
			end
			local o = 0
			if owner then
				named = named + 1
				o = ownerIndex[owner]
				if not o then
					owners[#owners + 1] = owner
					o = #owners
					ownerIndex[owner] = o
				end
			end
			parts[#parts + 1] = id .. ":" .. count .. ":" .. buyout .. ":" .. o .. ":" .. timeLeft .. ":" .. bid
		end
	end
	local data, n = Summarize(byItem)
	local scan = {
		t = time(), realm = RealmKey(), faction = UnitFactionGroup("player"), full = true, n = n, data = data,
		auc = table.concat(parts, ","), owners = table.concat(owners, ","), na = #parts, src = src,
		ah = MODERN and "modern" or "classic", -- the time left bands differ
	}
	table.insert(db.scans, scan)
	Prune()
	Print(("full scan saved: %d auctions of %d items, %d sellers (%d%% of auctions with a seller name). It is uploaded on your next /reload, logout or exit.")
		:format(#parts, n, #owners, #parts > 0 and math.floor(named / #parts * 100) or 0))
	if db.popup then StaticPopup_Show("AUCTIONHOUSESYNC_RELOAD", n) end
	return scan, byItem
end

-- Hands the scan's prices to Auctionator's price database, as its own full scan would
local function FeedAuctionator(byItem)
	local database = Auctionator and Auctionator.Database
	if not (database and database.ProcessScan) then return end
	local itemIndexes = {}
	for id, entries in pairs(byItem) do itemIndexes[tostring(id)] = entries end
	feeding = true
	local ok, err = pcall(database.ProcessScan, database, itemIndexes)
	feeding = false
	if not ok then Print("could not update Auctionator's prices: " .. tostring(err)) end
	pcall(function()
		if MODERN then
			Auctionator.SavedState.TimeOfLastReplicateScan = db.lastGetAll
		else
			Auctionator.SavedState.TimeOfLastGetAllScan = db.lastGetAll
		end
	end)
end

-- ---------------------------------------------------------------------------
-- Our own full scan
-- ---------------------------------------------------------------------------

local scanFrame = CreateFrame("Frame")

-- Seconds until the next full scan is allowed (ours or Auctionator's, whichever was last)
local function Cooldown()
	local last = db.lastGetAll or 0
	local state = Auctionator and Auctionator.SavedState
	if state then
		last = math.max(last, (MODERN and state.TimeOfLastReplicateScan or state.TimeOfLastGetAllScan) or 0)
	end
	return math.max(0, last + GETALL_COOLDOWN - time())
end

local function CanScan()
	if MODERN then return Cooldown() == 0 end -- the modern API does not tell
	local _, canGetAll = CanSendAuctionQuery()
	return canGetAll
end

local function UpdateButton(text)
	if not button then return end
	if scanning then
		button:SetText(text or "Scanning...")
		button:Disable()
		return
	end
	if CanScan() then
		button:SetText("Full Scan")
		button:Enable()
	else
		local left = Cooldown()
		button:SetText(left > 0 and ("Scan in %d:%02d"):format(math.floor(left / 60), left % 60) or "Full Scan")
		button:Disable()
	end
end

local function Unmute()
	scanFrame:UnregisterEvent(LIST_EVENT)
	for _, f in ipairs(mutedFrames or {}) do f:RegisterEvent("AUCTION_ITEM_LIST_UPDATE") end
	mutedFrames = nil
end

local function FinishScan(list, total)
	Unmute()
	scanning, reading = false, false
	if list then
		local _, byItem = RecordAuctions(list, total, "own")
		FeedAuctionator(byItem)
	else
		Print("scan interrupted: the auction house was closed or a new search was made. The next full scan is allowed 15 minutes after the last one.")
	end
	UpdateButton()
end

local function StartScan()
	if scanning then return Print("a scan is already running") end
	if not ahOpen then return Print("open the auction house first") end
	if not CanScan() then
		local left = Cooldown()
		return Print(left > 0 and ("the next full scan is allowed in %d:%02d"):format(math.floor(left / 60), left % 60)
			or "a full scan is not allowed right now (one every 15 minutes)")
	end
	scanning = true
	db.lastGetAll = time()
	scanFrame:RegisterEvent(LIST_EVENT)
	if MODERN then
		C_AuctionHouse.ReplicateItems()
	else
		-- other listeners (Blizzard's browse tab, Auctionator) would try to show all auctions at once
		mutedFrames = {}
		for _, f in ipairs({ GetFramesRegisteredForEvent("AUCTION_ITEM_LIST_UPDATE") }) do
			if f ~= scanFrame then
				f:UnregisterEvent("AUCTION_ITEM_LIST_UPDATE")
				mutedFrames[#mutedFrames + 1] = f
			end
		end
		if not ITEM_QUALITY_COLORS[-1] then ITEM_QUALITY_COLORS[-1] = { r = 0, g = 0, b = 0 } end -- classic AH code errors without it
		QueryAuctionItems("", nil, nil, 0, nil, nil, true, false, nil)
	end
	Print("full scan started, waiting for the server...")
	UpdateButton("Waiting...")
	local started = db.lastGetAll
	C_Timer.After(120, function()
		if scanning and not reading and db.lastGetAll == started then
			Print("no answer from the server")
			FinishScan(nil)
		end
	end)
end

scanFrame:SetScript("OnEvent", function(self, event)
	if event == LIST_EVENT and scanning then
		self:UnregisterEvent(LIST_EVENT)
		reading = true
		ReadAuctions(FinishScan, UpdateButton)
	end
end)

local function CreateButton()
	local parent = AuctionHouseFrame or AuctionFrame
	if button or not parent then return end
	button = CreateFrame("Button", "AuctionhouseSyncScanButton", parent, "UIPanelButtonTemplate")
	button:SetSize(120, 22)
	if parent == AuctionHouseFrame then
		button:SetPoint("TOPRIGHT", parent, "BOTTOMRIGHT", -4, -2) -- below the frame, right of the tabs
	else
		button:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -32, -14)
	end
	button:SetScript("OnClick", StartScan)
	button:SetScript("OnEnter", function(self)
		GameTooltip:SetOwner(self, "ANCHOR_BOTTOM")
		GameTooltip:SetText("Auctionhouse Sync")
		GameTooltip:AddLine("Scans every auction, with sellers, for the auction house website. Allowed once every 15 minutes.", 1, 1, 1, true)
		GameTooltip:Show()
	end)
	button:SetScript("OnLeave", GameTooltip_Hide)
	C_Timer.NewTicker(1, function()
		if ahOpen then UpdateButton() end
	end)
end

-- ---------------------------------------------------------------------------
-- Auctionator's own scans and searches
-- ---------------------------------------------------------------------------

local function OnProcessScan(_, itemIndexes)
	if feeding or scanning or type(itemIndexes) ~= "table" then return end
	local data, count = Summarize(itemIndexes)
	if count == 0 then return end
	local scan = { t = time(), realm = RealmKey(), faction = UnitFactionGroup("player"), full = false, n = count, data = data }
	if count < FULL_SCAN_MIN_ITEMS then -- a search
		table.insert(db.scans, scan)
		Prune()
		return
	end
	-- Auctionator's Full Scan: read the auctions it got, with sellers
	local state = Auctionator.SavedState
	db.lastGetAll = state and (MODERN and state.TimeOfLastReplicateScan or state.TimeOfLastGetAllScan) or time()
	ReadAuctions(function(list, total)
		if list then
			RecordAuctions(list, total, "auctionator")
		else
			scan.full = true
			table.insert(db.scans, scan)
			Prune()
			Print(("full scan saved: %d items (without the auction list, the auction house was closed too soon)"):format(count))
		end
	end)
end

local function HookDatabase()
	local database = Auctionator and Auctionator.Database
	if database and not hooked[database] and database.ProcessScan then
		hooksecurefunc(database, "ProcessScan", OnProcessScan)
		hooked[database] = true
	end
end

-- ---------------------------------------------------------------------------
-- Events and slash command
-- ---------------------------------------------------------------------------

local frame = CreateFrame("Frame")
frame:RegisterEvent("ADDON_LOADED")
frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("AUCTION_HOUSE_SHOW")
frame:RegisterEvent("AUCTION_HOUSE_CLOSED")
frame:SetScript("OnEvent", function(_, event, name)
	if event == "AUCTION_HOUSE_SHOW" then
		ahOpen = true
		CreateButton()
		UpdateButton()
	elseif event == "AUCTION_HOUSE_CLOSED" then
		ahOpen = false
		if scanning and not reading then
			FinishScan(nil)
		elseif scanning then
			Unmute() -- the read in progress notices and ends the scan
		end
	elseif event == "ADDON_LOADED" and name == ADDON then
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
	end
end)

SLASH_AUCTIONHOUSESYNC1 = "/ahsync"
SlashCmdList["AUCTIONHOUSESYNC"] = function(msg)
	local cmd = (msg or ""):lower():match("^(%S*)")
	if cmd == "scan" then
		StartScan()
	elseif cmd == "popup" then
		db.popup = not db.popup
		Print("reload popup after full scans: " .. (db.popup and "on" or "off"))
	elseif cmd == "clear" then
		wipe(db.scans)
		Print("stored scans cleared")
	else
		local full, latest = 0, nil
		for _, s in ipairs(db.scans) do
			if s.full then full = full + 1 end
			if s.na then latest = s end
		end
		Print(("%d scans stored (%d full), popup: %s"):format(#db.scans, full, db.popup and "on" or "off"))
		if latest then
			local sellers = latest.owners ~= "" and select(2, latest.owners:gsub(",", ",")) + 1 or 0
			Print(("latest auction list: %d auctions, %d sellers (%s)"):format(latest.na, sellers, date("%H:%M", latest.t)))
		end
		Print("/ahsync scan | /ahsync popup | /ahsync clear")
	end
end
