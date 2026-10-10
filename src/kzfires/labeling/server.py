"""Локальный сервер разметки: страница в браузере, карточки точек, запись меток.

Слушает только 127.0.0.1. Карточки готовятся заранее в фоне по порядку очереди,
поэтому человек почти никогда не ждёт сети.
"""

import datetime as dt
import json
import threading
import webbrowser
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kzfires.labeling import card as cards
from kzfires.labeling import store

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_FIELDS = ("label_id", "acq_at", "lat", "lon")


@dataclass
class Session:
    set_name: str
    todo_path: Path
    labels_path: Path
    cache_dir: Path
    pass_no: int = 1
    practice: bool = False


class LabelApp:
    def __init__(self, session: Session, prefetch_workers: int = 2):
        from kzfires.sources import sentinel2 as s2
        from kzfires.sources.osm import Nearest, load_industry, load_settlements

        self.s = session
        self.todo = store.read_todo(session.todo_path)
        self.points = {r["label_id"]: r for r in self.todo.to_dict("records")}
        self.client = s2.open_client()
        self.settlements = Nearest(load_settlements())
        self.industry = Nearest(load_industry())
        self.lock = threading.Lock()
        self.futures: dict[str, Future] = {}
        self.pool = ThreadPoolExecutor(prefetch_workers)

    def labels(self):
        return store.read_labels(self.s.labels_path)

    def queue(self) -> list[str]:
        return store.queue(self.todo, self.labels(), self.s.pass_no)

    def order(self) -> list[str]:
        """Все точки прохода в порядке показа: нужен для кнопки «назад»."""
        if self.s.pass_no == 1:
            return self.todo["label_id"].tolist()
        done = self.labels()
        done = done.loc[done["pass"] == str(self.s.pass_no), "label_id"].tolist()
        return list(dict.fromkeys(done + self.queue()))

    def card_future(self, label_id: str) -> Future:
        with self.lock:
            fut = self.futures.get(label_id)
            if fut is None:
                cached = cards.load_cached(self.s.cache_dir, label_id)
                if cached is not None:
                    fut = Future()
                    fut.set_result(cached)
                else:
                    point = self.points[label_id]
                    fut = self.pool.submit(
                        cards.build,
                        point,
                        self.s.cache_dir,
                        self.client,
                        self.settlements,
                        self.industry,
                    )
                self.futures[label_id] = fut
            return fut

    def prefetch_all(self) -> None:
        for label_id in self.queue():
            self.card_future(label_id)

    def state(self) -> dict:
        q = self.queue()
        order = self.order()
        labels = self.labels()
        warning = None
        if self.s.pass_no == 2:
            days = store.days_since_first_label(labels, dt.datetime.now(dt.UTC))
            if days is not None and days < store.RELABEL_MIN_DAYS:
                warning = (
                    f"С первой метки прошло {days:.1f} сут. Повторный проход — "
                    f"не раньше чем через {store.RELABEL_MIN_DAYS}."
                )
        return {
            "set": self.s.set_name,
            "pass": self.s.pass_no,
            "practice": self.s.practice,
            "total": len(order),
            "done": len(order) - len(q),
            "next": q[0] if q else None,
            "order": order,
            "warning": warning,
        }

    def point(self, label_id: str) -> dict:
        p = self.points[label_id]
        try:
            card = self.card_future(label_id).result(timeout=120)
            error = None
        except Exception as e:  # noqa: BLE001
            # сеть упала — карточку можно пересобрать при следующем открытии
            with self.lock:
                self.futures.pop(label_id, None)
            card, error = None, f"{type(e).__name__}: {e}"
        mine = store.latest(self.labels())
        mine = mine[(mine["label_id"] == label_id) & (mine["pass"] == str(self.s.pass_no))]
        out = {k: p[k] for k in PUBLIC_FIELDS}
        out.update(card=card, error=error)
        out["my_label"] = mine.iloc[-1].to_dict() if len(mine) else None
        if self.s.practice:
            out["hint"] = p.get("hint", "")
        return out

    def save(self, form: dict) -> dict:
        label_id = form["label_id"]
        try:
            card = self.card_future(label_id).result(timeout=120)
        except Exception:  # noqa: BLE001
            # снимки не загрузились, а «неясно» поставить всё равно можно
            card = {}
        row = store.make_row(
            self.points[label_id], card, form, self.s.pass_no, dt.datetime.now(dt.UTC)
        )
        store.append(self.s.labels_path, row)
        return self.state()


def make_handler(app: LabelApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=HTTPStatus.OK):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._send(
                    HTTPStatus.OK,
                    (STATIC_DIR / "index.html").read_bytes(),
                    "text/html; charset=utf-8",
                )
            if url.path == "/api/state":
                return self._json(app.state())
            if url.path == "/api/point":
                label_id = q.get("id", [""])[0]
                if label_id not in app.points:
                    return self._json({"error": "нет такой точки"}, HTTPStatus.NOT_FOUND)
                return self._json(app.point(label_id))
            if url.path.startswith("/img/"):
                parts = url.path.split("/")
                if (
                    len(parts) == 4
                    and parts[2] in app.points
                    and parts[3].removesuffix(".png") in cards.IMAGES
                ):
                    f = app.s.cache_dir / parts[2] / parts[3]
                    if f.exists():
                        return self._send(HTTPStatus.OK, f.read_bytes(), "image/png")
            return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/api/label":
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            length = int(self.headers.get("Content-Length", 0))
            try:
                form = json.loads(self.rfile.read(length))
                if form.get("label_id") not in app.points:
                    raise ValueError("нет такой точки")
                return self._json(app.save(form))
            except (ValueError, KeyError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

    return Handler


def serve(session: Session, port: int = 8765, open_browser: bool = True) -> None:
    app = LabelApp(session)
    threading.Thread(target=app.prefetch_all, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"разметка: {url}  (остановить: Ctrl+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.pool.shutdown(wait=False, cancel_futures=True)
