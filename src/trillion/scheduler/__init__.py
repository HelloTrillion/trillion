"""Reminders and timed asks (AGT-4, AGT-8, AGT-9).

The scheduler only writes: runs to the queue and notices for the door. It has no
route to a page, a speaker or a notification (SUR-9); `delivery/door.py` is the one
component that does, and a structural test keeps it that way.
"""
