"""CLI 入口：python -m audit_engine example.com"""

from .audit import main

if __name__ == "__main__":
    raise SystemExit(main())
