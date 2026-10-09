"""Readers for WoW SavedVariables (Lua table literals) and CBOR blobs (Auctionator's realm format)."""
import re, struct

_ESC = {"n": 10, "r": 13, "t": 9, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}
_NUM = re.compile(rb"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")

class _Parser:
	def __init__(self, data):
		self.s, self.i = data, 0

	def ws(self):
		s, n = self.s, len(self.s)
		while self.i < n:
			c = s[self.i]
			if c in b" \t\r\n,;":
				self.i += 1
			elif s.startswith(b"--", self.i):
				j = s.find(b"\n", self.i)
				self.i = n if j < 0 else j + 1
			else:
				break

	def value(self):
		self.ws()
		s, c = self.s, self.s[self.i:self.i + 1]
		if c == b"{":
			return self.table()
		if c in (b'"', b"'"):
			return self.string()
		for word, val in ((b"true", True), (b"false", False), (b"nil", None)):
			if s.startswith(word, self.i):
				self.i += len(word)
				return val
		m = _NUM.match(s, self.i)
		if not m:
			raise ValueError(f"unexpected {s[self.i:self.i + 20]!r} at {self.i}")
		self.i = m.end()
		t = m.group().decode()
		if "x" in t.lower():
			return int(t, 16)
		return float(t) if any(ch in t for ch in ".eE") else int(t)

	# Returns bytes: Lua strings are byte strings and Auctionator stores binary CBOR in them.
	def string(self):
		s, q = self.s, self.s[self.i]
		self.i += 1
		out = bytearray()
		while True:
			c = s[self.i]
			if c == q:
				self.i += 1
				return bytes(out)
			if c == 92:
				e = chr(s[self.i + 1])
				if e.isdigit():
					m = re.match(rb"\d{1,3}", s[self.i + 1:self.i + 4])
					out.append(int(m.group()))
					self.i += 1 + len(m.group())
				else:
					out.append(_ESC.get(e, ord(e)))
					self.i += 2
			else:
				out.append(c)
				self.i += 1

	def table(self):
		self.i += 1
		arr, d, idx = [], {}, 1
		while True:
			self.ws()
			if self.s[self.i:self.i + 1] == b"}":
				self.i += 1
				break
			if self.s[self.i:self.i + 1] == b"[":
				self.i += 1
				k = self.value()
				self.ws()
				self.i += 1 # ]
				self.ws()
				self.i += 1 # =
				d[_key(k)] = self.value()
			else:
				d[idx] = self.value()
				idx += 1
		return d

def _key(k):
	return k.decode("utf-8", "replace") if isinstance(k, bytes) else k

def load(path):
	"""Returns {globalName: value} for every top-level assignment in a SavedVariables file."""
	with open(path, "rb") as f:
		p = _Parser(f.read())
	out = {}
	while True:
		p.ws()
		m = re.compile(rb"([A-Za-z_][A-Za-z0-9_]*)\s*=").match(p.s, p.i)
		if not m:
			break
		p.i = m.end()
		out[m.group(1).decode()] = p.value()
	return out

def text(v):
	return v.decode("utf-8", "replace") if isinstance(v, bytes) else v

def cbor(data):
	"""Minimal CBOR decoder (what WoW's C_EncodingUtil.SerializeCBOR and LibCBOR emit)."""
	val, _ = _cbor(data, 0)
	return val

def _cbor(b, i):
	ib = b[i]
	major, info = ib >> 5, ib & 31
	i += 1
	if major == 7:
		if info == 20: return False, i
		if info == 21: return True, i
		if info in (22, 23): return None, i
		if info == 25: return _half(b[i:i + 2]), i + 2
		if info == 26: return struct.unpack(">f", b[i:i + 4])[0], i + 4
		if info == 27: return struct.unpack(">d", b[i:i + 8])[0], i + 8
		raise ValueError(f"cbor simple {info}")
	if info < 24: n = info
	elif info == 24: n, i = b[i], i + 1
	elif info == 25: n, i = int.from_bytes(b[i:i + 2], "big"), i + 2
	elif info == 26: n, i = int.from_bytes(b[i:i + 4], "big"), i + 4
	elif info == 27: n, i = int.from_bytes(b[i:i + 8], "big"), i + 8
	elif info == 31: n = None # indefinite length
	else: raise ValueError(f"cbor info {info}")
	if major == 0: return n, i
	if major == 1: return -1 - n, i
	if major in (2, 3):
		if n is None:
			parts = bytearray()
			while b[i] != 0xFF:
				part, i = _cbor(b, i)
				parts += part if isinstance(part, bytes) else part.encode()
			raw, i = bytes(parts), i + 1
		else:
			raw, i = b[i:i + n], i + n
		return (raw.decode("utf-8", "replace") if major == 3 else raw), i
	if major == 4:
		out = []
		while (n is None and b[i] != 0xFF) or (n is not None and len(out) < n):
			v, i = _cbor(b, i)
			out.append(v)
		return out, i + (1 if n is None else 0)
	if major == 5:
		out, c = {}, 0
		while (n is None and b[i] != 0xFF) or (n is not None and c < n):
			k, i = _cbor(b, i)
			v, i = _cbor(b, i)
			out[k if not isinstance(k, bytes) else k.decode("utf-8", "replace")] = v
			c += 1
		return out, i + (1 if n is None else 0)
	if major == 6:
		return _cbor(b, i)
	raise ValueError(f"cbor major {major}")

def _half(h):
	v = int.from_bytes(h, "big")
	e, m = (v >> 10) & 31, v & 1023
	val = m * 2 ** -24 if e == 0 else (float("inf") if e == 31 else (m + 1024) * 2 ** (e - 25))
	return -val if v & 0x8000 else val
