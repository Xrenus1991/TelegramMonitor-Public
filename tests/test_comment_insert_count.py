"""Insert counters remain correct when the driver does not report rowcount."""
from sqlalchemy.engine import Connection
from sqlalchemy.sql.dml import Insert
from tests.test_comments_v86 import database, comment


def test_unknown_insert_rowcount_does_not_corrupt_count(database, monkeypatch):
    original = Connection.execute

    class UnknownCount:
        rowcount = -1

        def __init__(self, result):
            self.result = result

        def __getattr__(self, name):
            return getattr(self.result, name)

    def execute(self, statement, *args, **kwargs):
        result = original(self, statement, *args, **kwargs)
        if isinstance(statement, Insert) and statement.table.name == "tm_post_comments":
            return UnknownCount(result)
        return result

    monkeypatch.setattr(Connection, "execute", execute)
    assert database.save_thread(1, 3, [comment()], capped=False) == 1
    assert database.save_thread(1, 3, [comment()], capped=False) == 0
    assert database.save_thread(1, 3, [comment(), comment(msg_id=11)], capped=False) == 1
    assert database.thread(1, 3)["count"] == 2
