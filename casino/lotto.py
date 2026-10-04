import asyncio
import json
import os
import random
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_draw_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValueError("Draw time must use 24-hour HH:MM format.")
    return datetime.strptime(value, "%H:%M").time()


def next_draw_at(draw_time, now=None, last_drawn=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scheduled = datetime.combine(now.date(), draw_time, tzinfo=timezone.utc)
    if scheduled <= now:
        scheduled += timedelta(days=1)
    if last_drawn is not None and last_drawn >= scheduled.date().isoformat():
        scheduled += timedelta(days=1)
    return scheduled


def draw_at_date(draw_date, draw_time):
    return datetime.combine(draw_date, draw_time, tzinfo=timezone.utc)


def quick_pick():
    return sorted(draw_numbers())


def draw_numbers():
    return random.SystemRandom().sample(range(100), 6)


def classify_tickets(tickets, drawn_numbers):
    drawn = tuple(drawn_numbers)
    drawn_set = set(drawn)
    exact = []
    unordered = []

    for ticket in tickets:
        numbers = tuple(ticket["numbers"])
        if numbers == drawn:
            exact.append(ticket)
        elif set(numbers) == drawn_set:
            unordered.append(ticket)

    return exact, unordered


def split_jackpot(amount, tickets):
    if not tickets:
        return []
    share, remainder = divmod(amount, len(tickets))
    return [
        (ticket, share + (index < remainder))
        for index, ticket in enumerate(tickets)
    ]


class LottoStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    @staticmethod
    async def _run_io(func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def _read(self):
        if not self.path.exists():
            return {"guilds": {}}
        with self.path.open(encoding="utf-8") as data_file:
            data = json.load(data_file)
        if not isinstance(data, dict) or not isinstance(data.get("guilds"), dict):
            raise ValueError("Lotto ticket database has an invalid format.")
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                delete=False,
            ) as data_file:
                temp_path = data_file.name
                json.dump(data, data_file, separators=(",", ":"))
                data_file.flush()
                os.fsync(data_file.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _guild_data(data, guild_id):
        return data.setdefault("guilds", {}).setdefault(
            str(guild_id),
            {"tickets": [], "last_drawn": None, "drawings": {}},
        )

    async def add_tickets(self, guild_id, draw_time, user_id, numbers_list, now=None):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            draw_date = next_draw_at(
                draw_time, now=now, last_drawn=guild_data.get("last_drawn")
            ).date().isoformat()
            entries = [
                {
                    "ticket_id": uuid.uuid4().hex,
                    "draw_date": draw_date,
                    "user_id": int(user_id),
                    "numbers": list(numbers),
                }
                for numbers in numbers_list
            ]
            guild_data["tickets"].extend(entries)
            await self._run_io(self._write, data)
            return draw_date, entries

    async def begin_drawing(self, guild_id, draw_date, drawn_numbers):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            drawings = guild_data.setdefault("drawings", {})
            drawing = drawings.get(draw_date)
            changed = False
            if drawing is None:
                drawing = {
                    "numbers": list(drawn_numbers),
                    "settlements": {},
                }
                drawings[draw_date] = drawing
                changed = True

            for ticket in guild_data.get("tickets", []):
                if ticket["draw_date"] == draw_date and "ticket_id" not in ticket:
                    ticket["ticket_id"] = uuid.uuid4().hex
                    changed = True
            if changed:
                await self._run_io(self._write, data)
            return {
                "numbers": list(drawing["numbers"]),
                "settlements": {
                    ticket_id: dict(settlement)
                    for ticket_id, settlement in drawing["settlements"].items()
                },
            }

    async def settle_ticket(self, guild_id, draw_date, ticket_id, paid, pending):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            drawing = guild_data.setdefault("drawings", {}).get(draw_date)
            if drawing is None:
                raise ValueError("No active lotto drawing exists for that date.")
            settlements = drawing.setdefault("settlements", {})
            settlement = settlements.get(ticket_id)
            if settlement is None:
                settlement = {"paid": int(paid), "pending": int(pending)}
                settlements[ticket_id] = settlement
                await self._run_io(self._write, data)
            return dict(settlement)

    async def drawing_dates(self, guild_id):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            return sorted(guild_data.get("drawings", {}))

    async def tickets(self, guild_id, draw_date=None):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            entries = guild_data.get("tickets", [])
            if draw_date is not None:
                entries = [
                    ticket for ticket in entries if ticket["draw_date"] == draw_date
                ]
            return [dict(ticket) for ticket in entries]

    async def tickets_for_user(self, guild_id, user_id, from_date):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            return [
                dict(ticket)
                for ticket in guild_data.get("tickets", [])
                if ticket["user_id"] == int(user_id)
                and ticket["draw_date"] >= from_date
            ]

    async def last_drawn(self, guild_id):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            return guild_data.get("last_drawn")

    async def expire_before(self, guild_id, draw_date):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            in_progress = set(guild_data.setdefault("drawings", {}))
            expired = [
                ticket
                for ticket in guild_data.get("tickets", [])
                if ticket["draw_date"] < draw_date
                and ticket["draw_date"] not in in_progress
            ]
            if expired:
                guild_data["tickets"] = [
                    ticket
                    for ticket in guild_data["tickets"]
                    if ticket["draw_date"] >= draw_date
                    or ticket["draw_date"] in in_progress
                ]
                await self._run_io(self._write, data)
            return [dict(ticket) for ticket in expired]

    async def complete_drawing(self, guild_id, draw_date):
        async with self._lock:
            data = await self._run_io(self._read)
            guild_data = self._guild_data(data, guild_id)
            expired = [
                ticket
                for ticket in guild_data.get("tickets", [])
                if ticket["draw_date"] == draw_date
            ]
            guild_data["tickets"] = [
                ticket
                for ticket in guild_data.get("tickets", [])
                if ticket["draw_date"] != draw_date
            ]
            guild_data["last_drawn"] = draw_date
            guild_data.setdefault("drawings", {}).pop(draw_date, None)
            await self._run_io(self._write, data)
            return [dict(ticket) for ticket in expired]

    async def delete_user(self, user_id):
        async with self._lock:
            data = await self._run_io(self._read)
            removed = {}
            removed_ticket_ids = set()
            for guild_id, guild_data in data["guilds"].items():
                tickets = guild_data.get("tickets", [])
                user_tickets = [
                    ticket for ticket in tickets if ticket["user_id"] == int(user_id)
                ]
                if user_tickets:
                    removed[guild_id] = [dict(ticket) for ticket in user_tickets]
                    removed_ticket_ids.update(
                        ticket["ticket_id"]
                        for ticket in user_tickets
                        if "ticket_id" in ticket
                    )
                    guild_data["tickets"] = [
                        ticket
                        for ticket in tickets
                        if ticket["user_id"] != int(user_id)
                    ]
            if removed:
                for guild_data in data["guilds"].values():
                    for drawing in guild_data.get("drawings", {}).values():
                        settlements = drawing.get("settlements", {})
                        for ticket_id in removed_ticket_ids:
                            settlements.pop(ticket_id, None)
                await self._run_io(self._write, data)
            return removed

    async def clear(self):
        async with self._lock:
            await self._run_io(self._write, {"guilds": {}})
