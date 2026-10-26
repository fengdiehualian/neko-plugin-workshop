# Travel arrival and command dispatch

Development 0.6.90 consumes autonomy 0.2.85 at d2bceaf7 and core
0.5.7-rc.66 at f9554418. Public release remains 0.6.70.

A travel step already within its destination radius deliberately sends no command.
The default native schema, help, task label and receipt now distinguish that
position check from command execution. Queries and activity commands use chat;
a successful send still requires the original server reply to establish acceptance.
The adapter adds no command rewriting, retries, permissions or second queue.

The installed previous core reproduced the unsent-command case without a game,
model or network connection. Three new regressions failed before the repair and
passed after it; the full core suite passed 1864 tests with 3 optional skips.
The framework suite passed 1445 tests. These source checks do not establish
successful group entry, completed projects or sustained survival.
