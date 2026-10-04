import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kzfires")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="один опрос FIRMS NRT")
    sub.add_parser("retro", help="ретро-прогон по замороженной конфигурации")
    args = parser.parse_args(argv)
    print(f"{args.cmd}: ещё не реализовано", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
