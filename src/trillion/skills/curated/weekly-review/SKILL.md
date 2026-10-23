---
name: weekly-review
description: Run a weekly review with the user, looking back at the past week and ahead to the next. Use when the user asks for a weekly review, a week ahead, or to plan their week.
allowed-tools: calendar_events memory_recall memory_write
license: Apache-2.0
---
# Weekly review

1. Look back. Read the past seven days with calendar_events, and use memory_recall for
   what the user said they wanted to get done this week.
2. Look ahead. Read the next seven days with calendar_events.
3. Talk it through in this order, briefly:
   - what got done, and what slipped;
   - the busiest days ahead and anything that clashes;
   - two or three suggestions: something to drop, move or prepare.
4. Ask what they want to focus on next week. When they tell you, save it with
   memory_write so next week's review can check on it.

Suggest; don't change their calendar. If they want a change, they will ask.
