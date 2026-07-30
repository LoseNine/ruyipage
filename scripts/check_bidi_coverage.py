"""Compare local BiDi command/event literals with a WebDriver BiDi Bikeshed source."""

import argparse
import json
import re
from pathlib import Path

COMMAND_RE = re.compile(r"^#### The ([A-Za-z][\w]*\.[A-Za-z][\w]*) Command ####", re.MULTILINE)
EVENT_RE = re.compile(r"^#### The ([A-Za-z][\w]*\.[A-Za-z][\w]*) Event ####", re.MULTILINE)
LITERAL_RE = re.compile(
    r"[\"']((?:browser|browsingContext|emulation|input|log|network|script|session|storage|webExtension)\.[A-Za-z][\w]*)[\"']"
)


def build_report(spec_path, source_root):
    spec = Path(spec_path).read_text(encoding="utf-8")
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path(source_root).rglob("*.py")
    )
    commands = sorted(set(COMMAND_RE.findall(spec)))
    events = sorted(set(EVENT_RE.findall(spec)))
    literals = sorted(set(LITERAL_RE.findall(source_text)))
    local_commands = sorted(set(commands) & set(literals))
    local_events = sorted(set(events) & set(literals))
    return {
        "spec_command_count": len(commands),
        "spec_event_count": len(events),
        "local_command_literal_count": len(local_commands),
        "local_event_literal_count": len(local_events),
        "missing_commands": sorted(set(commands) - set(local_commands)),
        "missing_event_literals": sorted(set(events) - set(local_events)),
        "event_api_is_passthrough": True,
        "unknown_or_extension_literals": sorted(set(literals) - set(commands) - set(events)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--source-root", default="ruyipage")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = build_report(args.spec, args.source_root)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 1 if report["missing_commands"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
