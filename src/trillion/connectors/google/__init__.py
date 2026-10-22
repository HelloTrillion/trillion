"""Google: Gmail, Google Calendar and Google Drive, built in (CON-4).

Signed in with OAuth and PKCE through the user's browser, back to Trillion's own
loopback address. Each service is connected on its own, with only the access it
needs, and several Google accounts can be connected at once. Every tool is a spine
tool: reading asks first until the user says it may run on its own, and sending an
email or changing a calendar always asks (`irreversible`). What Google returns is
someone else's words, wrapped as untrusted like a web page.
"""
