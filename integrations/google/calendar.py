"""
Google Calendar over REST (v3). Read events; add/update events on the primary
calendar. Times are handled in the calendar's own timezone so events land where
the user expects.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from integrations.google._http import GoogleAPIError, api_get, api_patch, api_post

BASE = "https://www.googleapis.com/calendar/v3"
_CAL = f"{BASE}/calendars/primary"


def list_events(access_token: str, date: str = "", max_results: int = 20) -> list[dict]:
    """Upcoming events, or all events on a single YYYY-MM-DD date."""
    params = {
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    if date:
        params["timeMin"] = f"{date}T00:00:00Z"
        params["timeMax"] = f"{date}T23:59:59Z"
    else:
        params["timeMin"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    data = api_get(f"{_CAL}/events", access_token, params=params)
    return [_event_summary(e) for e in data.get("items", [])]


def add_event(access_token: str, title: str, date: str, time: str = "",
              duration_min: int = 60, location: str = "",
              description: str = "") -> dict:
    """Create an event. `date` is YYYY-MM-DD; `time` is HH:MM (24h), optional
    for all-day events."""
    body: dict = {"summary": title, "location": location, "description": description}
    if time:
        tz = _primary_timezone(access_token)
        start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration_min or 60)
        body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz}
        body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz}
    else:
        end_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).date()
        body["start"] = {"date": date}
        body["end"] = {"date": end_date.isoformat()}
    created = api_post(f"{_CAL}/events", access_token, json=body)
    return {"status": "created", "event": _event_summary(created)}


def update_event(access_token: str, event_id: str, title: str = "", date: str = "",
                 time: str = "", location: str = "", description: str = "") -> dict:
    """Patch fields on an existing event (only non-empty fields are changed)."""
    patch: dict = {}
    if title:
        patch["summary"] = title
    if location:
        patch["location"] = location
    if description:
        patch["description"] = description
    if date:
        if time:
            tz = _primary_timezone(access_token)
            start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            patch["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz}
            patch["end"] = {
                "dateTime": (start_dt + timedelta(hours=1)).isoformat(),
                "timeZone": tz,
            }
        else:
            patch["start"] = {"date": date}
    if not patch:
        return {"error": "Nothing to update."}
    updated = api_patch(f"{_CAL}/events/{event_id}", access_token, json=patch)
    return {"status": "updated", "event": _event_summary(updated)}


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def _primary_timezone(access_token: str) -> str:
    try:
        return api_get(_CAL, access_token).get("timeZone", "UTC")
    except GoogleAPIError:
        return "UTC"


def _event_summary(e: dict) -> dict:
    start = e.get("start", {})
    end = e.get("end", {})
    return {
        "id": e.get("id"),
        "title": e.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date", ""),
        "end": end.get("dateTime") or end.get("date", ""),
        "location": e.get("location", ""),
        "description": e.get("description", ""),
        "htmlLink": e.get("htmlLink", ""),
    }
