"""Flixcloud (flixcloud.cc) embed -> HLS master.m3u8 resolver. Pure Python, no browser.

Usage:
    from anime_sh.resolvers.flixcloud.crypto import resolve
    info = resolve("https://flixcloud.cc/e/<id>?v=1", fetch)   # fetch(url, headers) -> (status, text)
    info["url"], info["headers"], info["subtitles"]

How the site hides the playlist URL (reverse-engineered from the SvelteKit player chunk
``nodes/12.*.js``; functions Ta / _a / Ea / xa there):

 1. The embed HTML inlines the SvelteKit page data as a JS object literal (``kit.start(...,
    {data:[null,null,{type:"data",data:{...}}]})``) -- NOT JSON (unquoted keys), so a tiny JS
    literal parser is used here.
 2. data.obfuscation_seed (SEED, per request) drives a SHA-256 chain that names every field:
        e = SEED; repeat 3x: e = sha256hex(e + str(i))        # i = 0,1,2
        a = e;    repeat 3x: a = sha256hex(a + str(i))
        keyField  = "kf_"  + e[8:16]     ivField    = "ivf_" + e[16:24]
        container = "cd_"  + e[24:32]    arrayName  = "ad_"  + e[32:40]   objectName = "od_" + e[40:48]
        tokenField= e[48:64] + "_" + e[56:64]            (value = one-shot token id)
        keyFrag2  = a[0:16]  + "_" + a[16:24]            (base64 key fragment 2)
    data.obfuscated_crypto_data[container][arrayName][0][objectName] = {keyField: frag1_b64,
    ivField: iv_b64, ...decoys}.
 3. GET {origin}/api/m3u8/{token}  (single use; no cookies/referer needed) -> JSON with
        ct_b64 = resp[sha256hex(token+"vid")[:10]]   (AES-CBC ciphertext of the playlist URL)
        k3_b64 = resp[sha256hex(token+"key")[:10]]   (key fragment 3)
 4. data.w_payload is a tiny per-request *randomised* WebAssembly module (exports _s(seed),
    _r(a,b,c,out,len)); it mixes frag1/frag2/frag3 byte-wise into a 32 byte value.  The op chain
    and constants differ on every page load, so we do NOT port it - ``_MiniWasm`` below is a small
    interpreter for the i32/memory/control-flow subset such modules use, and runs the payload as-is.
        seed_i32 = int(SEED[:8], 16);  mem[1000..]=frag1, mem[1000+n..]=frag2, mem[1000+2n..]=frag3
        _s(seed_i32); _r(1000, 1000+n, 1000+2n, 1000+3n, n);  F = mem[1000+3n : 1000+4n]
 5. K0 = PBKDF2-HMAC-SHA256(F, salt=SEED.encode(), 1000 iters, 32 bytes);
    K0[i] ^= ord(SEED[i % len(SEED)]);  AESKEY = sha256(K0);  url = AES-256-CBC-decrypt(AESKEY, iv,
    ct) with PKCS7 padding.

Static (hard-coded; would break if the site changes the scheme): the field-name derivation in (2),
the ``/api/m3u8/`` path, the "vid"/"key" suffix + [:10], the PBKDF2/XOR/SHA-256 key schedule in (5),
the WASM calling convention in (4) (memory offset 1000, export names).
Dynamic (read from the page every call): seed, all field names, token, wasm bytes, IV, fragments.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
from collections.abc import Callable
from urllib.parse import urlsplit

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"

Fetch = Callable[[str, dict], "tuple[int, str]"]


class FlixcloudError(RuntimeError):
    pass


# --------------------------------------------------------------------------- JS literal parser
_WS = " \t\r\n"
_ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0",
        '"': '"', "'": "'", "\\": "\\", "/": "/"}


class _JS:
    """Parser for the JS object-literal subset emitted by SvelteKit's devalue-less serializer."""

    def __init__(self, s: str, i: int = 0):
        self.s, self.i = s, i

    def ws(self):
        s = self.s
        while self.i < len(s) and s[self.i] in _WS:
            self.i += 1

    def value(self):
        self.ws()
        s, c = self.s, self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "\"'":
            return self.string()
        m = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?").match(s, self.i)
        if m:
            self.i = m.end()
            f = float(m.group(0))
            return int(f) if f.is_integer() and "." not in m.group(0) and "e" not in m.group(0).lower() else f
        for lit, val in (("true", True), ("false", False), ("null", None), ("undefined", None),
                         ("void 0", None), ("!0", True), ("!1", False), ("NaN", None),
                         ("Infinity", float("inf")), ("-Infinity", float("-inf"))):
            if s.startswith(lit, self.i):
                self.i += len(lit)
                return val
        raise FlixcloudError(f"unsupported JS literal at {self.i}: {s[self.i:self.i + 40]!r}")

    def string(self):
        s, q = self.s, self.s[self.i]
        self.i += 1
        out = []
        while True:
            c = s[self.i]
            if c == q:
                self.i += 1
                return "".join(out)
            if c == "\\":
                n = s[self.i + 1]
                if n == "u":
                    out.append(chr(int(s[self.i + 2:self.i + 6], 16)))
                    self.i += 6
                elif n == "x":
                    out.append(chr(int(s[self.i + 2:self.i + 4], 16)))
                    self.i += 4
                else:
                    out.append(_ESC.get(n, n))
                    self.i += 2
            else:
                out.append(c)
                self.i += 1

    def key(self):
        self.ws()
        if self.s[self.i] in "\"'":
            return self.string()
        m = re.compile(r"[A-Za-z0-9_$]+").match(self.s, self.i)
        if not m:
            raise FlixcloudError(f"bad object key at {self.i}")
        self.i = m.end()
        return m.group(0)

    def obj(self):
        self.i += 1
        d = {}
        while True:
            self.ws()
            if self.s[self.i] == "}":
                self.i += 1
                return d
            k = self.key()
            self.ws()
            if self.s[self.i] != ":":
                raise FlixcloudError(f"expected ':' at {self.i}")
            self.i += 1
            d[k] = self.value()
            self.ws()
            if self.s[self.i] == ",":
                self.i += 1

    def arr(self):
        self.i += 1
        a = []
        while True:
            self.ws()
            if self.s[self.i] == "]":
                self.i += 1
                return a
            a.append(self.value())
            self.ws()
            if self.s[self.i] == ",":
                self.i += 1


def parse_page_data(html: str) -> dict:
    """Return the page-data dict (the node that holds obfuscated_crypto_data)."""
    m = re.search(r"\bdata\s*:\s*\[\s*null\s*,", html) or re.search(r"\bnode_ids\s*:\s*\[[^\]]*\]\s*,\s*data\s*:\s*\[", html)
    if not m:
        raise FlixcloudError("SvelteKit page data not found in embed HTML")
    start = html.index("[", m.start(), m.end() + 1) if html[m.start():m.end()].count("[") else m.end()
    nodes = _JS(html, start).value()
    for n in nodes:
        if isinstance(n, dict):
            d = n.get("data")
            if isinstance(d, dict) and "obfuscated_crypto_data" in d:
                return d
    raise FlixcloudError("no node with obfuscated_crypto_data (page changed? error page?)")


# --------------------------------------------------------------------------- mini WebAssembly
class _MiniWasm:
    """Minimal WASM MVP interpreter: i32 only, one memory, globals, structured control flow."""

    M32 = 0xFFFFFFFF

    def __init__(self, blob: bytes):
        if blob[:4] != b"\0asm":
            raise FlixcloudError("w_payload is not wasm")
        self.types, self.funcs, self.exports = [], [], {}
        self.globals, self.mem, self.code, self.data = [], bytearray(), [], []
        p = 8
        while p < len(blob):
            sid = blob[p]
            size, p = self._u(blob, p + 1)
            body, p = blob[p:p + size], p + size
            self._section(sid, body)
        # data segments (active, memory 0)
        for off, bs in self.data:
            self.mem[off:off + len(bs)] = bs
        self.fns = [self._decode(c) for c in self.code]

    # LEB helpers
    @staticmethod
    def _u(b, p):
        r = s = 0
        while True:
            x = b[p]
            p += 1
            r |= (x & 0x7F) << s
            s += 7
            if not x & 0x80:
                return r, p

    @staticmethod
    def _s(b, p):
        r = s = 0
        while True:
            x = b[p]
            p += 1
            r |= (x & 0x7F) << s
            s += 7
            if not x & 0x80:
                if x & 0x40:
                    r -= 1 << s
                return r, p

    def _const_expr(self, b, p):
        if b[p] == 0x41:
            v, p = self._s(b, p + 1)
        else:
            raise FlixcloudError("unsupported const expr")
        if b[p] != 0x0B:
            raise FlixcloudError("bad const expr")
        return v & self.M32, p + 1

    def _section(self, sid, b):
        u, p = self._u, 0
        if sid == 1:
            n, p = u(b, p)
            for _ in range(n):
                p += 1  # 0x60
                np_, p = u(b, p)
                params = list(b[p:p + np_]); p += np_
                nr, p = u(b, p)
                res = list(b[p:p + nr]); p += nr
                self.types.append((params, res))
        elif sid == 3:
            n, p = u(b, p)
            for _ in range(n):
                t, p = u(b, p)
                self.funcs.append(t)
        elif sid == 5:
            n, p = u(b, p)
            flags, p = u(b, p)
            mn, p = u(b, p)
            self.mem = bytearray(mn * 65536)
        elif sid == 6:
            n, p = u(b, p)
            for _ in range(n):
                p += 2  # valtype, mutability
                v, p = self._const_expr(b, p)
                self.globals.append(v)
        elif sid == 7:
            n, p = u(b, p)
            for _ in range(n):
                ln, p = u(b, p)
                name = b[p:p + ln].decode(); p += ln
                kind, idx = b[p], None
                idx, p = u(b, p + 1)
                self.exports[(kind, name)] = idx
        elif sid == 10:
            n, p = u(b, p)
            for _ in range(n):
                size, p = u(b, p)
                self.code.append(b[p:p + size])
                p += size
        elif sid == 11:
            n, p = u(b, p)
            for _ in range(n):
                flag, p = u(b, p)
                if flag != 0:
                    raise FlixcloudError("unsupported data segment kind")
                off, p = self._const_expr(b, p)
                ln, p = u(b, p)
                self.data.append((off, bytes(b[p:p + ln])))
                p += ln

    def _decode(self, body):
        u, sg = self._u, self._s
        p = 0
        ngroups, p = u(body, p)
        nlocals = 0
        for _ in range(ngroups):
            c, p = u(body, p)
            nlocals += c
            p += 1
        ins, stack = [], []
        while p < len(body):
            op = body[p]
            p += 1
            imm = None
            if op in (0x02, 0x03, 0x04):
                p += 1  # blocktype (void / single valtype assumed; no multi-value)
                stack.append(len(ins))
                ins.append([op, None, None])  # [op, end_idx, else_idx]
                continue
            if op == 0x05:
                ins[stack[-1]][2] = len(ins)
                ins.append((op, stack[-1], None))
                continue
            if op == 0x0B:
                if stack:
                    ins[stack.pop()][1] = len(ins)
                ins.append((op, None, None))
                continue
            if op in (0x0C, 0x0D, 0x20, 0x21, 0x22, 0x23, 0x24):
                imm, p = u(body, p)
            elif op == 0x41:
                imm, p = sg(body, p)
            elif 0x28 <= op <= 0x3E:  # loads / stores: align, offset
                _, p = u(body, p)
                imm, p = u(body, p)
            elif op in (0x3F, 0x40):
                p += 1
            elif op in (0x00, 0x01, 0x0F, 0x1A, 0x1B) or 0x45 <= op <= 0x78:
                pass
            else:
                raise FlixcloudError(f"unsupported wasm opcode 0x{op:02x}")
            ins.append((op, imm, None))
        return nlocals, [tuple(x) for x in ins]

    # -- execution
    def call(self, name, *args):
        idx = self.exports[(0, name)]
        return self._run(idx, list(args))

    def _run(self, fidx, args):
        params, results = self.types[self.funcs[fidx]]
        nlocals, code = self.fns[fidx]
        loc = [a & self.M32 for a in args] + [0] * nlocals
        st, labels, mem, M = [], [], self.mem, self.M32
        pc = 0

        def s32(x):
            return x - (1 << 32) if x & 0x80000000 else x

        def rotl(x, k):
            k &= 31
            return ((x << k) | (x >> (32 - k))) & M if k else x

        while pc < len(code):
            ins = code[pc]
            op, imm = ins[0], ins[1]
            pc += 1
            if op in (0x02, 0x03):
                labels.append((op, pc - 1, imm))
            elif op == 0x04:
                c = st.pop()
                labels.append((op, pc - 1, imm))
                if not c:
                    e = ins[2]
                    pc = e + 1 if e is not None else imm  # no else: run the `end` (pops label)
            elif op == 0x05:  # reached else from then-branch: skip to end
                lab = labels.pop()
                pc = lab[2] + 1
            elif op == 0x0B:
                if labels:
                    labels.pop()
                else:
                    break
            elif op in (0x0C, 0x0D):
                if op == 0x0D and not st.pop():
                    continue
                lab = labels[-1 - imm]
                if imm:
                    del labels[-imm:]
                if lab[0] == 0x03:
                    pc = lab[1] + 1  # re-enter loop; label stays
                else:
                    labels.pop()
                    pc = lab[2] + 1
            elif op == 0x0F:
                break
            elif op == 0x00:
                raise FlixcloudError("wasm trap: unreachable")
            elif op == 0x01:
                pass
            elif op == 0x1A:
                st.pop()
            elif op == 0x1B:
                c, b, a = st.pop(), st.pop(), st.pop()
                st.append(a if c else b)
            elif op == 0x20:
                st.append(loc[imm])
            elif op == 0x21:
                loc[imm] = st.pop()
            elif op == 0x22:
                loc[imm] = st[-1]
            elif op == 0x23:
                st.append(self.globals[imm])
            elif op == 0x24:
                self.globals[imm] = st.pop()
            elif op == 0x41:
                st.append(imm & M)
            elif op in (0x28, 0x2C, 0x2D, 0x2E, 0x2F):
                a = st.pop() + imm
                if op == 0x28:
                    st.append(struct.unpack_from("<I", mem, a)[0])
                elif op == 0x2D:
                    st.append(mem[a])
                elif op == 0x2C:
                    st.append(mem[a] - 256 & M if mem[a] & 0x80 else mem[a])
                elif op == 0x2F:
                    st.append(struct.unpack_from("<H", mem, a)[0])
                else:
                    st.append(struct.unpack_from("<h", mem, a)[0] & M)
            elif op in (0x36, 0x3A, 0x3B):
                v, a = st.pop(), st.pop() + imm
                if op == 0x36:
                    struct.pack_into("<I", mem, a, v & M)
                elif op == 0x3A:
                    mem[a] = v & 0xFF
                else:
                    struct.pack_into("<H", mem, a, v & 0xFFFF)
            elif op == 0x45:
                st.append(int(st.pop() == 0))
            elif 0x46 <= op <= 0x4F:
                b, a = st.pop(), st.pop()
                st.append(int({
                    0x46: a == b, 0x47: a != b, 0x48: s32(a) < s32(b), 0x49: a < b,
                    0x4A: s32(a) > s32(b), 0x4B: a > b, 0x4C: s32(a) <= s32(b), 0x4D: a <= b,
                    0x4E: s32(a) >= s32(b), 0x4F: a >= b}[op]))
            elif op in (0x67, 0x68, 0x69):
                a = st.pop()
                if op == 0x67:
                    st.append(32 - a.bit_length())
                elif op == 0x68:
                    st.append(32 if a == 0 else (a & -a).bit_length() - 1)
                else:
                    st.append(bin(a).count("1"))
            elif 0x6A <= op <= 0x78:
                b, a = st.pop(), st.pop()
                if op == 0x6A: r = a + b
                elif op == 0x6B: r = a - b
                elif op == 0x6C: r = a * b
                elif op == 0x6D: r = int(s32(a) / s32(b))
                elif op == 0x6E: r = a // b
                elif op == 0x6F: r = s32(a) - s32(b) * int(s32(a) / s32(b))
                elif op == 0x70: r = a % b
                elif op == 0x71: r = a & b
                elif op == 0x72: r = a | b
                elif op == 0x73: r = a ^ b
                elif op == 0x74: r = a << (b & 31)
                elif op == 0x75: r = s32(a) >> (b & 31)
                elif op == 0x76: r = a >> (b & 31)
                elif op == 0x77: r = rotl(a, b)
                else: r = rotl(a, 32 - (b & 31))
                st.append(r & M)
            else:
                raise FlixcloudError(f"unsupported wasm opcode 0x{op:02x}")
        return st[-1] if results and st else None


def _wasm_mix(payload_b64: str, seed_i32: int, f1: bytes, f2: bytes, f3: bytes):
    """Run the per-page wasm. Returns (F, playlist_xor_key); key = 32 bytes at _c() (== window.__pk)."""
    w = _MiniWasm(base64.b64decode(payload_b64))
    n = len(f1)
    E, A, K, H = 1000, 1000 + n, 1000 + 2 * n, 1000 + 3 * n
    if len(w.mem) == 0:
        w.mem = bytearray(65536)
    for off, bs in ((E, f1), (A, f2), (K, f3)):
        w.mem[off:off + len(bs)] = bs
    w.call("_s", seed_i32)
    w.call("_r", E, A, K, H, n)
    F = bytes(w.mem[H:H + n])
    pk = b""
    if (0, "_c") in w.exports:
        ptr = w.call("_c")
        pk = bytes(w.mem[ptr:ptr + 32])
    return F, pk


# --------------------------------------------------------------------------- AES (fallback)
def _aes_cbc_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    """AES-CBC decrypt (no unpadding). Uses `cryptography` when installed, else pure Python."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return d.update(ct) + d.finalize()
    except ImportError:
        return _py_aes_cbc_decrypt(key, iv, ct)


def _build_aes_tables():
    def rotl8(x, k):
        return ((x << k) | (x >> (8 - k))) & 0xFF

    sbox, p, q = [0] * 256, 1, 1
    while True:
        p = (p ^ (p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q = (q ^ (q << 1)) & 0xFF
        q = (q ^ (q << 2)) & 0xFF
        q = (q ^ (q << 4)) & 0xFF
        if q & 0x80:
            q ^= 0x09
        sbox[p] = q ^ rotl8(q, 1) ^ rotl8(q, 2) ^ rotl8(q, 3) ^ rotl8(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


_SBOX, _INV = _build_aes_tables()


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a, b = _xt(a), b >> 1
    return r


def _expand_key(key):
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xt(rcon)
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(nr + 1)]


def _py_aes_cbc_decrypt(key, iv, ct):
    rk = _expand_key(key)
    nr = len(rk) - 1
    out, prev = bytearray(), iv
    for off in range(0, len(ct), 16):
        blk = list(ct[off:off + 16])
        s = [a ^ b for a, b in zip(blk, rk[nr])]
        for r in range(nr - 1, -1, -1):
            # inv shift rows (column-major state)
            t = [0] * 16
            for c in range(4):
                for rr in range(4):
                    t[((c + rr) % 4) * 4 + rr] = s[c * 4 + rr]
            s = [_INV[b] for b in t]
            s = [a ^ b for a, b in zip(s, rk[r])]
            if r:
                n = []
                for c in range(4):
                    a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                    n += [_mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9),
                          _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13),
                          _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11),
                          _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14)]
                s = n
        out += bytes(a ^ b for a, b in zip(s, prev))
        prev = ct[off:off + 16]
    return bytes(out)


# --------------------------------------------------------------------------- the resolver
def _sha_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def derive_fields(seed: str) -> dict:
    e = seed
    for i in range(3):
        e = _sha_hex(e + str(i))
    a = e
    for i in range(3):
        a = _sha_hex(a + str(i))
    return {
        "keyField": f"kf_{e[8:16]}", "ivField": f"ivf_{e[16:24]}",
        "containerName": f"cd_{e[24:32]}", "arrayName": f"ad_{e[32:40]}", "objectName": f"od_{e[40:48]}",
        "tokenField": f"{e[48:64]}_{e[56:64]}", "keyFrag2Field": f"{a[0:16]}_{a[16:24]}",
    }


def decrypt_playlist_url(data: dict, token_json: dict, token: str, with_key: bool = False):
    """Pure function: page data + /api/m3u8 JSON -> plaintext playlist URL (and optionally pk)."""
    seed = data["obfuscation_seed"]
    f = derive_fields(seed)
    try:
        obj = data["obfuscated_crypto_data"][f["containerName"]][f["arrayName"]][0][f["objectName"]]
        frag1_b64, iv_b64 = obj[f["keyField"]], obj[f["ivField"]]
        frag2_b64 = data[f["keyFrag2Field"]]
        ct_b64 = token_json[_sha_hex(token + "vid")[:10]]
        frag3_b64 = token_json[_sha_hex(token + "key")[:10]]
    except (KeyError, IndexError, TypeError) as ex:
        raise FlixcloudError(f"field mapping failed ({ex!r}); scheme probably rotated") from ex
    b64 = base64.b64decode
    seed_i32 = int(seed[:8], 16)
    F, pk = _wasm_mix(data["w_payload"], seed_i32, b64(frag1_b64), b64(frag2_b64), b64(frag3_b64))
    k0 = bytearray(hashlib.pbkdf2_hmac("sha256", F, seed.encode(), 1000, 32))
    for i in range(32):
        k0[i] ^= ord(seed[i % len(seed)]) & 0xFF
    aes_key = hashlib.sha256(bytes(k0)).digest()
    pt = _aes_cbc_decrypt(aes_key, b64(iv_b64), b64(ct_b64))
    pad = pt[-1]
    if not 1 <= pad <= 16 or pt[-pad:] != bytes([pad]) * pad:
        raise FlixcloudError("bad padding after AES decrypt (wrong key?)")
    url = pt[:-pad].decode("utf-8").strip()
    if not url:
        raise FlixcloudError("decrypted URL empty")
    return (url, pk) if with_key else url


def decode_playlist(body: str, pk: bytes) -> str:
    """Playlists are served as base64(XOR(text, pk)) unless they already start with #EXTM3U.
    (Mirrors the patched hls.js playlist loader: key = window.__pk = wasm._c() bytes.)"""
    if body.startswith("#EXTM3U"):
        return body
    raw = base64.b64decode(body.strip())
    out = bytes(c ^ pk[i % len(pk)] for i, c in enumerate(raw)).decode("utf-8")
    if not out.startswith("#EXTM3U"):
        raise FlixcloudError("playlist decode failed (pk mismatch?)")
    return out


def resolve(embed_url: str, fetch: Fetch) -> dict:
    """Embed URL -> {'url': master.m3u8, 'headers': {...}, 'subtitles': [...], ...}.

    ``fetch(url, headers) -> (status, text)`` must perform a plain GET and return the body as text.
    """
    sp = urlsplit(embed_url)
    origin = f"{sp.scheme}://{sp.netloc}"
    base_h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    status, html = fetch(embed_url, {**base_h, "Accept": "text/html,*/*"})
    if status != 200:
        raise FlixcloudError(f"embed page HTTP {status}")
    data = parse_page_data(html)
    f = derive_fields(data["obfuscation_seed"])
    token = data.get(f["tokenField"])
    if not isinstance(token, str) or not token:
        raise FlixcloudError("token field missing from page data (scheme rotated?)")
    status, body = fetch(f"{origin}/api/m3u8/{token}",
                         {**base_h, "Accept": "application/json", "Referer": embed_url})
    if status != 200:
        raise FlixcloudError(f"/api/m3u8 HTTP {status}: {body[:120]}")
    tj = json.loads(body)
    if "error" in tj:
        raise FlixcloudError(f"/api/m3u8 error: {tj['error']}")
    url, pk = decrypt_playlist_url(data, tj, token, with_key=True)
    subs = [{"url": s.get("url"), "language": s.get("language"), "format": s.get("format"),
             "default": bool(s.get("default"))}
            for s in data.get("subtitles") or [] if isinstance(s, dict)]
    return {
        "url": url,
        "playlist_key_b64": base64.b64encode(pk).decode(),  # XOR key for playlist bodies
        "headers": {"User-Agent": UA, "Referer": origin + "/", "Origin": origin},
        "subtitles": subs,
        "fonts": data.get("available_fonts") or {},
        "audio_type": data.get("audio_type"),
        "default_audio_track": data.get("default_audio_track"),
        "video_title": data.get("video_title"),
        "intro": data.get("intro_chapter"), "outro": data.get("outro_chapter"),
    }
