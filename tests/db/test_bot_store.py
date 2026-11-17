from kzfires.bot.app import DbStore, Subscription


def test_save_get_delete(conn):
    store = DbStore(conn)
    store.save(1001, Subscription(43.12, 76.99, 15))
    assert store.get(1001) == Subscription(43.12, 76.99, 15)
    store.save(1001, Subscription(43.2, 77.0, 30))
    assert store.get(1001).radius_km == 30
    assert store.delete(1001) is True
    assert store.get(1001) is None
    assert store.delete(1001) is False
