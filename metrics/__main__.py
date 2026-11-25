"""CLI 入口：python -m metrics examples/synthetic_runs.csv"""

from .metrics import main

if __name__ == "__main__":
    raise SystemExit(main())
