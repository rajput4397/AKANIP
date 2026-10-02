import argparse
import logging
from pathlib import Path

import yaml

from _utils.logger_config import setup_logging

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_scans(config_path=None, provider_filter=None):
    config_path = Path(config_path or PROJECT_ROOT / "config.yaml").expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {config_path}: {error}") from error

    scans = config.get("scans") if isinstance(config, dict) else None
    if not isinstance(scans, list) or not scans:
        raise ValueError("YAML config must contain a non-empty 'scans' list")

    from services.upstock_api import run_scan as run_upstox_scan
    from services.yfinance import run_scan as run_yfinance_scan

    scan_runners = {
        "upstox": run_upstox_scan,
        "yfinance": run_yfinance_scan,
    }
    setup_logging()
    log = logging.getLogger(__name__)
    run_count = 0
    failure_count = 0

    for index, scan in enumerate(scans, start=1):
        if not isinstance(scan, dict):
            log.error(f"Scan entry {index} must be a YAML mapping")
            failure_count += 1
            continue

        scan_name = scan.get("name", f"scan {index}")
        provider = scan.get("provider")
        if provider not in scan_runners:
            log.error(f"Scan '{scan_name}' has unsupported provider '{provider}'")
            failure_count += 1
            continue
        if provider_filter and provider != provider_filter:
            continue
        if not isinstance(scan.get("csv_path"), str) or not scan["csv_path"].strip():
            log.error(f"Scan '{scan_name}' must define a csv_path")
            failure_count += 1
            continue

        try:
            log.info(f"Running scan '{scan_name}' using {provider}")
            scan_runners[provider](scan, config_path)
            run_count += 1
        except Exception as error:
            log.exception(f"Scan '{scan_name}' failed: {error}")
            failure_count += 1

    if provider_filter and run_count == 0 and failure_count == 0:
        raise ValueError(f"No scans configured for provider '{provider_filter}'")
    log.info(f"Configured scan run complete: {run_count} succeeded, {failure_count} failed")
    return run_count, failure_count


def main():
    parser = argparse.ArgumentParser(description="Run all configured yfinance and Upstox CSV scans.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.yaml")
    args = parser.parse_args()
    run_scans(args.config)


if __name__ == "__main__":
    main()
