"""允许 python -m alphaforge.cli 调用。"""

from alphaforge.cli import main

if __name__ == "__main__":
    raise SystemExit(main())