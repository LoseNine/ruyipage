#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Decode a binary jscall trace into JSONL, or print its call tree.

The browser writes fixed 32-byte records plus a JSONL sidecar mapping each
func_id to a name/url/line. Joining the two here rather than in the browser is
the whole point: it keeps string formatting off the JS thread.

Record order in the .bin is *leave* order. call_id is allocated at enter, so
sorting by call_id recovers enter order. call_id also encodes the thread in its
top 16 bits, because per-thread counters avoid a contended global atomic; ids
are therefore only comparable within one thread.

Usage:
    jscall_decode.py trace_jscall_process_1234.bin              # JSONL
    jscall_decode.py trace_jscall_process_1234.bin --tree       # call tree
    jscall_decode.py trace_jscall_process_1234.bin --stats      # summary
"""

import argparse
import json
import struct
import sys
from pathlib import Path

HEADER_FORMAT = "<4sHHII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
RECORD_FORMAT = "<QQIIHHBBH"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)
MAGIC = b"JSCT"
SUPPORTED_VERSION = 1

FLAG_CONSTRUCT = 1 << 0
FLAG_OK = 1 << 1
FLAG_NATIVE = 1 << 2
FLAG_SELF_HOSTED = 1 << 3
FLAG_SUSPENDED = 1 << 4
FLAG_RESUMED = 1 << 5
FLAG_CALLER_JIT = 1 << 6

ENTRY_PATHS = {0: "unknown", 1: "stack", 2: "inline", 3: "jit", 4: "baseline"}

CALL_ID_THREAD_SHIFT = 48
CALL_ID_COUNTER_MASK = (1 << 48) - 1


DETAIL_HEADER_FORMAT = "<QHBBI"
DETAIL_HEADER_SIZE = struct.calcsize(DETAIL_HEADER_FORMAT)
DETAIL_FILE_FORMAT = "<4sHHII"
DETAIL_FILE_SIZE = struct.calcsize(DETAIL_FILE_FORMAT)
DETAIL_MAGIC = b"JSCV"

VALUE_TYPES = {
    0: "unknown",
    1: "undefined",
    2: "null",
    3: "boolean",
    4: "int32",
    5: "double",
    6: "string",
    7: "bigint",
    8: "object",
    9: "array",
    10: "typedarray",
    11: "arraybuffer",
    12: "function",
    13: "symbol",
    14: "magic",
}

DETAIL_TRUNCATED = 1 << 0
DETAIL_UNREADABLE = 1 << 1
DETAIL_IDENTITY_ONLY = 1 << 2
DETAIL_UNSTABLE_IDENTITY = 1 << 3

SLOT_RETURN = 0xFFFF
SLOT_THIS = 0xFFFE


class TraceFormatError(Exception):
    pass


OPCODE_MAGIC = b"JSCO"
OPCODE_FORMAT = "<QII"
OPCODE_SIZE = struct.calcsize(OPCODE_FORMAT)
OPCODE_PC_MASK = 0x00FFFFFF


def default_detail_path(bin_path):
    return Path(str(bin_path)[: -len(".bin")] + ".detail.bin")


def default_opcode_path(bin_path):
    return Path(str(bin_path)[: -len(".bin")] + ".opcode.bin")


def load_opcodes(path):
    """Executed bytecode ops, in execution order per thread.

    Deliberately has no timestamp -- a clock read per bytecode op would cost
    more than the record itself and adds nothing to control-flow recovery.
    Order is file order; call_id ties a run of ops to the call that ran them.
    """
    ops = []
    if not path.exists():
        return ops
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        return ops
    magic, version, record_size, _pid, _ = struct.unpack_from(
        HEADER_FORMAT, data, 0
    )
    if magic != OPCODE_MAGIC:
        raise TraceFormatError(f"{path}: bad opcode magic {magic!r}")
    if record_size != OPCODE_SIZE:
        raise TraceFormatError(
            f"{path}: opcode record {record_size}, decoder reads {OPCODE_SIZE}"
        )
    body = data[HEADER_SIZE:]
    for offset in range(0, len(body) - (len(body) % OPCODE_SIZE), OPCODE_SIZE):
        call_id, func_id, pc_and_op = struct.unpack_from(
            OPCODE_FORMAT, body, offset
        )
        ops.append(
            {
                "call_id": call_id,
                "func_id": func_id,
                "pc": pc_and_op & OPCODE_PC_MASK,
                "op": pc_and_op >> 24,
            }
        )
    return ops


def decode_value(type_name, flags, payload):
    """Renders a captured value, keeping raw bytes recoverable.

    Binary payloads are hex rather than a lossy text rendering: for a crypto
    routine the exact bytes are the point.
    """
    if flags & DETAIL_UNREADABLE:
        return None
    if flags & DETAIL_IDENTITY_ONLY:
        # Which object, not what was in it. Rendered as the same fixed-width hex
        # the wasm and DOM logs use for an object id, so the two can be joined.
        # A "?" prefix marks an identity that is not collection-stable and so
        # must not be joined on -- see kJSCallDetailUnstableIdentity.
        if len(payload) != 8:
            return None
        value = struct.unpack("<Q", payload)[0]
        prefix = "?" if flags & DETAIL_UNSTABLE_IDENTITY else "#"
        return f"{prefix}{value:016x}"
    if type_name == "boolean":
        return bool(payload[0]) if payload else None
    if type_name == "int32":
        return struct.unpack("<i", payload)[0] if len(payload) == 4 else None
    if type_name == "double":
        return struct.unpack("<d", payload)[0] if len(payload) == 8 else None
    if type_name == "string":
        return payload.decode("utf-8", errors="replace")
    if type_name in ("object", "function"):
        return payload.decode("utf-8", errors="replace")
    if type_name in ("typedarray", "arraybuffer"):
        # A raw memory dump. On x86 each element of a Uint32Array is stored
        # little-endian, so this hex is NOT the array's logical word order --
        # comparing it against a Uint8Array's hex reports the same bytes as
        # different data.
        return payload.hex()
    if type_name == "array":
        # A plain Array's dense elements, serialized as JSON by the capture
        # side because the members are Values rather than a buffer. Parsed
        # here so a caller gets a list instead of having to know that.
        text = payload.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return None


def load_detail(path):
    """callId -> {slot label: value record}."""
    by_call = {}
    if not path.exists():
        return by_call
    data = path.read_bytes()
    if len(data) < DETAIL_FILE_SIZE:
        return by_call
    magic, version, header_size, _pid, _ = struct.unpack_from(
        DETAIL_FILE_FORMAT, data, 0
    )
    if magic != DETAIL_MAGIC:
        raise TraceFormatError(f"{path}: bad detail magic {magic!r}")
    if header_size != DETAIL_HEADER_SIZE:
        raise TraceFormatError(
            f"{path}: detail header {header_size}, decoder reads {DETAIL_HEADER_SIZE}"
        )
    offset = DETAIL_FILE_SIZE
    while offset + DETAIL_HEADER_SIZE <= len(data):
        call_id, slot, type_id, flags, length = struct.unpack_from(
            DETAIL_HEADER_FORMAT, data, offset
        )
        offset += DETAIL_HEADER_SIZE
        if offset + length > len(data):
            break
        payload = data[offset : offset + length]
        offset += length
        type_name = VALUE_TYPES.get(type_id, "unknown")
        if slot == SLOT_RETURN:
            label = "return"
        elif slot == SLOT_THIS:
            label = "this"
        else:
            label = f"arg{slot}"
        by_call.setdefault(call_id, {})[label] = {
            "type": type_name,
            "value": decode_value(type_name, flags, payload),
            "bytes": length,
            "truncated": bool(flags & DETAIL_TRUNCATED),
            "unreadable": bool(flags & DETAIL_UNREADABLE),
            "identity_only": bool(flags & DETAIL_IDENTITY_ONLY),
            "unstable_identity": bool(flags & DETAIL_UNSTABLE_IDENTITY),
        }
    return by_call


def default_dict_path(bin_path):
    return bin_path.with_suffix("").with_suffix(".dict.jsonl")


def load_dict(path):
    """Returns (functions, stats).

    The sidecar carries one entry per interned function, a per-function
    jscall_func_stats line written at shutdown, and a single jscall_stats line.
    A missing jscall_stats line means the process did not shut down cleanly, so
    the capture should be treated as truncated.

    The two per-function lines share a func_id and must be merged rather than
    let overwrite each other. They were not merged once, and because the stats
    line carries no name or url and is written last, every name in the capture
    silently became empty -- 1,905 of 2,310 functions in the run that found
    this. A decoder that loses the one thing the sidecar exists for should at
    least be loud about it, so the merge keeps both and nothing here drops a
    line it does not recognise.
    """
    functions = {}
    stats = None
    if not path.exists():
        return functions, stats
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as error:
                raise TraceFormatError(f"{path}:{line_number}: {error}") from error
            if entry.get("type") == "jscall_stats":
                stats = entry
                continue
            if "func_id" not in entry:
                continue
            func_id = entry["func_id"]
            existing = functions.get(func_id)
            if entry.get("type") == "jscall_func_stats":
                per_func = {
                    key: value
                    for key, value in entry.items()
                    if key not in ("type", "func_id")
                }
                if existing is None:
                    functions[func_id] = {"func_id": func_id, "stats": per_func}
                else:
                    existing["stats"] = per_func
                continue
            # The name entry. Either side may arrive first, so carry over any
            # stats already collected for this id.
            if existing is not None and "stats" in existing:
                entry["stats"] = existing["stats"]
            functions[func_id] = entry
    return functions, stats


def read_records(path):
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise TraceFormatError(f"{path}: shorter than a file header")
    magic, version, record_size, pid, _ = struct.unpack_from(HEADER_FORMAT, data, 0)
    if magic != MAGIC:
        raise TraceFormatError(f"{path}: bad magic {magic!r}, expected {MAGIC!r}")
    if version != SUPPORTED_VERSION:
        raise TraceFormatError(f"{path}: version {version}, expected {SUPPORTED_VERSION}")
    if record_size != RECORD_SIZE:
        raise TraceFormatError(
            f"{path}: record size {record_size}, this decoder reads {RECORD_SIZE}"
        )

    body = data[HEADER_SIZE:]
    # A process killed mid-write can leave a partial record; report it rather
    # than silently dropping the tail.
    trailing = len(body) % RECORD_SIZE
    records = []
    for offset in range(0, len(body) - trailing, RECORD_SIZE):
        (
            call_id,
            parent_call_id,
            func_id,
            duration_us,
            depth,
            argc,
            flags,
            entry_path,
            _reserved,
        ) = struct.unpack_from(RECORD_FORMAT, body, offset)
        records.append(
            {
                "call_id": call_id,
                "parent_call_id": parent_call_id,
                "func_id": func_id,
                "duration_us": duration_us,
                "depth": depth,
                "argc": argc,
                "flags": flags,
                "entry_path": entry_path,
            }
        )
    return pid, records, trailing


def enrich(record, functions):
    func = functions.get(record["func_id"], {})
    flags = record["flags"]
    return {
        "type": "jscall",
        "call_id": record["call_id"],
        "parent_call_id": record["parent_call_id"],
        "thread": record["call_id"] >> CALL_ID_THREAD_SHIFT,
        "seq": record["call_id"] & CALL_ID_COUNTER_MASK,
        "depth": record["depth"],
        "func_id": record["func_id"],
        "name": func.get("name", ""),
        "url": func.get("url", ""),
        "line": func.get("line", 0),
        "column": func.get("column", 0),
        "kind": func.get("kind", "unknown"),
        "argc": record["argc"],
        "op": "construct" if flags & FLAG_CONSTRUCT else "call",
        "ok": bool(flags & FLAG_OK),
        "native": bool(flags & FLAG_NATIVE),
        "self_hosted": bool(flags & FLAG_SELF_HOSTED),
        # The frame parked at an await/yield instead of returning.
        "suspended": bool(flags & FLAG_SUSPENDED),
        "resumed": bool(flags & FLAG_RESUMED),
        # parent_call_id is only the *immediate* caller when this is false.
        # When true, at least one JIT frame was elided, so the edge asserts
        # reachability, not adjacency.
        "parent_exact": not (flags & FLAG_CALLER_JIT),
        # The callee ran in the JIT, so its own callees are absent: this node's
        # subtree is truncated rather than genuinely empty.
        "subtree_truncated": ENTRY_PATHS.get(record["entry_path"]) == "jit",
        "entry_path": ENTRY_PATHS.get(record["entry_path"], "unknown"),
        "duration_us": record["duration_us"],
    }


def label(event):
    name = event["name"] or "<anonymous>"
    where = ""
    if event["url"]:
        where = f'  {event["url"]}:{event["line"]}:{event["column"]}'
    marker = "" if event["ok"] else "  [threw]"
    return f'{name}({event["argc"]}){where}{marker}'


def print_tree(events, stream):
    # Enter order, per thread. Depth comes straight from the record, so the
    # tree renders without having to reconstruct parent links.
    for thread in sorted({event["thread"] for event in events}):
        stream.write(f"=== thread {thread} ===\n")
        for event in sorted(
            (e for e in events if e["thread"] == thread), key=lambda e: e["seq"]
        ):
            indent = "  " * min(event["depth"], 40)
            stream.write(f"{indent}{label(event)}\n")


def integrity(events, stats):
    """Checks the trace can actually support the conclusions drawn from it.

    A record count alone cannot fail visibly. These can: an enter/leave
    mismatch means calls never returned or records were lost, a dangling
    parent means a subtree lost its root, and the fabricated-edge count bounds
    how much of the call graph asserts adjacency it cannot support.
    """
    ids = {e["call_id"] for e in events}
    dangling = sum(
        1 for e in events if e["parent_call_id"] and e["parent_call_id"] not in ids
    )
    report = {
        "records": len(events),
        "dangling_parents": dangling,
        "roots": sum(1 for e in events if not e["parent_call_id"]),
        "compressed_edges": sum(1 for e in events if not e["parent_exact"]),
        "truncated_subtrees": sum(1 for e in events if e["subtree_truncated"]),
        "suspensions": sum(1 for e in events if e["suspended"]),
        "resumptions": sum(1 for e in events if e["resumed"]),
    }
    if stats and "recorded" in stats:
        # Entered but never emitted a leave: still on the stack at exit, never
        # returned, or the record was lost.
        report["entered_not_returned"] = stats["recorded"] - len(events)
    return report


def print_stats(events, pid, trailing, stats, stream):
    by_func = {}
    for event in events:
        key = (event["name"] or "<anonymous>", event["url"])
        by_func[key] = by_func.get(key, 0) + 1
    threw = sum(1 for e in events if not e["ok"])
    max_depth = max((e["depth"] for e in events), default=0)
    stream.write(f"{'pid':<17}{pid}\n")
    stream.write(f"{'records':<17}{len(events)}\n")
    stream.write(f"{'threads':<17}{len({e['thread'] for e in events})}\n")
    stream.write(f"{'functions':<17}{len({e['func_id'] for e in events})}\n")
    stream.write(f"{'max depth':<17}{max_depth}\n")
    stream.write(f"{'threw':<17}{threw}\n")
    if trailing:
        stream.write(f"WARNING          {trailing} trailing bytes (truncated write)\n")
    if stats is None:
        stream.write("WARNING          no jscall_stats line: unclean shutdown\n")
    else:
        for key in (
            "recorded",
            "dropped_limit",
            "dropped_gate",
            "dropped_chunks",
            "filtered_sources",
            "io_errors",
            "script_filter",
        ):
            if key in stats:
                stream.write(f"{key:<17}{stats[key]}\n")
        if stats.get("dropped_no_func_id"):
            stream.write("WARNING          function table could not mint ids: "
                         "records are missing\n")

    stream.write("\nintegrity:\n")
    for key, value in integrity(events, stats).items():
        stream.write(f"  {key:<22}{value}\n")

    # Deliberately not called a profile. A record only exists where the call
    # was visible to the VM's generic path, which is anti-correlated with how
    # hot a function is: the hottest function in a JITted loop may not appear
    # at all. This ranks observability, not cost.
    stream.write("\nmost frequently observed callees (NOT a hotness profile):\n")
    for (name, url), count in sorted(by_func.items(), key=lambda kv: -kv[1])[:20]:
        stream.write(f"  {count:>8}  {name}  {url}\n")


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin", type=Path, help="trace_jscall_process_<pid>.bin")
    parser.add_argument(
        "--dict", type=Path, default=None, help="defaults to <bin>.dict.jsonl"
    )
    parser.add_argument(
        "--detail", type=Path, default=None, help="defaults to <bin>.detail.bin"
    )
    parser.add_argument(
        "--values", action="store_true", help="print only calls with captured values"
    )
    parser.add_argument(
        "--opcodes", action="store_true", help="print the executed bytecode stream"
    )
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--tree", action="store_true", help="print a call tree")
    parser.add_argument("--stats", action="store_true", help="print a summary")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        pid, records, trailing = read_records(args.bin)
        functions, stats = load_dict(args.dict or default_dict_path(args.bin))
        detail = load_detail(args.detail or default_detail_path(args.bin))
    except (TraceFormatError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    events = [enrich(record, functions) for record in records]
    for event in events:
        values = detail.get(event["call_id"])
        if values:
            event["values"] = values

    stream = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    try:
        if args.opcodes:
            ops = load_opcodes(default_opcode_path(args.bin))
            stream.write(f"{len(ops):,} opcode records\n")
            for op in ops[:2000]:
                func = functions.get(op["func_id"], {})
                name = func.get("name") or "<anonymous>"
                stream.write(
                    f'  call={op["call_id"]:<6} {name:<24} pc={op["pc"]:<6} '
                    f'op={op["op"]}\n'
                )
            if len(ops) > 2000:
                stream.write(f"  ... {len(ops) - 2000:,} more\n")
        elif args.values:
            for event in sorted(
                (e for e in events if "values" in e), key=lambda e: e["call_id"]
            ):
                stream.write(f'{label(event)}  call_id={event["call_id"]}\n')
                for slot, v in event["values"].items():
                    mark = " (truncated)" if v["truncated"] else ""
                    mark += " (unreadable)" if v["unreadable"] else ""
                    stream.write(
                        f'    {slot:<8} {v["type"]:<12} {v["value"]!r}{mark}\n'
                    )
        elif args.stats:
            print_stats(events, pid, trailing, stats, stream)
        elif args.tree:
            print_tree(events, stream)
        else:
            for event in events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    finally:
        if args.output:
            stream.close()

    if trailing and not args.stats:
        print(
            f"warning: {trailing} trailing bytes in {args.bin} (truncated write)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
