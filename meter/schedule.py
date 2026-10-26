"""Offline price schedules. Official DeepSeek windows always use Beijing time."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = timezone(timedelta(hours=8), 'Asia/Shanghai')
PRICE_SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
HOLIDAY_SOURCE = 'https://www.gov.cn/zhengce/content/202511/content_7047090.htm'
HOLIDAYS_2026 = set()
for month, start, end in [(1, 1, 3), (2, 15, 23), (4, 4, 6), (5, 1, 5),
                           (6, 19, 21), (9, 25, 27), (10, 1, 7)]:
    HOLIDAYS_2026.update(f'2026-{month:02d}-{day:02d}' for day in range(start, end + 1))


def local_datetime(timestamp, name='local'):
    if name == 'local':
        return datetime.fromtimestamp(timestamp).astimezone()
    if name == 'Asia/Shanghai':
        return datetime.fromtimestamp(timestamp, SHANGHAI)
    return datetime.fromtimestamp(timestamp, ZoneInfo(name))


def deepseek_period(timestamp, options=None):
    options = options or {}
    now = datetime.fromtimestamp(timestamp, SHANGHAI)
    holidays = HOLIDAYS_2026 | set(options.get('deepseek_holidays', []))
    if now.weekday() >= 5 or now.date().isoformat() in holidays:
        return 'deepseek_offpeak'
    if str(now.year) not in {str(y) for y in options.get('deepseek_calendar_years', ['2026'])}:
        return 'calendar_unknown'
    minutes = now.hour * 60 + now.minute
    return 'deepseek_peak' if 540 <= minutes < 720 or 840 <= minutes < 1080 else 'deepseek_offpeak'


def minute(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Time must be HH:MM')
    hour, minutes = map(int, value.split(':'))
    return hour * 60 + minutes


def matches(price, timestamp, local_zone='local', options=None):
    mode = price.get('period', 'all')
    if mode == 'all':
        return True
    if mode in {'deepseek_peak', 'deepseek_offpeak'}:
        return deepseek_period(timestamp, options) == mode
    if mode != 'custom':
        raise ValueError('Unknown schedule')
    now = local_datetime(timestamp, price.get('timezone') or local_zone)
    start, end = minute(price.get('start', '00:00')), minute(price.get('end', '00:00'))
    days = {int(day.strip()) for day in str(price.get('weekdays', '1,2,3,4,5,6,7')).split(',')}
    if not days or not days <= set(range(1, 8)):
        raise ValueError('Invalid weekdays')
    clock = now.hour * 60 + now.minute
    # A Monday 22:00–02:00 entry includes Tuesday 01:00, using Monday's weekday.
    anchor = now - timedelta(days=1) if start > end and clock < end else now
    return anchor.isoweekday() in days and (start == end or
        (start <= clock < end if start < end else clock >= start or clock < end))


def deepseek_prices():
    entries = []
    for models, low in [(['deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp',
                         'DeepSeek-V4.1-Flash-1017'], ('1', '0.02', '4')),
                        (['deepseek-v4-pro', 'DeepSeek-V4-Pro-0813'], ('4.5', '0.15', '13.5'))]:
        from decimal import Decimal
        for model in models:
            for mode, factor in [('deepseek_peak', 2), ('deepseek_offpeak', 1)]:
                entries.append(dict(model=model, provider='', billing='api', currency='CNY',
                    input=str(Decimal(low[0]) * factor), cached=str(Decimal(low[1]) * factor),
                    output=str(Decimal(low[2]) * factor), period=mode,
                    name='DeepSeek 官方·' + ('高峰' if factor == 2 else '空闲'),
                    timezone='Asia/Shanghai', source=PRICE_SOURCE, verified_on='2026-10-26'))
    return entries
