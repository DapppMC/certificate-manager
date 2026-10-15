from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from certslice import pipeline
from certslice.errors import AccountingError, InputError
from certslice.evaluate import evaluate


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="certslice", description="Certificate Slicer CLI")
    subparsers = parser.add_subparsers(dest="subcommand")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Split certificate PDF bundle and match holders")
    run_parser.add_argument("--pdf", required=True, help="Path to input bundle PDF")
    run_parser.add_argument("--db", required=True, help="Path to employee database XLSX")
    run_parser.add_argument("--out", required=True, help="Path to output directory")
    run_parser.add_argument("--dry-run", action="store_true", help="Perform dry run without writing split PDFs")
    run_parser.add_argument(
        "--pages-per-cert",
        choices=["1", "2", "auto"],
        default="auto",
        help="Pages per certificate (default: auto)",
    )
    run_parser.add_argument("--no-ocr", action="store_true", help="Disable OCR fallback")
    run_parser.add_argument("--config", default=None, help="Path to custom TOML config file")
    run_parser.add_argument("--use-ai", action="store_true", help="Enable Gemini AI rescue layer for unmatched certificates")
    run_parser.add_argument("--gemini-api-key", default=None, help="Google Gemini API key")

    # Subcommand: evaluate
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate run results against gold set")
    eval_parser.add_argument("--run", required=True, help="Path to run folder")
    eval_parser.add_argument("--gold", required=True, help="Path to gold.csv")

    # Subcommand: gui
    subparsers.add_parser("gui", help="Launch desktop graphical user interface")

    args = parser.parse_args(argv)

    if args.subcommand == "gui":
        from certslice.gui import launch_gui
        launch_gui()
        return 0

    if args.subcommand == "evaluate":
        try:
            return evaluate(Path(args.run), Path(args.gold))
        except InputError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 2

    if args.subcommand == "run":
        try:
            result = pipeline.run(
                pdf_path=args.pdf,
                db_path=args.db,
                out_dir=args.out,
                dry_run=args.dry_run,
                pages_per_cert=args.pages_per_cert,
                ocr=not args.no_ocr,
                config_path=args.config,
                use_ai=args.use_ai,
                gemini_api_key=args.gemini_api_key,
            )
        except InputError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 2
        except AccountingError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 3

        summary = result.summary
        print(f"PAGES={summary['pages']} ACCEPTED={summary['accepted']} UNMATCHED={summary['unmatched']}")
        if result.run_dir is not None:
            print(f"RUN_DIR={result.run_dir.resolve()}")
        return result.exit_code

    parser.print_help(sys.stderr)
    return 2
