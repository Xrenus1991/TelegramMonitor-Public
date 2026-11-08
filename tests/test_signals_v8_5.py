"""v8.5 signal triage, Unicode obfuscation, bounded API and read-only tests."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, func
from telegram_monitor.cloud import api
from telegram_monitor.cloud.analytics import signal_summary, place_mentions, cluster_posts, text_key
from telegram_monitor.cloud.db import Store, messages, now
from telegram_monitor.cloud.signal_filter import categorize
from telegram_monitor.cloud.smart_text import fix_mixed_script, contains_query, search_tokens

SAMPLE_TIME = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def row(index, text, classification=None):
    return {
        'key': f'100:{index}', 'channel_id': 100, 'message_id': index,
        'channel_name': 'Проверочный канал', 'timestamp': SAMPLE_TIME,
        'text': text, 'classification': json.dumps(classification or {}, ensure_ascii=False),
        'state': 'done',
    }


@pytest.mark.parametrize('sample, expected', [
    ('Гpaницa', 'граница'),
    ('Аpмeния', 'армения'),
    ('Рoccия', 'россия'),
    ('Пpoтecт', 'протест'),
    ('Гра\u200bница', 'граница'),
    ('граница', 'граница'),
])
def test_cyrillic_lookalikes(sample, expected):
    assert fix_mixed_script(sample) == expected


def test_no_unsafe_general_translation():
    assert fix_mixed_script('English users') == 'english users'
    assert fix_mixed_script('Երևան') == 'երեւան'  # Unicode NFKC expands Armenian ligature
    assert fix_mixed_script('abc123') == 'abc123'
    assert 'github' in search_tokens('GitHub.com') or 'github' in search_tokens('GitHub')


def test_mixed_script_for_duplicates_and_search():
    a = 'На границе Армении сегодня сообщили об ограничении движения автомобилей.'
    b = 'На гpaницe Аpмeнии сегодня сообщили об ограничении движения автомобилей.'
    assert contains_query(b, 'границе Армении')
    assert text_key(a) == text_key(b)
    assert len(cluster_posts([row(1, a), row(2, b)])) == 1


def test_place_names_in_three_languages_and_case_forms():
    assert place_mentions('Сообщение из Ереване') == ['Ереван']
    assert place_mentions('Արտակարգ դեպք Երևան') == ['Ереван']
    assert place_mentions('Road conditions around Yerevan and Gyumri') == ['Гюмри', 'Ереван']
    assert contains_query('В городе Երևան закрыта улица', 'Yerevan')
    assert contains_query('Из города Ереване поступило сообщение', 'Ереван')
    assert contains_query('Сегодня granica открыта', 'граница')
    assert not contains_query('Только Гюмри', 'Ереван')


def test_query_word_boundary_and_empty():
    assert not contains_query('армения', 'арм')
    assert not contains_query('армения', '')
    assert not contains_query('армения', ' '.join('a' for i in range(16)))
    assert not contains_query('English only', 'русский')


def test_candidate_and_noise_triage_are_non_authoritative():
    c, reason = categorize('В Ереване сегодня произошло отключение водоснабжения.')
    assert c == 'candidate' and reason
    c, reason = categorize('Реклама вакансии: зарплата высокая, пишите в личку')
    assert c == 'likely_ad' and reason
    assert categorize('Добрый день, ребята')[0] == 'other'
    assert categorize('Заголовок без проверенной информации', {'priority':'high'})[0] == 'candidate'
    assert categorize('Ограничение движения?', {'relevant':True})[0] == 'candidate'
    # Advertising must not become an incident just because an untrusted model flagged priority.
    assert categorize('Реклама вакансии, зарплата 5000', {'priority':'high'})[0] == 'likely_ad'


def test_signal_summary_sampling_and_metadata():
    result = signal_summary([
        row(1,'Сегодня в Ереване произошло отключение воды'),
        row(2,'Реклама вакансии, зарплата высокая, пишите в личку'),
        row(3,'Добрый вечер')], True, hours=72, category='all')
    assert result['sample_truncated'] is True
    assert result['scanned_messages'] == 3
    assert result['category_counts'] == {'candidate':1, 'likely_ad':1, 'other':1}
    assert len(result['items']) == 3
    assert result['items'][0]['source'] == 'Проверочный канал'
    assert result['items'][0]['places_mentioned'] == ['Ереван']
    result = signal_summary([row(1,'В Ереване перекрыта улица')], False,
                            hours=24, category='candidate', query='Yerevan')
    assert result['matched_count'] == 1
    with pytest.raises(ValueError):
        signal_summary([], False, hours=24, category='invalid')


def test_capped_response_without_losing_counts():
    posts = [row(i+1, f'В Ереване произошло отключение воды, сообщение {i}') for i in range(65)]
    result = signal_summary(posts, False, hours=72, category='candidate')
    assert len(result['items']) == 50
    assert result['matched_count'] == 65
    assert result['items_truncated'] is True


def test_api_auth_and_query_validation(tmp_path, monkeypatch):
    store = Store('sqlite:///'+str(tmp_path/'signals.sqlite'))
    with store.engine.begin() as c:
        c.execute(insert(messages).values(**{
            **row(1, 'На гpaницe Армении сегодня перекрыта дорога.'),
            'timestamp': now()-timedelta(minutes=15),
            'tries':0, 'deep_tries':0, 'deep_state':'skip', 'created_at':now(),
        }))
    monkeypatch.setattr(api,'store',lambda: Store('sqlite:///'+str(tmp_path/'signals.sqlite'), read_only=True))
    monkeypatch.setenv('DASHBOARD_PASSWORD', 'strong-dummy-password-only-for-tests')
    client = TestClient(api.app)
    assert client.get('/api/analytics/signals').status_code == 401
    auth=('admin','strong-dummy-password-only-for-tests')
    for hours in (24,72,168):
        resp=client.get('/api/analytics/signals',params={
            'hours':hours,'category':'candidate','q':'границе Армении'},auth=auth)
        assert resp.status_code == 200, resp.text
        assert resp.json()['matched_count'] == 1
    for params in ({'hours':25},{'category':'deleted'},{'q':'а'},{'q':'A'*161}):
        assert client.get('/api/analytics/signals',params=params,auth=auth).status_code == 422
    with store.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(messages)).scalar_one() == 1
    assert not any(name in resp.text.lower() for name in ('password','database_url','traceback'))


def test_ui_has_no_xss_innerhtml_and_cautious_explanation():
    html=Path(api.__file__).with_name('dashboard_v8_3.html').read_text(encoding='utf-8')
    assert 'Сигналы и умный поиск · v8.5' in html
    assert 'не проверка фактов' in html
    assert 'signal-submit' in html
    assert 'innerHTML' not in html
