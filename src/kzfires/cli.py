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


def cmd_sample_test(years: list[int]) -> int:
    from kzfires.eval import sample
    from kzfires.sources.osm import load_settlements
    from kzfires.sources.regions import load_regions
    from kzfires.sources.worldcover import majority_classes

    regions, settlements = load_regions(), load_settlements()
    for year in years:
        set_name = f"test{year}"
        frame = sample.build_frame(year, regions, settlements, majority_classes)
        selected, shortfall = sample.draw(frame, sample.DEV_QUOTAS, sample.SEED + year)
        todo = sample.to_todo(selected, set_name)
        path = sample.write_sample(
            todo, frame, shortfall, set_name, year, sample.DEV_QUOTAS, sample.SEED + year, 0
        )
        print(f"TEST {year}: рамка {len(frame)} точек, выбрано {len(todo)} -> {path}")
        if shortfall:
            print(f"недобор по стратам: {shortfall}")
    return 0


def cmd_report(set_name: str) -> int:
    import datetime as dt
    import json
    import subprocess

    import pandas as pd

    from kzfires.core.mask import build_mask
    from kzfires.eval import filter_eval as fe
    from kzfires.sources import archive

    cfg, params = fe.load_config()
    years = [DEV_YEAR] if set_name == "dev" else [2024, 2025]
    sets = ["dev"] if set_name == "dev" else [f"test{y}" for y in years]
    label_files = [f"labels/labels_{s}.csv" for s in sets]

    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", *label_files], capture_output=True, text=True
    ).stdout.strip()
    if set_name == "test" and dirty:
        # TEST считается только по закоммиченным меткам: иначе их можно поправить после взгляда
        print(f"метки не закоммичены:\n{dirty}", file=sys.stderr)
        return 1

    snpp = archive.load(range(min(years) - params.lookback_years, max(years)), satellites=("N",))
    parts, pops = [], {}
    for year, s, path in zip(years, sets, label_files, strict=True):
        lab = fe.attach_firms(fe.load_labels(Path(path)), archive.load([year]))
        lab["stratum"] = f"{year}/" + lab["stratum"]
        meta = json.loads(Path(f"labels/labels_todo_{s}.meta.json").read_text())
        pops[year] = {f"{year}/{k}": v for k, v in meta["population"].items()}
        parts.append(lab)
    masks = {y: build_mask(snpp, dt.datetime(y, 1, 1, tzinfo=dt.UTC), params) for y in years}

    out_dir = Path("data/eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    scopes = [(str(y), [p], pops[y]) for y, p in zip(years, parts, strict=True)]
    if len(years) > 1:
        scopes.append(("all", parts, {k: v for p in pops.values() for k, v in p.items()}))
    frames = []
    for scope, ps, pop in scopes:
        lab = pd.concat(ps, ignore_index=True)
        rep = fe.report(lab, fe.variants(lab, masks), pop)
        rep.insert(0, "scope", scope)
        frames.append(rep)
        classes = lab["class"].value_counts().sort_index().to_dict()
        print(f"\n## {set_name} {scope}: n = {len(lab)}, классы {classes}")
        print(fe.format_table(rep))
    pd.concat(frames).to_csv(out_dir / f"report_{set_name}.csv", index=False)

    if set_name == "test":
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        entry = {
            "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "git_sha": sha.strip(),
            "mask_params_hash": cfg["mask_params_hash"],
        }
        with (out_dir / "test_runs.jsonl").open("a") as f:
            f.write(json.dumps(entry) + "\n")
        n = sum(1 for _ in (out_dir / "test_runs.jsonl").open())
        print(f"\nTEST просмотрен раз: {n}")
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
    elif set_name == "check":
        # слепая проверка 20 точек DEV человеком: карточки те же, что у DEV
        session = Session(
            set_name="dev",
            todo_path=Path("labels/labels_todo_dev_check.csv"),
            labels_path=Path("labels/labels_dev_review.csv"),
            cache_dir=CACHE_DIR / "dev",
            pass_no=1,
        )
    else:
        session = Session(
            set_name=set_name,
            todo_path=Path(f"labels/labels_todo_{set_name}.csv"),
            labels_path=Path(f"labels/labels_{set_name}_review.csv"),
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
    smp = sub.add_parser("sample", help="выборка точек для разметки")
    smp.add_argument("--test", action="store_true", help="TEST: сезоны 2024 и 2025")
    rep = sub.add_parser("report", help="метрики фильтра на размеченных точках")
    rep.add_argument("--set", default="dev", choices=["dev", "test"])
    lab = sub.add_parser("label", help="страница разметки в браузере")
    lab.add_argument(
        "--set", default="dev", choices=["dev", "test2024", "test2025", "practice", "check"]
    )
    lab.add_argument("--pass", dest="pass_no", type=int, default=1, choices=[1, 2])
    lab.add_argument("--port", type=int, default=8765)
    lab.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "border":
        return cmd_border()
    if args.cmd == "sample":
        return cmd_sample_test([2024, 2025]) if args.test else cmd_sample()
    if args.cmd == "report":
        return cmd_report(args.set)
    if args.cmd == "label":
        return cmd_label(args.set, args.pass_no, args.port, not args.no_browser)
    print(f"{args.cmd}: ещё не реализовано", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
