import argparse
import sys


def cmd_border() -> int:
    from kzfires.db.session import connect
    from kzfires.sources.border import load_border, mark_in_kz

    with connect() as conn:
        load_border(conn)
        n = mark_in_kz(conn)
    print(f"граница загружена, in_kz посчитан для {n} точек")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kzfires")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="один опрос FIRMS NRT")
    sub.add_parser("retro", help="ретро-прогон по замороженной конфигурации")
    sub.add_parser("border", help="загрузить границу Казахстана в БД и посчитать in_kz")
    args = parser.parse_args(argv)
    if args.cmd == "border":
        return cmd_border()
    print(f"{args.cmd}: ещё не реализовано", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
