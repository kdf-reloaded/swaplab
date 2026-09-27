"""Decode the outputs of a UTXO transaction: Bitcoin legacy/segwit and the Zcash-style
overwintered v3/v4 format used by KMD. Only what the evidence needs: value and script kind."""


def _varint(b, i):
    v = b[i]
    if v < 0xfd:
        return v, i + 1
    n = {0xfd: 2, 0xfe: 4, 0xff: 8}[v]
    return int.from_bytes(b[i + 1:i + 1 + n], "little"), i + 1 + n


def _kind(spk):
    if len(spk) == 25 and spk[:3] == b"\x76\xa9\x14" and spk[-2:] == b"\x88\xac":
        return "p2pkh", spk[3:23].hex()
    if len(spk) == 23 and spk[:2] == b"\xa9\x14" and spk[-1:] == b"\x87":
        return "p2sh", spk[2:22].hex()
    if len(spk) == 22 and spk[:2] == b"\x00\x14":
        return "p2wpkh", spk[2:].hex()
    if len(spk) == 34 and spk[:2] == b"\x00\x20":
        return "p2wsh", spk[2:].hex()
    if spk[:1] == b"\x6a":
        return "op_return", spk[1:].hex()
    return "other", spk.hex()


def outputs(tx_hex):
    """[{n, value, kind, data}] or {"error": ...}."""
    try:
        b = bytes.fromhex(tx_hex)
        header = int.from_bytes(b[0:4], "little")
        i = 4
        if header & 0x80000000:  # overwintered: version group id follows
            i += 4
        segwit = b[i] == 0 and b[i + 1] != 0
        if segwit:
            i += 2
        nin, i = _varint(b, i)
        for _ in range(nin):
            i += 36
            ln, i = _varint(b, i)
            i += ln + 4
        nout, i = _varint(b, i)
        outs = []
        for n in range(nout):
            value = int.from_bytes(b[i:i + 8], "little")
            i += 8
            ln, i = _varint(b, i)
            kind, data = _kind(b[i:i + ln])
            i += ln
            outs.append({"n": n, "value": value, "kind": kind, "data": data})
        return outs
    except Exception as e:  # noqa
        return {"error": f"decode failed: {e!r}"}
