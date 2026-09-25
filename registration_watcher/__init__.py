"""Read-only Subtensor watcher that records Subnet 66 registrations for the competitions.

A competition submission is paid for with a subnet registration: one registration buys one
accepted submission. The API checks that against the competition database's `registrations`
table, and this is the process that fills it. Without it that table stays empty and every
signed competition submit is refused `NOT_REGISTERED`.

It holds no wallet and signs nothing. It reads three storage maps at the finalized head --
which hotkey holds each uid, when each uid registered, and which coldkey owns each hotkey --
and appends a row only when a uid's (hotkey, coldkey) pair has changed.

Ported from the gate repository's `chain/watcher.py`, where it lived beside a service that no
longer exists there. Two things changed on the way. It follows the *finalized* head rather
than the tip: a registration seen on a block that is later reorganised away would otherwise
grant a submission slot that does not exist. And it reads through the platform's own
`conjectures_subnet.transfers` connection handling -- one held connection, bounded reads,
reconnect on failure -- rather than bringing a second copy of that along.
"""
