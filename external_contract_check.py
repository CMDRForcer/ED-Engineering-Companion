"""Explicit, read-only reachability/schema probe for release QA."""

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ed_companion import APP_VERSION
from ed_companion.integrations.eddn import supported_schema_names
from ed_companion.integrations.inara import INARA_API_URL


EDDN_SCHEMA_ROOT = "https://eddn.edcd.io/schemas/"


def _read_url(url, opener=urlopen):
    request = Request(
        url, headers={"User-Agent": f"EDOPS-contract-check/{APP_VERSION}"}
    )
    try:
        with opener(request, timeout=15) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except (OSError, URLError):
        return 0, b""


def run_live_check(opener=urlopen):
    failures = []
    for schema in sorted(supported_schema_names()):
        status, body = _read_url(EDDN_SCHEMA_ROOT + schema, opener)
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            document = None
        if status != 200 or not isinstance(document, dict):
            failures.append(f"EDDN {schema}: HTTP {status}, invalid schema document")
    status, _body = _read_url(INARA_API_URL, opener)
    if status == 0 or status >= 500:
        failures.append(f"INARA endpoint: HTTP {status}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Manual read-only EDDN schema and INARA reachability check."
    )
    parser.add_argument(
        "--live", action="store_true",
        help="perform explicit GET requests; never uploads Commander data",
    )
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("network access requires explicit --live")
    failures = run_live_check()
    if failures:
        print("\n".join(failures))
        return 1
    print(
        f"External contract check OK · {len(supported_schema_names())} EDDN schemas "
        "· INARA endpoint reachable"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
