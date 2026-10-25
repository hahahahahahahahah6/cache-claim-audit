"""Command-line entry point for cache-claim-audit."""

import argparse
import json
import sys
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ": error: " + message + "\n")


def main(argv=None):
    parser = Parser(prog="cache-claim-audit", description="Offline-audit documented caching claims against Python source.")
    parser.add_argument("path", nargs="?", default=".", help="project directory (default: current directory)")
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    parser.add_argument("--verbose", action="store_true", help="show claims, evidence and scan warnings")
    parser.add_argument("--ignore", action="append", default=[], metavar="GLOB", help="ignore relative paths; repeatable")
    parser.add_argument("--fail-on", choices=("MEDIUM", "HIGH"), default="MEDIUM", help="minimum failing severity (default: MEDIUM)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(args.path, args.ignore)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    failed = any(item["severity"] == "HIGH" or args.fail_on == "MEDIUM" for item in result["findings"])
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("FAIL" if failed else "PASS")
        for finding in result["findings"]:
            print(finding["severity"] + " " + finding["rule"])
            for claim in finding["claims"]:
                print("  {}:{}: {}".format(claim["path"], claim["line"], claim["text"]))
            for evidence in finding["evidence"]:
                print("  {}:{}: {}".format(evidence.get("path", "-"), evidence.get("line", "-"), evidence["kind"]))
        if args.verbose:
            print(json.dumps({key: value for key, value in result.items() if key != "findings"}, indent=2, sort_keys=True))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
