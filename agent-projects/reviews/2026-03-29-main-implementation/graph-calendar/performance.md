# Performance Review: graph-calendar
**Date**: 2026-03-29 | **Files reviewed**: 2 | **Reviewer**: performance-reviewer

## Summary

Reviewed `graph/calendar.py` (396 lines, 11 functions) and `tests/test_calendar.py` (348 lines). The calendar module is well-structured with proper async usage throughout -- all Graph API calls use `await`, no blocking I/O or synchronous libraries are present. Pagination is implemented for `list_events` via `@odata.nextLink`. However, there are two notable gaps: missing `$select` projections on queries that fetch full event objects, and missing pagination on `list_calendars` and `search_events`.

---

## Findings

### [IMPORTANT] Missing $select on list_events calendarView query
**File**: `graph/calendar.py` (lines 123-131)

**Issue**: The `list_events` function queries `/me/calendarView` without a `$select` parameter. Graph API Event objects contain 50+ fields (recurrence patterns, extended properties, multi-value extended properties, change keys, etc.). The function only uses ~13 fields in `_event_to_dict`, but the API returns everything.

**Impact**: Each event response is significantly larger than necessary. For a typical "list my events this week" call returning 20-30 events, this adds unnecessary payload size and deserialization time. Estimated 2-5x more data transferred per event than needed.

**Recommendation**: Add a `$select` parameter to the query configuration:
```python
query_params = CalendarViewRequestBuilder.CalendarViewRequestBuilderGetQueryParameters(
    start_date_time=start_datetime,
    end_date_time=end_datetime,
    top=limit,
    select=[
        "id", "subject", "start", "end", "location",
        "isOnlineMeeting", "onlineMeetingUrl", "body",
        "organizer", "attendees", "isCancelled", "isAllDay",
    ],
)
```

**Rationale**: Reducing payload size directly reduces latency for the most common calendar operation. This is especially impactful because calendarView expands recurring events, which can multiply the number of results.

---

### [IMPORTANT] Missing $select on get_event and search_events
**File**: `graph/calendar.py` (lines 157-194)

**Issue**: Both `get_event` (line 160) and `search_events` (line 187) fetch full Event objects without `$select`. Same field set as `list_events` is used in `_event_to_dict`.

**Impact**: Every single-event fetch and every search result carries unnecessary payload. For `search_events` with up to 20 results, this compounds.

**Recommendation**: Add `$select` with the same 12 fields used by `_event_to_dict` to both query configurations.

**Rationale**: Consistent `$select` usage across all read operations reduces latency and bandwidth.

---

### [IMPORTANT] list_calendars does not handle pagination
**File**: `graph/calendar.py` (lines 83-102)

**Issue**: `list_calendars` calls `gc.client.me.calendars.get()` and returns only `result.value` without checking for `@odata.nextLink`. While most users have fewer than 10 calendars, users with shared/delegated calendars or many category-based calendars could exceed the default page size.

**Impact**: Low probability but silent data truncation -- a user with many calendars would see only the first page of results with no indication that more exist.

**Recommendation**: Add pagination loop similar to `list_events`, or at minimum document the limitation. Since calendar counts are typically small, this is lower urgency than the `$select` findings.

**Rationale**: Silent truncation is a correctness issue that manifests as a confusing user experience (missing calendars).

---

### [SUGGESTION] search_events does not handle pagination
**File**: `graph/calendar.py` (lines 174-194)

**Issue**: `search_events` fetches a single page and slices to `limit`. If a user searches for a common term and the API returns fewer results than exist due to the default page size being smaller than `limit`, results will be silently truncated.

**Impact**: With the default `limit=20` and Graph's default page size of 10, a search matching 15 events would only return 10. However, event search is typically used for targeted lookups so this is lower impact than the equivalent issue in mail.

**Recommendation**: Add pagination loop following `@odata.nextLink` until `limit` is reached, consistent with the pattern already used in `list_events`.

**Rationale**: Consistency with the existing pagination pattern in `list_events` and correctness for searches with many matches.

---

## Verdict

**PASS WITH CHANGES**: The async implementation is correct -- no event loop blocking, no synchronous HTTP calls, proper use of the msgraph SDK's async client. The two important findings are the missing `$select` projections (which add unnecessary latency to every calendar read operation) and the missing pagination on `list_calendars`. These should be addressed before merge to avoid unnecessary bandwidth overhead and silent data truncation.

---

**Review performed by**: Claude Code (performance-reviewer)
**Review focus**: Async correctness, Graph API efficiency, Pagination completeness, Token refresh overhead, HTTPX client lifecycle
