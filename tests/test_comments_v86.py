"""Comment collection regression coverage, no Telegram network calls."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, insert, select, func
from telegram_monitor.cloud.db import md, messages
from telegram_monitor.cloud.comments import (
    CommentStore, comments, scans, masked, channel_ids_from_env,
    normalise_comment, collect_thread)

NOW = datetime.now(timezone.utc)

@pytest.fixture()
def database(tmp_path):
    url = f'sqlite:///{tmp_path}/test.db'
    engine = create_engine(url)
    md.create_all(engine)
    engine.dispose()
    store = CommentStore(url, initialize=True)
    yield store
    store.dispose()


def add_post(store, channel, post_id, *, age_minutes=0):
    dt = NOW-timedelta(minutes=age_minutes)
    with store.engine.begin() as con:
        con.execute(insert(messages).values(
            key=f'{channel}:{post_id}',channel_id=channel,message_id=post_id,
            channel_name='Публичный канал',timestamp=dt,text='Новость о событии',
            state='done',tries=0,deep_tries=0,deep_state='skip',created_at=dt))


def comment(channel=1,post_id=3,group=55,msg_id=10,text='На границе перекрыли дорогу'):
    return {'key':f'{channel}:{post_id}:{group}:{msg_id}', 'channel_id':channel,
            'post_id':post_id,'discussion_id':group,'comment_id':msg_id,
            'timestamp':NOW,'text':text}


@pytest.mark.parametrize('raw,expected', [
    ('',set()), ('12',{12}),('12, 345',{12,345}), ('12,12',{12}),
])
def test_valid_channels(raw,expected):
    assert channel_ids_from_env(raw)==expected

@pytest.mark.parametrize('raw',['0','-1','all','123,','a','123;456','  3.0  ', '1,'*100+'2'])
def test_invalid_channels(raw):
    with pytest.raises(ValueError): channel_ids_from_env(raw)


def test_masking():
    assert '[EMAIL]' in masked('Почта: news@example.com')
    assert '[PHONE]' in masked('Телефон: +374 91 123 456')
    assert '[HANDLE]' in masked('Напишите @user_handle')


def test_normalise_no_identity():
    raw=SimpleNamespace(id=10, message='Гpaницa', peer_id=SimpleNamespace(channel_id=99),
                        sender_id=123456, from_id=98765, date=NOW)
    got=normalise_comment(raw,7,42)
    assert got['key']=='7:42:99:10'
    assert set(got)=={'key','channel_id','post_id','discussion_id','comment_id','timestamp','text','potential_direct_threat','author_user_id'}
    assert got['author_user_id'] is None and not got['potential_direct_threat']
    assert got['text']=='Гpaницa'
    assert normalise_comment(SimpleNamespace(id=2,message='only',peer_id=None,date=NOW),7,42) is None
    assert normalise_comment(SimpleNamespace(id=2,message='',peer_id=SimpleNamespace(channel_id=99),date=NOW),7,42) is None


def test_idempotent_save(database):
    c=comment()
    assert database.save_thread(1,3,[c],capped=False)==1
    assert database.save_thread(1,3,[c],capped=False)==0
    with database.engine.connect() as cn:
        assert cn.execute(select(func.count()).select_from(comments)).scalar_one()==1
        assert cn.execute(select(func.count()).select_from(scans)).scalar_one()==1


def test_bad_parent_atomic(database):
    with pytest.raises(ValueError): database.save_thread(7,3,[comment(channel=1)],capped=False)
    assert database.overview()['total_comments']==0


def test_candidates_new_and_fair(database):
    for ch in (1,2):
        for i in range(1,5): add_post(database,ch,i)
    candidates=database.choose_posts({1,2},lookback_hours=72,per_cycle=10,retry_seconds=600)
    assert len(candidates)==4
    assert {x['channel_id'] for x in candidates} == {1,2}
    database.save_thread(1,3,[],capped=False)
    candidates=database.choose_posts({1},lookback_hours=72,per_cycle=10,retry_seconds=600)
    assert (1,3) not in [(x['channel_id'],x['post_id']) for x in candidates]


def test_candidates_only_allowed(database):
    add_post(database,1,1)
    add_post(database,2,2)
    assert {c['channel_id'] for c in database.choose_posts({2},lookback_hours=72,per_cycle=10,retry_seconds=600)}=={2}
    assert database.choose_posts(set(),lookback_hours=72,per_cycle=10,retry_seconds=600)==[]


def test_candidates_old_posts_ignored(database):
    add_post(database,1,1,age_minutes=72*60+2)
    assert database.choose_posts({1},lookback_hours=72,per_cycle=10,retry_seconds=600)==[]


def test_thread_summary(database):
    add_post(database,1,3)
    database.save_thread(1,3,[comment(text='Пожар в Ереване'), comment(msg_id=12,text='Ищу работу, зарплата 100000')], capped=False)
    result=database.overview(hours=24)
    assert result['total_comments']==2 and result['threads'][0]['count']==2
    result=database.thread(1,3)
    assert result['count']==2 and result['displayed']==2
    assert set(result['category_counts_in_displayed']).issubset({'candidate','likely_ad','other'})
    assert all('sender_id' not in x for x in result['items'])


def test_readonly_never_writes(database):
    reader=CommentStore(str(database.engine.url),read_only=True)
    assert reader.overview()['initialized'] is True
    with pytest.raises(RuntimeError):reader.save_thread(1,1,[],capped=False)
    with pytest.raises(RuntimeError):reader.choose_posts({1},lookback_hours=24,per_cycle=1,retry_seconds=60)
    reader.dispose()


def test_retention(database):
    database.save_thread(1,3,[comment()],capped=False)
    with database.engine.begin() as con:
        con.execute(comments.update().values(timestamp=NOW-timedelta(days=31)))
    assert database.prune(retention_days=30)==1
    assert database.overview(hours=168)['total_comments']==0

class FakeClient:
    def __init__(self, rows, fail=False): self.rows=rows;self.fail=fail;self.calls=[]
    async def get_messages(self, entity, ids):
        return SimpleNamespace(replies=SimpleNamespace(comments=True, replies=len(self.rows)))
    async def iter_messages(self, entity, **kw):
        self.calls.append((entity.id,kw))
        for row in self.rows[:kw['limit']]:
            yield row
        if self.fail: raise OSError('connection')

def test_collect_thread_and_dedup(database):
    raw=SimpleNamespace(id=20,message='Обсуждение в Ереване',peer_id=SimpleNamespace(channel_id=100),date=NOW,sender_id=999)
    client=FakeClient([raw])
    entity=SimpleNamespace(id=1)
    assert asyncio.run(collect_thread(client,entity,database,44,max_comments=10))==(1,1)
    assert asyncio.run(collect_thread(client,entity,database,44,max_comments=10))==(0,1)
    assert client.calls[0][1]=={'reply_to':44,'limit':10}


def test_failed_collect_does_not_mark_scanned(database):
    raw=SimpleNamespace(id=20,message='Test',peer_id=SimpleNamespace(channel_id=100),date=NOW)
    client=FakeClient([raw],fail=True)
    with pytest.raises(OSError):asyncio.run(collect_thread(client,SimpleNamespace(id=1),database,44,max_comments=10))
    with database.engine.connect() as con:
        assert con.execute(select(func.count()).select_from(scans)).scalar_one()==0
        assert con.execute(select(func.count()).select_from(comments)).scalar_one()==0


def test_thread_unknown(database):
    assert database.thread(2,99)['count']==0
    assert database.overview(hours=24)['total_comments']==0


def test_no_tables_when_disabled(tmp_path):
    path=tmp_path/'empty.db'
    reader=CommentStore(f'sqlite:///{path}',read_only=True)
    assert not reader.initialized()
    reader.dispose()
