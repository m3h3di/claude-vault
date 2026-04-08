#!/usr/bin/env python3
"""
scheduler.py
Entry point. Runs backup immediately on start,
then repeats every INTERVAL_MINUTES from .env.

Usage:
    python scheduler.py

Background (Mac/Linux):
    nohup python scheduler.py >> vault.log 2>&1 &
"""
import logging
import os
import time

import schedule
from dotenv import load_dotenv

from backup import run_backup

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("vault.log"),
    ],
)
log = logging.getLogger(__name__)
INTERVAL = int(os.getenv("INTERVAL_MINUTES", 60))


def main():
    log.info(
        f"claude-vault started - every {INTERVAL} min -> {os.getenv('GITHUB_REPO')}"
    )
    run_backup()
    schedule.every(INTERVAL).minutes.do(run_backup)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
