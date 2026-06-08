from __future__ import annotations

import argparse
from pathlib import Path

from .configurable_batch import run_batch_scrape
from .config import AppConfig
from .pipelines import PipelineRunner
from .structured_exports import export_structured_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="district-court-api-scraper")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="Project root directory (default: package root)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="Discover district/court/establishment/act metadata")
    discover.add_argument("--state", required=True, help="State code (e.g., 4)")
    discover.add_argument("--run-id", default=None)

    list_acts = sub.add_parser("list-acts", help="Run act-based case listing")
    list_acts.add_argument("--state", required=True)
    list_acts.add_argument("--district", required=True)
    list_acts.add_argument("--court-complex", required=True)
    list_acts.add_argument("--est", default="")
    list_acts.add_argument("--act", required=True)
    list_acts.add_argument("--section", default="")
    list_acts.add_argument("--status", default="Pending", choices=["Pending", "Disposed", "Both"])
    list_acts.add_argument("--run-id", default=None)

    fetch_details = sub.add_parser("fetch-details", help="Fetch queued case details/hearings for a run")
    fetch_details.add_argument("--run-id", required=True)

    fetch_cnr = sub.add_parser("fetch-cnr", help="Fetch case details by CNR input file (.txt one-per-line or .csv with header)")
    fetch_cnr.add_argument("--input", required=True, help="Path to text/CSV file of CNRs")
    fetch_cnr.add_argument("--run-id", default=None)
    fetch_cnr.add_argument(
        "--proxy-file",
        default=None,
        help="Path to newline-delimited proxy URLs (e.g. http://1.2.3.4:8888). Falls back to $ECOURTS_PROXY_FILE.",
    )

    retry = sub.add_parser("retry-incomplete", help="Requeue and rerun INCOMPLETE tasks")
    retry.add_argument("--run-id", required=True)

    export = sub.add_parser("export", help="Generate CSV exports for a run")
    export.add_argument("--run-id", required=True)

    export_format = sub.add_parser(
        "export-format",
        help="Generate structured cases/hearings/caselists CSVs for a run",
    )
    export_format.add_argument("--run-id", required=True)
    export_format.add_argument(
        "--years",
        default="2025,2026",
        help="Comma-separated CNR year suffixes, e.g. 2025,2026",
    )
    export_format.add_argument("--state-name", default="Kerala")
    export_format.add_argument("--district-name", default="Kollam")
    export_format.add_argument("--sample-size", type=int, default=0)

    batch = sub.add_parser(
        "batch-scrape",
        help="Config-driven multithread scrape (submitAct -> viewHistory -> viewBusiness in same worker session)",
    )
    batch.add_argument("--config", required=True, help="Path to JSON batch config")
    batch.add_argument("--run-id", default=None, help="Optional run id override")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig.default(project_root=Path(args.project_root))

    if args.command == "discover":
        runner = PipelineRunner(config=config, run_id=args.run_id)
        runner.run_discover(state_code=str(args.state))
        print(f"run_id={runner.run_id}")
        return 0

    if args.command == "list-acts":
        runner = PipelineRunner(config=config, run_id=args.run_id)
        runner.run_list_acts(
            state_code=str(args.state),
            dist_code=str(args.district),
            court_complex_code=str(args.court_complex),
            est_code=str(args.est),
            act_code=str(args.act),
            section=str(args.section),
            case_status=str(args.status),
        )
        print(f"run_id={runner.run_id}")
        return 0

    if args.command == "fetch-details":
        runner = PipelineRunner(config=config, run_id=args.run_id)
        runner.run_fetch_details()
        print(f"run_id={runner.run_id}")
        return 0

    if args.command == "fetch-cnr":
        if args.proxy_file:
            config.proxy_file = args.proxy_file
        runner = PipelineRunner(config=config, run_id=args.run_id)
        runner.run_fetch_cnr(input_path=Path(args.input))
        print(f"run_id={runner.run_id}")
        return 0

    if args.command == "retry-incomplete":
        runner = PipelineRunner(config=config, run_id=args.run_id)
        count = runner.retry_incomplete()
        print(f"run_id={runner.run_id} requeued={count}")
        return 0

    if args.command == "export":
        runner = PipelineRunner(config=config, run_id=args.run_id)
        cases_csv, hearings_csv = runner.export()
        print(f"run_id={runner.run_id} cases_csv={cases_csv} hearings_csv={hearings_csv}")
        return 0

    if args.command == "export-format":
        run_root = config.output_runs_dir / args.run_id
        years = {token.strip() for token in str(args.years).split(",") if token.strip()}
        result = export_structured_outputs(
            run_root=run_root,
            years=years,
            state_name=str(args.state_name),
            district_name=str(args.district_name),
            sample_size=args.sample_size if args.sample_size and args.sample_size > 0 else None,
        )
        print(
            " ".join(
                [
                    f"run_id={result.run_id}",
                    f"cases_csv={result.cases_csv}",
                    f"hearings_csv={result.hearings_csv}",
                    f"caselists_csv={result.caselists_csv}",
                    f"cases_rows={result.cases_rows}",
                    f"hearings_rows={result.hearings_rows}",
                    f"caselists_rows={result.caselists_rows}",
                ]
            )
        )
        return 0

    if args.command == "batch-scrape":
        summary = run_batch_scrape(
            app_config=config,
            config_path=Path(args.config),
            run_id_override=args.run_id,
        )
        print(summary)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
