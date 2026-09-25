"""Command line entry point: `hgm ask "who works at Acme"`."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import HybridGraph
from .mesh import PlanningError
from .model import Node
from .nl import TranslationError


def _load(args: argparse.Namespace) -> HybridGraph:
    if args.config:
        from .config import load_config

        return load_config(args.config)
    from .demo import build_demo

    return build_demo()


def _fmt(v: Any) -> str:
    if isinstance(v, Node):
        props = ", ".join(f"{k}={val!r}" for k, val in v.properties.items())
        return f"({':'.join(v.labels)} {props}) <{v.source}>"
    return repr(v)


def cmd_ask(g: HybridGraph, args: argparse.Namespace) -> int:
    try:
        answer = g.ask(args.question)
    except (TranslationError, PlanningError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(answer.to_dict(), indent=2, default=str))
        return 0
    print("interpretation:", " ; ".join(answer.translation.explanation))
    for step in answer.results.plan:
        print(f"\n[{step['backend']} | {step['strategy']} | {step['language']}]")
        print(step["query"])
    print(f"\n{len(answer.results.rows)} row(s):")
    for row in answer.results.rows:
        print("  " + " | ".join(f"{k}: {_fmt(v)}" for k, v in row.items()))
    return 0


def cmd_compile(g: HybridGraph, args: argparse.Namespace) -> int:
    try:
        for backend, text in g.compile(args.question).items():
            print(f"-- {backend}\n{text}\n")
    except (TranslationError, PlanningError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_schema(g: HybridGraph, args: argparse.Namespace) -> int:
    print(json.dumps(g.schema(), indent=2))
    return 0


def cmd_native(g: HybridGraph, args: argparse.Namespace) -> int:
    rs = g.sparql(args.query, args.backend) if args.lang == "sparql" else g.cypher(args.query, args.backend)
    print(json.dumps(rs.to_dict(), indent=2, default=str))
    return 0


def cmd_serve(g: HybridGraph, args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(g), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hgm", description="Hybrid Graph Mesh")
    p.add_argument("--config", help="JSON config describing backends (defaults to the bundled demo)")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="answer a natural-language question")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_ask)

    c = sub.add_parser("compile", help="show the SPARQL/Cypher a question compiles to")
    c.add_argument("question")
    c.set_defaults(fn=cmd_compile)

    s = sub.add_parser("schema", help="print the merged mesh schema")
    s.set_defaults(fn=cmd_schema)

    n = sub.add_parser("native", help="run raw SPARQL or Cypher on one backend")
    n.add_argument("lang", choices=["sparql", "cypher"])
    n.add_argument("query")
    n.add_argument("--backend")
    n.set_defaults(fn=cmd_native)

    v = sub.add_parser("serve", help="start the HTTP API")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return args.fn(_load(args), args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
