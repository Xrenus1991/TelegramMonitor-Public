"""Queue concurrency contracts in dedicated disposable PostgreSQL only."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
import pytest
from sqlalchemy import select,update,delete,func
from telegram_monitor.cloud import db
URL="postgresql+psycopg://postgres:fixture@127.0.0.1:5432/queue_fixture"
pytestmark=pytest.mark.skipif(not os.getenv("TM_QUEUE_TEST_PG"),reason="dedicated PostgreSQL fixture only")
@pytest.fixture
def store():
    assert os.environ["TM_QUEUE_TEST_PG"]==URL
    s=db.Store(URL)
    with s.engine.begin() as conn:
        for table in reversed(db.md.sorted_tables):conn.execute(delete(table))
    yield s
    s.engine.dispose()
def seed(s,count=2):
    s.ingest(111,[dict(channel_id=111,message_id=i,channel_name="synthetic",
             timestamp=db.now(),text="Synthetic queue fixture") for i in range(1,count+1)])
def test_locked_candidate_is_skipped_and_claims_are_disjoint(store):
    seed(store)
    with store.engine.begin() as lock:
        lock.execute(select(db.messages).where(db.messages.c.key=="111:1").with_for_update()).all()
        rows,token=store.claim_classify(2)
        assert [r["key"] for r in rows]==["111:2"]
    remaining,other=store.claim_classify(2)
    assert [r["key"] for r in remaining]==["111:1"] and token!=other
    assert store.claim_classify(2)[0]==[]
def test_expired_owner_cannot_commit_and_retry_is_bounded(store):
    seed(store,1)
    rows,old=store.claim_classify(1)
    with store.engine.begin() as conn:
        conn.execute(update(db.messages).values(lease_until=db.now()-timedelta(seconds=1)))
    recovered,new=store.claim_classify(1)
    assert recovered and old!=new
    with pytest.raises(RuntimeError):
        store.finish_classify(old,{"111:1":{"priority":"normal"}})
    store.retry(rows,old,"classify")
    with store.engine.connect() as conn:
        row=conn.execute(select(db.messages)).mappings().one()
        assert row["claim_id"]==new and row["state"]=="processing"
    store.finish_classify(new,{"111:1":{"priority":"normal"}},deep_enabled=False)
    with store.engine.connect() as conn:
        row=conn.execute(select(db.messages)).mappings().one()
        assert row["state"]=="done" and row["tries"]==2 and row["deep_state"]=="skip"
def test_concurrent_budget_reservation_and_single_settlement(store):
    barrier=Barrier(2)
    def reserve():
        barrier.wait(timeout=10)
        return store.reserve("synthetic-model",600000,1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:reserve(),range(2)))
    ids=[x for x in results if x]
    assert len(ids)==1
    with store.engine.connect() as conn:
        assert conn.execute(select(func.sum(db.budgets.c.used_micro_usd))).scalar_one()==600000
        assert conn.execute(select(func.count()).select_from(db.requests)).scalar_one()==1
    store.settle(ids[0],150000)
    with pytest.raises(ValueError):store.settle(ids[0],150000)
    with store.engine.connect() as conn:
        assert conn.execute(select(func.sum(db.budgets.c.used_micro_usd))).scalar_one()==150000

def test_crash_on_last_attempt_becomes_failed(store):
    seed(store,1)
    with store.engine.begin() as conn:
        conn.execute(update(db.messages).values(state="processing",tries=6,claim_id="dead-owner",
                     lease_until=db.now()-timedelta(seconds=1)))
    assert store.claim_classify(1)[0]==[]
    assert store.claim_classify(1)[0]==[]
    with store.engine.connect() as conn:
        row=conn.execute(select(db.messages)).mappings().one()
        assert row["state"]=="failed" and row["tries"]==6
        assert row["claim_id"] is None and row["lease_until"] is None
