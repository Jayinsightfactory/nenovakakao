"""Defects are posted in the week after their source shipment week."""
from datetime import date, timedelta


def upload_scope(row):
    year = int(row['event']['timestamp'].split('년')[0])
    week = int(row['extracted']['sequence'].split('-')[0])
    if not 2000 <= year <= 2100:
        raise ValueError('불량 원문 연도 확인 필요')
    following = (date.fromisocalendar(year, week, 1) + timedelta(days=7)).isocalendar()
    return {'year': following.year, 'week': following.week}
