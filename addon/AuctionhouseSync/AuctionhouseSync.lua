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
	* The modern auction house leaves other players' names out of the full scan, but item searches
	  have them: Scan Sellers searches every item one by one in the background (slow, throttled).
	* The prices are also handed to Auctionator, so its tooltips stay current.
	* Auctionator's own searches are recorded too (hooked on its price database). When its Full Scan
	  button is used instead of ours, the result is read the same way.
	* WoW only writes saved variables on /reload, logout or exit. After a full scan the addon
	  offers a reload, so the sync program on your PC can upload the scan right away.

/ahsync          status
/ahsync scan     full scan (auction house window open)
/ahsync sellers  start, pause or resume the seller scan (modern auction house)
/ahsync popup    toggle the reload popup after full scans
/ahsync resetbuttons  put the (draggable) buttons back in their default place
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
		timeLeft = C_AuctionHouse.GetReplicateItemTimeLeft(i - 1) -- 1 short .. 4 very long, like the classic API
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
-- Seller scan (modern auction house). The full scan does not name other players' auctions;
-- searches do. So this searches every item of the latest full scan one by one, most valuable
-- first. The server throttles searches, so it is slow and runs in the background while the
-- auction house is open, and resumes where it left off.
-- db.crawl = {started, realm, faction, queue = {itemIDs}, pos, results = {[itemID] = {t, rows}}}
-- rows: "quantity:unit price:seller/seller:number of sellers;..." (commodity rows can have several)
-- ---------------------------------------------------------------------------

local CRAWL_TIMEOUT = 10 -- seconds to wait for a search before skipping the item
local CRAWL_MAX_PAGES = 3 -- extra result pages requested per item
local CRAWL_FRESH = 12 * 3600 -- a seller scan started longer ago than this starts over
local CRAWL_KEEP = 3 -- finished seller scans kept for the sync program
local crawling = false
local crawlItem, crawlKey, crawlSent, crawlPages
local crawlButton
local crawlFrame = CreateFrame("Frame")
local CRAWL_EVENTS = { "AUCTION_HOUSE_THROTTLED_SYSTEM_READY", "ITEM_SEARCH_RESULTS_UPDATED", "COMMODITY_SEARCH_RESULTS_UPDATED" }

StaticPopupDialogs["AUCTIONHOUSESYNC_CRAWL_DONE"] = {
	text = "Auctionhouse Sync\n\nSeller scan finished (%s items).\nReload the UI now so it can be uploaded to the website?",
	button1 = "Reload & Upload",
	button2 = "Later",
	OnAccept = function() ReloadUI() end,
	timeout = 0,
	whileDead = true,
	hideOnEscape = true,
	preferredIndex = 3,
}

-- Item IDs of the newest full scan, most valuable first
local function CrawlQueue()
	local latest
	for i = #db.scans, 1, -1 do
		if db.scans[i].auc then
			latest = db.scans[i]
			break
		end
	end
	if not latest then return nil end
	local value, ids = {}, {}
	for part in latest.auc:gmatch("[^,]+") do
		local id, _, buyout = part:match("^(%d+):(%d+):(%d+):")
		if id then
			id = tonumber(id)
			if not value[id] then ids[#ids + 1] = id end
			value[id] = (value[id] or 0) + tonumber(buyout)
		end
	end
	table.sort(ids, function(a, b) return value[a] > value[b] end)
	return ids
end

local function UpdateCrawlButton()
	if not crawlButton then return end
	local c = db.crawl
	if crawling then
		crawlButton:SetText(("Stop (%d/%d)"):format(c.pos - 1, #c.queue))
	elseif c and c.pos <= #c.queue and time() - c.started <= CRAWL_FRESH then
		crawlButton:SetText(("Resume (%d/%d)"):format(c.pos - 1, #c.queue))
	else
		crawlButton:SetText("Scan Sellers")
	end
	if scanning then crawlButton:Disable() else crawlButton:Enable() end
end

local CrawlNext

local function CrawlStop(why)
	crawling, crawlSent = false, nil
	FrameUtil.UnregisterFrameForEvents(crawlFrame, CRAWL_EVENTS)
	local c = db.crawl
	if c and c.pos > #c.queue then -- finished: keep it for the sync program
		db.crawlDone = db.crawlDone or {}
		table.insert(db.crawlDone, { started = c.started, realm = c.realm, faction = c.faction, results = c.results })
		while #db.crawlDone > CRAWL_KEEP do table.remove(db.crawlDone, 1) end
		db.crawl = nil
		Print(("seller scan finished: %d items. It is uploaded on your next /reload, logout or exit."):format(#c.queue))
		if db.popup then StaticPopup_Show("AUCTIONHOUSESYNC_CRAWL_DONE", #c.queue) end
	elseif why then
		Print(("seller scan %s at %d of %d items. Click Resume (or /ahsync sellers) to go on."):format(why, c.pos - 1, #c.queue))
	end
	UpdateCrawlButton()
end

-- Records the item's search results and moves on
local function CrawlDone(rows)
	local c = db.crawl
	if rows then c.results[crawlItem] = { t = time(), r = rows } end
	c.pos = c.pos + 1
	crawlSent = nil
	UpdateCrawlButton()
	C_Timer.After(0.05, CrawlNext)
end

local function CrawlResults(commodity)
	local full
	if commodity then full = C_AuctionHouse.HasFullCommoditySearchResults(crawlItem) else full = C_AuctionHouse.HasFullItemSearchResults(crawlKey) end
	if not full and crawlPages < CRAWL_MAX_PAGES then
		crawlPages = crawlPages + 1
		if commodity then C_AuctionHouse.RequestMoreCommoditySearchResults(crawlItem) else C_AuctionHouse.RequestMoreItemSearchResults(crawlKey) end
		return -- the results event comes again
	end
	local me = UnitName("player")
	local n = commodity and C_AuctionHouse.GetNumCommoditySearchResults(crawlItem) or C_AuctionHouse.GetNumItemSearchResults(crawlKey)
	local rows = {}
	for i = 1, n or 0 do
		local r
		if commodity then r = C_AuctionHouse.GetCommoditySearchResultInfo(crawlItem, i) else r = C_AuctionHouse.GetItemSearchResultInfo(crawlKey, i) end
		if r then
			local owners = {}
			for _, o in ipairs(r.owners or {}) do owners[#owners + 1] = (o == "player") and me or o end
			local qty = r.quantity or 1
			local unit = r.unitPrice or (r.buyoutAmount and math.ceil(r.buyoutAmount / math.max(1, qty))) or 0
			rows[#rows + 1] = qty .. ":" .. unit .. ":" .. table.concat(owners, "/") .. ":" .. (r.totalNumberOfOwners or #owners)
		end
	end
	CrawlDone(table.concat(rows, ";"))
end

CrawlNext = function()
	if not crawling or crawlSent then return end
	local c = db.crawl
	if not ahOpen then return CrawlStop("paused (auction house closed)") end
	if c.pos > #c.queue then return CrawlStop() end
	if not C_AuctionHouse.IsThrottledMessageSystemReady() then return end -- AUCTION_HOUSE_THROTTLED_SYSTEM_READY calls again
	crawlItem, crawlPages = c.queue[c.pos], 0
	crawlKey = C_AuctionHouse.MakeItemKey(crawlItem)
	local sent = GetTime()
	crawlSent = sent
	C_AuctionHouse.SendSearchQuery(crawlKey, { { sortOrder = Enum.AuctionHouseSortOrder.Price, reverseSort = false } }, true)
	C_Timer.After(CRAWL_TIMEOUT, function()
		if crawling and crawlSent == sent then CrawlDone(nil) end -- no answer: skip the item
	end)
end

crawlFrame:SetScript("OnEvent", function(_, event, arg)
	if not crawling then return end
	if event == "AUCTION_HOUSE_THROTTLED_SYSTEM_READY" then
		CrawlNext()
	elseif event == "ITEM_SEARCH_RESULTS_UPDATED" then
		if crawlSent and type(arg) == "table" and arg.itemID == crawlItem then CrawlResults(false) end
	elseif event == "COMMODITY_SEARCH_RESULTS_UPDATED" then
		if crawlSent and arg == crawlItem then CrawlResults(true) end
	end
end)

local function CrawlStart()
	if crawling then return CrawlStop("stopped") end
	if not MODERN then return Print("not needed here: on this auction house the full scan already has the sellers") end
	if not ahOpen then return Print("open the auction house first") end
	if scanning then return Print("wait for the full scan to finish") end
	local c = db.crawl
	if not c or c.pos > #c.queue or time() - c.started > CRAWL_FRESH then
		local queue = CrawlQueue()
		if not queue then return Print("run a full scan first: the seller scan searches the items it found") end
		c = { started = time(), realm = RealmKey(), faction = UnitFactionGroup("player"), queue = queue, pos = 1, results = {} }
		db.crawl = c
	end
	crawling = true
	FrameUtil.RegisterFrameForEvents(crawlFrame, CRAWL_EVENTS)
	Print(("seller scan: %d of %d items to go, most valuable first. It runs while the auction house is open; click Stop to pause.")
		:format(#c.queue - c.pos + 1, #c.queue))
	UpdateCrawlButton()
	CrawlNext()
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
	UpdateCrawlButton()
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
	if crawling then CrawlStop("paused for the full scan") end
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

-- The buttons sit together in a holder that can be dragged anywhere (the position is remembered)
local holder

local function PlaceButtons()
	local parent = holder:GetParent()
	holder:ClearAllPoints()
	if db.buttonPos then
		holder:SetPoint("TOPLEFT", parent, "TOPLEFT", db.buttonPos[1], db.buttonPos[2])
	elseif parent == AuctionHouseFrame then
		holder:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -26, -1) -- the title bar, left of the close button
	else
		holder:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -32, -14)
	end
end

local function MakeButton(name, width, onClick, title, text)
	local b = CreateFrame("Button", name, holder, "UIPanelButtonTemplate")
	b:SetSize(width, 22)
	b:SetScript("OnClick", onClick)
	b:RegisterForDrag("LeftButton")
	b:SetScript("OnDragStart", function() holder:StartMoving() end)
	b:SetScript("OnDragStop", function()
		holder:StopMovingOrSizing()
		local parent = holder:GetParent()
		db.buttonPos = { math.floor(holder:GetLeft() - parent:GetLeft() + 0.5), math.floor(holder:GetTop() - parent:GetTop() + 0.5) }
		PlaceButtons()
	end)
	b:SetScript("OnEnter", function(self)
		GameTooltip:SetOwner(self, "ANCHOR_BOTTOM")
		GameTooltip:SetText(title)
		GameTooltip:AddLine(text, 1, 1, 1, true)
		GameTooltip:AddLine("Drag to move the buttons. /ahsync resetbuttons puts them back.", 0.6, 0.6, 0.6, true)
		GameTooltip:Show()
	end)
	b:SetScript("OnLeave", GameTooltip_Hide)
	return b
end

local function CreateButton()
	local parent = AuctionHouseFrame or AuctionFrame
	if button or not parent then return end
	holder = CreateFrame("Frame", "AuctionhouseSyncButtons", parent)
	holder:SetSize(MODERN and 254 or 120, 22)
	holder:SetMovable(true)
	holder:SetClampedToScreen(true)
	holder:SetFrameStrata("HIGH")
	PlaceButtons()
	button = MakeButton("AuctionhouseSyncScanButton", 120, StartScan, "Auctionhouse Sync",
		"Scans every auction for the auction house website. Allowed once every 15 minutes.")
	button:SetPoint("RIGHT", holder, "RIGHT")
	if MODERN then
		crawlButton = MakeButton("AuctionhouseSyncSellersButton", 130, CrawlStart, "Scan Sellers",
			"The full scan does not include other players' names. This searches every item one by one to find who sells what. Slow (the server limits searches): it runs while the auction house is open and can be paused and resumed.")
		crawlButton:SetPoint("RIGHT", button, "LEFT", -4, 0)
	end
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
		if crawling then CrawlStop("paused (auction house closed)") end
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
	elseif cmd == "sellers" then
		CrawlStart()
	elseif cmd == "resetbuttons" then
		db.buttonPos = nil
		if holder then PlaceButtons() end
		Print("buttons moved back to their default place")
	elseif cmd == "popup" then
		db.popup = not db.popup
		Print("reload popup after full scans: " .. (db.popup and "on" or "off"))
	elseif cmd == "clear" then
		wipe(db.scans)
		db.crawl, db.crawlDone = nil, nil
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
		if db.crawl then Print(("seller scan in progress: %d of %d items"):format(db.crawl.pos - 1, #db.crawl.queue)) end
		if db.crawlDone and #db.crawlDone > 0 then Print(("finished seller scans waiting to upload: %d"):format(#db.crawlDone)) end
		Print("/ahsync scan | /ahsync sellers | /ahsync popup | /ahsync clear")
	end
end
