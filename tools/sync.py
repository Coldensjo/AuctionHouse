"""Auction house sync: uploads your auction data every time WoW saves it.

Run it and leave it running (sync.bat shows a window; install-autostart.bat starts it hidden at login):
	python tools/sync.py            watch and upload
	python tools/sync.py --once     export and upload once, then exit

WoW only writes addon data to disk on /reload, logout or exit, so that is when a scan becomes visible
here. The AuctionhouseSync addon (installed into WoW by this program) offers a reload right after
every full scan, so a scan is uploaded seconds after it finishes.
"""
import argparse, filecmp, glob, logging, logging.handlers, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export

ADDON_SRC = os.path.join(export.ROOT, "addon", "AuctionhouseSync")
LOG_FILE = os.path.join(export.STATE, "sync.log")
WATCHED = ("Auctionator", "AuctionhouseSync", "DisenchantValue")
SETTLE_SECONDS = 3 # wait until WoW has finished writing
MAX_RETRY_SECONDS = 300

log = logging.getLogger("sync")

def setup_logging():
	os.makedirs(export.STATE, exist_ok=True)
	log.setLevel(logging.INFO)
	fmt = logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S")
	fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
	fh.setFormatter(fmt)
	log.addHandler(fh)
	if sys.stdout: # not under pythonw
		sh = logging.StreamHandler(sys.stdout)
		sh.setFormatter(fmt)
		log.addHandler(sh)

	# export.py and wowhead.py report with print(); send that to the log too
	class ToLog:
		def __init__(self, level):
			self.level, self.buf = level, ""
		def write(self, s):
			self.buf += s
			while "\n" in self.buf:
				line, self.buf = self.buf.split("\n", 1)
				if line.strip():
					log.log(self.level, line.rstrip())
		def flush(self):
			pass
	sys.stdout, sys.stderr = ToLog(logging.INFO), ToLog(logging.WARNING)

def single_instance():
	"""Keeps a lock on state/sync.lock while running; returns False if another sync already holds it."""
	path = os.path.join(export.STATE, "sync.lock")
	global _lock
	_lock = open(path, "a+")
	try:
		import msvcrt
		msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
	except ImportError:
		import fcntl
		try:
			fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
		except OSError:
			return False
	except OSError:
		return False
	return True

def install_addon(cfg):
	"""Copies the AuctionhouseSync addon into WoW's AddOns folder when it is missing or out of date."""
	dest = os.path.join(cfg["wow_path"], "Interface", "AddOns", "AuctionhouseSync")
	if not os.path.isdir(os.path.dirname(dest)):
		log.warning(f"WoW AddOns folder not found under {cfg['wow_path']}; check wow_path in config.json")
		return
	changed = False
	os.makedirs(dest, exist_ok=True)
	for f in os.listdir(ADDON_SRC):
		src, dst = os.path.join(ADDON_SRC, f), os.path.join(dest, f)
		if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
			shutil.copy2(src, dst)
			changed = True
	if changed:
		log.info("installed/updated the AuctionhouseSync addon in WoW (takes effect after /reload or restarting WoW)")

def signature(cfg):
	sig = {}
	for addon in WATCHED:
		for path in export.saved_variables(cfg, addon):
			try:
				st = os.stat(path)
				sig[path] = (st.st_mtime, st.st_size)
			except OSError:
				pass
	return sig

def sync_once(cfg):
	try:
		export.run(cfg, fetch=True, push=bool(cfg.get("push")))
		return True
	except SystemExit as e:
		log.warning(str(e))
	except Exception as e:
		log.exception(f"sync failed: {e}")
	return False

def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--once", action="store_true", help="export and upload once, then exit")
	args = ap.parse_args()
	setup_logging()
	if not single_instance():
		log.info("another sync is already running; exiting")
		return
	cfg = export.load_config()
	install_addon(cfg)
	ok = sync_once(cfg)
	if args.once:
		return
	interval = max(2, int(cfg.get("check_interval_seconds") or 10))
	log.info(f"watching for new scans every {interval}s (WoW saves them on /reload, logout or exit)")
	seen = signature(cfg)
	retry_at, backoff = (0 if ok else time.time() + interval), interval
	pending = not ok
	while True:
		time.sleep(interval)
		now = signature(cfg)
		if now != seen:
			while True: # wait until the files stop changing
				time.sleep(SETTLE_SECONDS)
				settled = signature(cfg)
				if settled == now:
					break
				now = settled
			seen = now
			pending = True
			retry_at = 0
			log.info("new data from WoW")
		if pending and time.time() >= retry_at:
			cfg = export.load_config()
			if sync_once(cfg):
				pending, backoff = False, interval
			else:
				backoff = min(backoff * 2, MAX_RETRY_SECONDS)
				retry_at = time.time() + backoff
				log.info(f"will retry in {backoff}s")

if __name__ == "__main__":
	main()
