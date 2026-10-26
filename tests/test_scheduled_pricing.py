import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from meter.pricing import estimate
from meter.schedule import SHANGHAI, deepseek_period
from meter.store import Store
from meter.usage import Usage, parse_usage
from meter.report import plain, period_start


def ts(day='2026-10-26', clock='09:00'):
    return datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=SHANGHAI).timestamp()


class ScheduledPriceTests(unittest.TestCase):
    def test_utc_does_not_require_external_timezone_database(self):
        from unittest.mock import patch
        from meter.schedule import local_datetime
        with patch('meter.schedule.ZoneInfo', side_effect=KeyError('No tzdata')):
            self.assertEqual(local_datetime(ts(), 'UTC').utcoffset().total_seconds(), 0)

    def official(self, clock='09:00', model='deepseek-flash', day='2026-10-26', **options):
        return estimate(Usage(1000000, 1000000, 800000), 'unknown', 'official', model, [],
                        ts(day, clock), 'Asia/Shanghai', dict(deepseek_official=True, **options))

    def test_every_peak_boundary_is_half_open(self):
        for clock, expected in [('08:59', False), ('09:00', True), ('11:59', True),
                ('12:00', False), ('13:59', False), ('14:00', True), ('17:59', True), ('18:00', False)]:
            with self.subTest(clock=clock):
                self.assertEqual(self.official(clock)['period'] == 'deepseek_peak', expected)

    def test_flash_split_cost_and_offpeak_halving(self):
        high = self.official(); low = self.official('12:00')
        self.assertEqual(Decimal(high['input_cost']), Decimal('0.4'))
        self.assertEqual(Decimal(high['cached_cost']), Decimal('0.032'))
        self.assertEqual(Decimal(high['output_cost']), Decimal('8'))
        self.assertEqual(Decimal(high['cost']), Decimal('8.432'))
        self.assertEqual(Decimal(low['cost']) * 2, Decimal(high['cost']))

    def test_pro_prices_are_separate(self):
        self.assertEqual(Decimal(self.official(model='deepseek-v4-pro')['cost']), Decimal('29.04'))
        self.assertEqual(Decimal(self.official('18:00', model='deepseek-v4-pro')['cost']), Decimal('14.52'))

    def test_official_aliases_and_unknown_models(self):
        for model in ['deepseek-v4-flash', 'deepseek-v4-flash-vision-exp', 'DeepSeek-V4.1-Flash-1017']:
            self.assertEqual(self.official(model=model)['cost'], self.official()['cost'])
        self.assertIsNone(self.official(model='deepseek-chat')['cost'])

    def test_weekend_including_adjusted_working_saturday_is_offpeak(self):
        self.assertEqual(self.official(day='2026-10-10')['period'], 'deepseek_offpeak')

    def test_weekday_holiday_is_offpeak(self):
        self.assertEqual(self.official(day='2026-10-01')['period'], 'deepseek_offpeak')

    def test_unknown_future_calendar_is_not_silently_priced(self):
        result = self.official(day='2027-01-04')
        self.assertEqual(result['period'], 'calendar_unknown'); self.assertIsNone(result['cost'])
        result = self.official(day='2027-01-04', deepseek_calendar_years=['2026', '2027'],
                               deepseek_holidays=['2027-01-04'])
        self.assertEqual(result['period'], 'deepseek_offpeak')

    def test_official_timezone_is_independent_of_server_display_zone(self):
        result = estimate(Usage(100, 10, 0), 'unknown', 'official', 'deepseek-flash', [],
                          ts(), 'UTC', {'deepseek_official': True})
        self.assertEqual(result['period'], 'deepseek_peak')
        self.assertIn('01:00:00+00:00', result['local_time'])
        self.assertIn('09:00:00+08:00', result['price_snapshot']['matched_at'])

    def test_provider_allowlist_does_not_price_resellers(self):
        self.assertIsNone(self.official(deepseek_provider_ids=['other'])['cost'])

    def custom(self, **changes):
        return dict(dict(model='model', provider='', billing='api', currency='CNY',
            input='2', cached='0.2', output='8', period='custom',
            start='22:00', end='02:00', weekdays='1', name='Monday night'), **changes)

    def test_cross_midnight_retains_start_day_and_end_is_exclusive(self):
        price = self.custom()
        for day, clock, priced in [('2026-10-26', '22:00', True), ('2026-10-27', '01:59', True),
                ('2026-10-27', '02:00', False), ('2026-10-27', '22:00', False)]:
            result = estimate(Usage(100, 10, 0), 'api', 'p', 'model', [price], ts(day, clock), 'Asia/Shanghai')
            self.assertEqual(result['cost'] is not None, priced)

    def test_specific_period_overrides_all_day_and_gap_uses_fallback(self):
        prices = [self.custom(), self.custom(period='all', input='9', name='fallback')]
        a = estimate(Usage(100, 10, 0), 'api', 'p', 'model', prices, ts(clock='23:00'), 'Asia/Shanghai')
        b = estimate(Usage(100, 10, 0), 'api', 'p', 'model', prices, ts(clock='15:00'), 'Asia/Shanghai')
        self.assertEqual(a['price_snapshot']['name'], 'Monday night')
        self.assertEqual(b['price_snapshot']['name'], 'fallback')

    def test_equal_priority_overlap_is_unknown(self):
        p = self.custom()
        result = estimate(Usage(100, 10, 0), 'api', 'p', 'model', [p, dict(p, input='9')],
                          ts(clock='23:00'), 'Asia/Shanghai')
        self.assertEqual(result['period'], 'ambiguous'); self.assertIsNone(result['cost'])

    def test_bad_timezone_and_time_are_visible_unknowns(self):
        for changes in [dict(start='25:00'), dict(weekdays='0'), dict(timezone='NoSuch/Zone')]:
            result = estimate(Usage(100, 10, 0), 'api', 'p', 'model', [self.custom(**changes)], ts(), 'Asia/Shanghai')
            self.assertIsNone(result['cost']); self.assertIn('invalid_', result['period'])

    def test_explicit_rules_override_builtin_without_using_wrong_gap_price(self):
        p = self.custom(model='deepseek-flash', start='22:00', end='23:00')
        result = estimate(Usage(100, 10, 0), 'api', 'p', 'deepseek-flash', [p], ts(),
                          'Asia/Shanghai', {'deepseek_official': True})
        self.assertIsNone(result['cost'])

    def test_subscription_and_unknown_cache_are_not_priced(self):
        for billing, usage in [('subscription', Usage(100, 10, 0)), ('unknown', Usage(100, 10))]:
            result = estimate(usage, billing, 'official', 'deepseek-flash', [], ts(),
                              'Asia/Shanghai', {'deepseek_official': True})
            self.assertIsNone(result['cost'])

    def test_inconsistent_deepseek_split_is_unknown(self):
        u = parse_usage(dict(prompt_tokens=100, completion_tokens=10,
                             prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=40))
        self.assertIsNone(u.cached)


class ScheduledStorageTests(unittest.TestCase):
    def test_delay_uses_receipt_timestamp_and_snapshots_survive_price_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory, retention_days=3650)
            options = {'deepseek_official': True}
            def add(request, clock):
                store.add(request_id=request, scope='g', conversation='', provider='p', model='deepseek-flash',
                          usage=Usage(1000000, 1000000, 800000), timestamp=ts(clock=clock),
                          local_zone='Asia/Shanghai', pricing_options=options)
            add('one', '11:59'); add('two', '12:00')
            options['deepseek_official'] = False
            with store.connect() as db:
                rows = db.execute('SELECT * FROM calls ORDER BY ts').fetchall()
            self.assertEqual([r['price_period'] for r in rows], ['deepseek_peak', 'deepseek_offpeak'])
            self.assertEqual([Decimal(r['cost']) for r in rows], [Decimal('8.432'), Decimal('4.216')])
            snapshot = json.loads(rows[0]['price_snapshot'])
            self.assertEqual(snapshot['cached'], '0.04'); self.assertEqual(snapshot['verified_on'], '2026-10-26')
            report = store.report(); self.assertEqual(len(report['periods']), 2)
            self.assertIn('未命中输入', plain(report, '今日', 'test'))

    def test_old_database_is_migrated_without_repricing_or_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            # Recreate an actual v0.1.0 table, then reopen with the new Store.
            with store.connect() as db:
                db.execute('DROP TABLE calls')
                db.execute('''CREATE TABLE calls (id TEXT PRIMARY KEY,ts REAL,scope TEXT,conversation TEXT,
                    provider TEXT,model TEXT,source TEXT,status TEXT,input INTEGER,output INTEGER,cached INTEGER,
                    written INTEGER,billing TEXT,currency TEXT,cost TEXT,saving TEXT)''')
                db.execute('INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('old', time.time(), 's', '', 'p', 'm', 'provider', 'completed', 10, 2, 0, None, 'api', 'CNY', '0.123', '0'))
            again = Store(directory)
            with again.connect() as db:
                old = db.execute('SELECT * FROM calls').fetchone()
            self.assertEqual(old['cost'], '0.123'); self.assertEqual(old['price_period'], 'legacy')
            self.assertEqual(old['price_snapshot'], '{}'); self.assertEqual(again.report()['calls'], 1)

    def test_reporting_day_can_use_utc_instead_of_beijing(self):
        from unittest.mock import patch
        with patch('meter.report.time.time', return_value=ts(clock='07:00')):
            self.assertEqual(period_start('今日', local_zone='UTC'),
                             datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp())


class StreamArrivalTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_timestamp_is_final_usage_arrival_not_generator_close(self):
        import types
        from unittest.mock import patch
        from meter.observer import Observer
        rows = []
        async def emit(row): rows.append(row)
        with patch('meter.observer.time.time', return_value=101.0) as clock:
            class Provider:
                def meta(self): return types.SimpleNamespace(id='p')
                def get_model(self): return 'm'
                async def text_chat_stream(self):
                    yield types.SimpleNamespace(usage={'prompt_tokens':100,'completion_tokens':10})
                    clock.return_value = 102.0
                    yield types.SimpleNamespace(usage={'prompt_tokens':100,'completion_tokens':20})
                    clock.return_value = 999.0  # cleanup may finish much later
            provider = Provider(); observer = Observer(emit); observer.install([provider])
            try:
                async for _ in provider.text_chat_stream(): pass
            finally:
                observer.restore()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['timestamp'], 102.0)
        self.assertEqual(rows[0]['usage'].output, 20)
