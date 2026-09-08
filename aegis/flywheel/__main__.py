"""Flywheel CLI: drive the self-optimization loop from the terminal.

Examples
--------
python -m aegis.flywheel status
python -m aegis.flywheel pipeline
python -m aegis.flywheel train --kind classify --layer preflight
python -m aegis.flywheel gate --layer preflight --version 1
python -m aegis.flywheel activate --layer preflight --version 1
python -m aegis.flywheel serve --adapter-dir artifacts/adapters/<run_id>
"""
import argparse
import json
import sys

from ..config import Settings
from ..service import ReviewService
from . import serve as flywheel_serve


def _service():
    settings = Settings.from_env()
    # Keep imports light: ReviewService builds the full reviewer stack.
    return ReviewService(settings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="aegis.flywheel", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="flywheel readiness and counters")
    p = sub.add_parser("pipeline", help="run the data pipeline")
    p.add_argument("--output-dir", default="", help="override dataset output dir")

    p = sub.add_parser("train", help="train a LoRA adapter from selected samples")
    p.add_argument("--layer", default="preflight")
    p.add_argument("--kind", default="", help="restrict to one sample kind")

    p = sub.add_parser("gate", help="non-regression gate for an adapter")
    p.add_argument("--layer", required=True)
    p.add_argument("--version", type=int, required=True)

    p = sub.add_parser("activate", help="activate an adapter version")
    p.add_argument("--layer", required=True)
    p.add_argument("--version", type=int, required=True)

    p = sub.add_parser("serve", help="serve one adapter on an OpenAI-compatible port")
    p.add_argument("--adapter-dir", required=True)
    p.add_argument("--port", type=int, default=8130)

    args = parser.parse_args(argv)

    service = _service() if args.command != "serve" else None

    if args.command == "status":
        print(json.dumps(service.flywheel_status(), ensure_ascii=False, indent=2,
                         default=str))
    elif args.command == "pipeline":
        manifest = service.flywheel_pipeline_run({} if not args.output_dir
                                                 else {"output_dir": args.output_dir})
        print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    elif args.command == "train":
        options = {"layer": args.layer}
        if args.kind:
            options["kind"] = args.kind
        result = service.flywheel_train_run(options)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.command == "gate":
        print(json.dumps(service.gate_and_activate_adapter(args.layer, args.version),
                         ensure_ascii=False, indent=2, default=str))
    elif args.command == "activate":
        ok = service.activate_adapter(args.layer, args.version)
        print(json.dumps({"activated": ok}, ensure_ascii=False, default=str))
    elif args.command == "serve":
        flywheel_serve.start(args.adapter_dir, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
