"""Command-line interface for the lexscholar federated client.

Runs on stock Python 3.9 (no dependencies). Examples:

    python3 -m lexscholar search "force majeure" --peer-reviewed -n 10
    python3 -m lexscholar search "arbitraje de inversiones" --jurisdiction ES
    python3 -m lexscholar search "energy charter treaty" --source doaj,openaire
    python3 -m lexscholar compare "stabilization clause" FR BR ID TR
    python3 -m lexscholar get doaj 001a1c30c1a64819b6985b4c3225c0a3
    python3 -m lexscholar fulltext scielo S1808-24322008000200006 --out article.txt
    python3 -m lexscholar doi 10.1093/jiel/jgaa002
    python3 -m lexscholar sources
"""

from __future__ import annotations

import argparse
import json
import sys

from ._http import LexScholarError
from .client import LexScholarClient

# Legal scholarship is multilingual by nature; force UTF-8 so a legacy Windows
# console codepage cannot crash the CLI on Portuguese or Azerbaijani text.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _add_filters(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--jurisdiction", help="ISO alpha-2 country code, e.g. FR, BR, TR")
    sp.add_argument("--language", help="ISO language code, e.g. es, fr, tr")
    sp.add_argument("--year-from", dest="year_from", type=int)
    sp.add_argument("--year-to", dest="year_to", type=int)
    sp.add_argument("--peer-reviewed", dest="peer_reviewed_only", action="store_true")
    sp.add_argument("--open-access", dest="open_access_only", action="store_true")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="lexscholar",
        description="Federated search over open-access legal scholarship (10 no-auth sources)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="search across routed sources")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--source", default="auto", help="auto | all | name | comma list")
    sp.add_argument("--full-text-only", dest="full_text_only", action="store_true")
    sp.add_argument("-n", "--limit", type=int, default=20)
    sp.add_argument("--max-sources", dest="max_sources", type=int)
    sp.add_argument("--brief", action="store_true", help="one line per hit")
    _add_filters(sp)

    cp = sub.add_parser("compare", help="run one query across several jurisdictions")
    cp.add_argument("query")
    cp.add_argument("jurisdictions", nargs="+", help="ISO alpha-2 codes, e.g. FR BR ID")
    cp.add_argument("-n", "--limit-per", dest="limit_per", type=int, default=5)
    cp.add_argument("--peer-reviewed", dest="peer_reviewed_only", action="store_true")

    gp = sub.add_parser("get", help="get one article's metadata")
    gp.add_argument("source")
    gp.add_argument("id")
    gp.add_argument("--collection", help="SciELO only, e.g. scl")

    fp = sub.add_parser("fulltext", help="get an article's body text")
    fp.add_argument("source")
    fp.add_argument("id")
    fp.add_argument("--collection")
    fp.add_argument("--max-chars", dest="max_chars", type=int, default=20000)
    fp.add_argument("--out", help="write to a file instead of stdout")

    dp = sub.add_parser("doi", help="resolve a DOI to metadata + open copy")
    dp.add_argument("doi")

    sub.add_parser("sources", help="list source capability cards")

    args = p.parse_args(argv)
    c = LexScholarClient()

    try:
        if args.cmd == "search":
            res = c.search(
                args.query, source=args.source, jurisdiction=args.jurisdiction,
                language=args.language, year_from=args.year_from, year_to=args.year_to,
                peer_reviewed_only=args.peer_reviewed_only,
                open_access_only=args.open_access_only,
                full_text_only=args.full_text_only,
                limit=args.limit, max_sources=args.max_sources,
            )
            if args.brief:
                print("sources: %s  (%.2fs)  %d hits"
                      % (", ".join(res["sources_queried"]), res["elapsed_seconds"], res["returned"]))
                for r in res["results"]:
                    flag = {True: "peer", False: "not-peer", None: "?"}[r.get("peer_reviewed")]
                    print("  [%s/%s] %s | %s" % (r["source"], flag, r.get("year") or "----",
                                                 (r.get("title") or "")[:78]))
            else:
                _print_json(res)

        elif args.cmd == "compare":
            _print_json(c.compare_jurisdictions(
                args.query, args.jurisdictions, limit_per=args.limit_per,
                peer_reviewed_only=args.peer_reviewed_only))

        elif args.cmd == "get":
            extra = {"collection": args.collection} if args.collection else {}
            _print_json(c.get_article(args.source, args.id, **extra))

        elif args.cmd == "fulltext":
            extra = {"collection": args.collection} if args.collection else {}
            res = c.get_fulltext(args.source, args.id, max_chars=args.max_chars, **extra)
            if args.out:
                with open(args.out, "w", encoding="utf-8") as fh:
                    fh.write(res["text"])
                print("wrote %d chars to %s" % (len(res["text"]), args.out), file=sys.stderr)
                if res.get("note"):
                    print("note: %s" % res["note"], file=sys.stderr)
            else:
                _print_json(res)

        elif args.cmd == "doi":
            _print_json(c.resolve_doi(args.doi))

        elif args.cmd == "sources":
            _print_json(c.list_sources())

    except LexScholarError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except KeyError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
