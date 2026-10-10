import argparse
import sys
from pathlib import Path

DEV_YEAR = 2023
CACHE_DIR = Path("data/labeling/cache")


def cmd_border() -> int:
    from kzfires.db.session import connect
    from kzfires.sources.border import load_border, mark_in_kz

    with connect() as conn:
        load_border(conn)
        n = mark_in_kz(conn)
    print(f"граница загружена, in_kz посчитан для {n} точек")
    return 0


def cmd_sample() -> int:
    from kzfires.eval import sample
    from kzfires.labeling import practice
    from kzfires.sources.osm import load_settlements
    from kzfires.sources.regions import load_regions
    from kzfires.sources.worldcover import majority_classes

    regions = load_regions()
    prac = practice.build(regions, majority_classes)
    practice.write(prac)
    print(f"тренировка: {len(prac)} точек -> {practice.TODO_PATH}")

    frame = sample.build_frame(DEV_YEAR, regions, load_settlements(), majority_classes)
    exclude = frozenset(prac["key"])
    selected, shortfall = sample.draw(frame, sample.DEV_QUOTAS, sample.SEED, exclude)
    todo = sample.to_todo(selected, "dev")
    path = sample.write_sample(
        todo,
        frame,
        shortfall,
        "dev",
        DEV_YEAR,
        sample.DEV_QUOTAS,
        sample.SEED,
        int(frame["key"].isin(exclude).sum()),
    )
    print(f"DEV {DEV_YEAR}: рамка {len(frame)} точек, выбрано {len(todo)} -> {path}")
    print(todo.groupby(["stratum", "period"]).size().unstack(fill_value=0).to_string())
    if shortfall:
        print(f"недобор по стратам: {shortfall}")
    return 0


def cmd_label(set_name: str, pass_no: int, port: int, open_browser: bool) -> int:
    from kzfires.labeling import practice
    from kzfires.labeling.server import Session, serve

    if set_name == "practice":
        session = Session(
            set_name="practice",
            todo_path=practice.TODO_PATH,
            labels_path=practice.PRACTICE_DIR / "labels_practice.csv",
            cache_dir=CACHE_DIR / "practice",
            pass_no=1,
            practice=True,
        )
    else:
        session = Session(
            set_name=set_name,
            todo_path=Path(f"labels/labels_todo_{set_name}.csv"),
            labels_path=Path(f"labels/labels_{set_name}.csv"),
            cache_dir=CACHE_DIR / set_name,
            pass_no=pass_no,
        )
    if not session.todo_path.exists():
        print(f"нет {session.todo_path}: сначала make sample", file=sys.stderr)
        return 1
    serve(session, port=port, open_browser=open_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kzfires")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="один опрос FIRMS NRT")
    sub.add_parser("retro", help="ретро-прогон по замороженной конфигурации")
    sub.add_parser("border", help="загрузить границу Казахстана в БД и посчитать in_kz")
    sub.add_parser("sample", help="выборка точек DEV и тренировочный набор для разметки")
    lab = sub.add_parser("label", help="страница разметки в браузере")
    lab.add_argument("--set", default="dev", choices=["dev", "test", "practice"])
    lab.add_argument("--pass", dest="pass_no", type=int, default=1, choices=[1, 2])
    lab.add_argument("--port", type=int, default=8765)
    lab.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "border":
        return cmd_border()
    if args.cmd == "sample":
        return cmd_sample()
    if args.cmd == "label":
        return cmd_label(args.set, args.pass_no, args.port, not args.no_browser)
    print(f"{args.cmd}: ещё не реализовано", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
