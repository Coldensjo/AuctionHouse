"""Publishing the built site to the gh-pages branch, and restoring local state from it.

site/ is its own git checkout of gh-pages. Every publish replaces that branch's single commit
(commit --amend + force push), so many uploads a day do not grow the repository. Git only
uploads the files that changed. A copy of state/ is published under _state/ as a backup; on a
fresh machine restore_state() brings it back.
"""
import datetime, io, os, shutil, subprocess, tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")
STATE = os.path.join(ROOT, "state")
BRANCH = "gh-pages"
BACKUP = "_state" # inside the published site

# On Windows every git call would otherwise open (and flash) its own console window when the sync runs hidden.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

def git(*args, cwd=ROOT, check=True, binary=False):
	r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=not binary, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
	if check and r.returncode:
		err = r.stderr.decode(errors="replace") if binary else r.stderr
		raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
	return r

def remote_url():
	return git("remote", "get-url", "origin").stdout.strip()

def ensure_site_repo():
	"""Makes site/ a git checkout of gh-pages (without disturbing files already there)."""
	os.makedirs(SITE, exist_ok=True)
	if os.path.isdir(os.path.join(SITE, ".git")):
		return
	git("init", "-q", "-b", BRANCH, cwd=SITE)
	git("remote", "add", "origin", remote_url(), cwd=SITE)
	# Start from the published branch if there is one, so the first push reuses its objects.
	if git("fetch", "-q", "--depth", "1", "origin", BRANCH, cwd=SITE, check=False).returncode == 0:
		git("reset", "-q", "--soft", "FETCH_HEAD", cwd=SITE)

def publish():
	"""Commits site/ as the only commit of gh-pages and force-pushes it. Returns False if nothing changed."""
	ensure_site_repo()
	git("add", "-A", cwd=SITE)
	has_head = git("rev-parse", "--verify", "-q", "HEAD", cwd=SITE, check=False).returncode == 0
	if has_head and git("diff", "--cached", "--quiet", cwd=SITE, check=False).returncode == 0:
		return False
	msg = f"Auction data {datetime.datetime.now():%Y-%m-%d %H:%M}"
	if has_head:
		git("commit", "-q", "--amend", "-m", msg, cwd=SITE)
	else:
		git("commit", "-q", "-m", msg, cwd=SITE)
	git("push", "-q", "--force", "origin", f"HEAD:{BRANCH}", cwd=SITE)
	# The replaced commits are unreachable now; drop them so site/.git stays small.
	git("reflog", "expire", "--expire=now", "--all", cwd=SITE, check=False)
	git("gc", "-q", "--prune=now", cwd=SITE, check=False)
	return True

def restore_state():
	"""If state/ is empty (fresh clone), restores the archive, caches and icons from the published branch."""
	if os.path.isdir(os.path.join(STATE, "archive")):
		return False
	if git("fetch", "-q", "--depth", "1", "origin", BRANCH, check=False).returncode:
		return False
	tar = git("archive", "--format=tar", "FETCH_HEAD", binary=True, check=False)
	if tar.returncode:
		return False
	restored = False
	with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as t:
		for m in t.getmembers():
			if not m.isfile():
				continue
			if m.name.startswith(BACKUP + "/"):
				dest = os.path.join(STATE, m.name[len(BACKUP) + 1:])
			elif m.name.startswith("icons/"):
				dest = os.path.join(STATE, m.name)
			else:
				continue
			if ".." in m.name.split("/"):
				continue
			os.makedirs(os.path.dirname(dest), exist_ok=True)
			with t.extractfile(m) as src, open(dest, "wb") as out:
				shutil.copyfileobj(src, out)
			restored = True
	return restored
