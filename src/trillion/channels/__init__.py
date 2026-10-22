"""Talking to her from Slack and Telegram (SUR-5).

Answers only. A message is answered only if its sender is on the user's allow-list
for that platform, which starts empty (it fails closed), and in a group only when
it @mentions her. A channel turn has no tools at all, not even the spine's own
(SPN-16): nothing said in a channel can act, read anything private, or approve
anything waiting for the user. What people say there is kept as that conversation's
memory: what was said, not what is true.

Both connections are outbound (Slack Socket Mode, Telegram long polling), so
nothing on this computer is reachable from the internet.
"""
