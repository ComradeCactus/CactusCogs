import asyncio
import json
from datetime import datetime, time, timedelta, timezone

from casino.lotto import (
    LottoStore,
    classify_tickets,
    draw_numbers,
    next_draw_at,
    parse_draw_time,
    quick_pick,
    split_jackpot,
)


def test_draw_time_and_next_daily_drawing_are_utc():
    draw_time = parse_draw_time("20:30")
    before = datetime(2026, 10, 4, 20, 29, tzinfo=timezone.utc)
    after = datetime(2026, 10, 4, 20, 31, tzinfo=timezone.utc)

    assert next_draw_at(draw_time, before) == datetime(
        2026, 10, 4, 20, 30, tzinfo=timezone.utc
    )
    assert next_draw_at(draw_time, after) == datetime(
        2026, 10, 5, 20, 30, tzinfo=timezone.utc
    )


def test_last_completed_drawing_prevents_a_second_same_day_drawing():
    draw_time = parse_draw_time("22:00")
    now = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)

    assert next_draw_at(draw_time, now, last_drawn="2026-10-04") == datetime(
        2026, 10, 5, 22, 0, tzinfo=timezone.utc
    )


def test_quick_picks_and_draws_contain_six_unique_numbers():
    for numbers in (quick_pick(), draw_numbers()):
        assert len(numbers) == 6
        assert len(set(numbers)) == 6
        assert all(0 <= number <= 99 for number in numbers)


def test_winner_tiers_require_all_numbers_and_prefer_exact_order():
    exact_ticket = {"numbers": [1, 2, 3, 4, 5, 6]}
    unordered_ticket = {"numbers": [6, 5, 4, 3, 2, 1]}
    non_winner = {"numbers": [1, 2, 3, 4, 5, 7]}

    exact, unordered = classify_tickets(
        [exact_ticket, unordered_ticket, non_winner], [1, 2, 3, 4, 5, 6]
    )

    assert exact == [exact_ticket]
    assert unordered == [unordered_ticket]


def test_jackpot_is_split_equally_with_remainder_preserved():
    tickets = [{"id": 1}, {"id": 2}, {"id": 3}]
    shares = split_jackpot(10, tickets)

    assert [share for _, share in shares] == [4, 3, 3]
    assert sum(share for _, share in shares) == 10


def test_json_ticket_store_persists_and_expires_tickets(tmp_path):
    async def run_test():
        store = LottoStore(tmp_path / "lotto.json")
        now = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)
        draw_time = time(20, 0)
        draw_date, entries = await store.add_tickets(
            7, draw_time, 42, [[0, 1, 2, 3, 4, 5]], now=now
        )
        assert draw_date == "2026-10-04"
        assert entries[0]["user_id"] == 42
        assert entries[0]["numbers"][0] == 0
        assert json.loads((tmp_path / "lotto.json").read_text())["guilds"]["7"][
            "tickets"
        ] == entries

        reloaded = LottoStore(tmp_path / "lotto.json")
        assert await reloaded.tickets_for_user(7, 42, "2026-10-04") == entries

        expired = await reloaded.expire_before(7, "2026-10-05")
        assert expired == entries
        assert await reloaded.tickets(7) == []

    asyncio.run(run_test())


def test_completed_drawing_deletes_only_its_tickets(tmp_path):
    async def run_test():
        store = LottoStore(tmp_path / "lotto.json")
        draw_time = time(20, 0)
        before = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)
        today, _ = await store.add_tickets(
            7, draw_time, 42, [[1, 2, 3, 4, 5, 6]], now=before
        )
        tomorrow, _ = await store.add_tickets(
            7,
            draw_time,
            42,
            [[6, 5, 4, 3, 2, 1]],
            now=before + timedelta(hours=2),
        )
        expired = await store.complete_drawing(7, today)

        assert len(expired) == 1
        assert expired[0]["draw_date"] == today
        assert [ticket["draw_date"] for ticket in await store.tickets(7)] == [
            tomorrow
        ]
        assert await store.last_drawn(7) == today

    asyncio.run(run_test())


def test_tickets_added_after_a_drawing_are_for_the_following_day(tmp_path):
    async def run_test():
        store = LottoStore(tmp_path / "lotto.json")
        draw_time = time(20, 0)
        before = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)
        draw_date, _ = await store.add_tickets(
            7, draw_time, 42, [[1, 2, 3, 4, 5, 6]], now=before
        )
        await store.complete_drawing(7, draw_date)

        next_date, _ = await store.add_tickets(
            7, draw_time, 42, [[6, 5, 4, 3, 2, 1]], now=before
        )

        assert next_date == "2026-10-05"

    asyncio.run(run_test())


def test_deleting_a_user_removes_only_their_lotto_tickets(tmp_path):
    async def run_test():
        store = LottoStore(tmp_path / "lotto.json")
        now = datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc)
        draw_date, _ = await store.add_tickets(
            7,
            time(20, 0),
            42,
            [[1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1]],
            now=now,
        )

        removed = await store.delete_user(42)

        assert len(removed["7"]) == 2
        assert await store.tickets(7, draw_date) == []

    asyncio.run(run_test())


def test_clear_removes_all_lotto_ticket_data(tmp_path):
    async def run_test():
        store = LottoStore(tmp_path / "lotto.json")
        await store.add_tickets(
            7,
            time(20, 0),
            42,
            [[1, 2, 3, 4, 5, 6]],
            now=datetime(2026, 10, 4, 19, 0, tzinfo=timezone.utc),
        )

        await store.clear()

        assert await store.tickets(7) == []
        assert await store.last_drawn(7) is None

    asyncio.run(run_test())
