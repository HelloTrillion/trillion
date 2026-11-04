"""The words spend is counted in, shared by storage, prices and the ledger. A leaf:
it imports nothing else from trillion.spend."""

from __future__ import annotations

from typing import Literal

# Who made a paid call: someone talking, or work nobody is waiting on.
Caller = Literal["conversation", "background"]
# What a usage row's quantity counts, and what a price is per.
Unit = Literal["tokens", "chars", "seconds"]
