# Contract requests from the web app

Five additions the web app's units wait on (`pc-app-gaps.md` section 5: N1, N2, N5, N6, N7). N3 and N4 wait for a founder decision and are not here. Each is written to be taken as it stands: the rows and sentence as `protocol.md` prints them, the edits to `contract/protocol.json` (then `python3 contract/generate.py`), the rules the fixtures pin, the topic events, and the fixtures as `contract/fixtures/` holds them. All five only add (a method, a field, an enum word, one more shape of a param), so protocol stays 1.

Under each is the bridge's own code to reuse, so what the daemon does is what the founder gets today. The fixtures use three new `--var`s, `peer_agent`, `session_agent` and `multi_agent`, besides `walled_agent`, `running_agent` and `dialog_agent`, which exist; a fixture whose var is not given is NEEDVAR, never a pass.

**Checked.** The edits applied to `protocol.json` on main regenerate `protocol.md` and `protocol.ts`, and `tests/infra/test_contract.py` passes on the result. Every fixture loads through `conformance.load` and names a method of the edited spec, and the shape fixtures' matchers pass a hand-made reply and refuse a wrong one. No daemon serves these methods yet, so no fixture has run against one.

## N1 · `chat.send_now`

**Why.** The ⚡ button on a queued message (`chat_page.py:826`) tells the agent now instead of when its queue reaches it. **Reuse.** `actions.send_now` (`actions/requests.py:163`): `db.mark_send_now`, then `ask.push` for the agent.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `chat.send_now` | member | yes | `request`, `request_id` | `Accepted & {request, landing}` | `refused`, `not_found` | role, state |

> Type a queued message of the person's now: as soon as the agent can be typed at, without waiting for the request ahead of it in the agent's queue.

- `request` is a ChatMessage id. The call acts on a message a person sent with `chat.send` that is still waiting (`landing` is `queued`): it stamps the message (`ChatMessage.send_now`, the time of the first call) and asks the delivery to try at once.
- Whether the agent can be typed at is the delivery's question, not the call's. An agent at its usage wall, behind a terminal lock, or with a dialog open leaves the message queued and stamped, and it is typed at the first moment it can be, ahead of any request already open in the agent's queue. So `terminal_lock`, `input_holder`, `wall` and `screen_blocker` never refuse this call: like `chat.send` it needs no input hold.
- `landing` in the result is the message's state when the call returns: `typed` if the push typed it, else `queued`.
- A second call on a stamped message is accepted again and keeps the first stamp. A retry with the same `request_id` replays.
- `refused`, gate `state`, reason naming the landing: the message is not waiting any more (typed, delivered, answered, dismissed, withdrawn), or it is not a person's message.
- `not_found`: no such request. An agent session is refused at the role gate, so add `chat.send_now` to `BARRED_FROM_AGENTS` in `tests/infra/test_contract.py`.
- **Events.** Topic `chat`, `chat.message` (ChatMessage): once when the stamp is set, and at each later change of `landing`, as for any message.

```jsonc
// methods, after chat.send
{
 "name": "chat.send_now",
 "summary": "Type a queued message of the person's now: as soon as the agent can be typed at, without waiting for the request ahead of it in the agent's queue.",
 "paramStructure": "by-name",
 "params": [
            {"name": "request", "required": true, "schema": {"type": "integer"}},
            {"name": "request_id", "required": true, "schema": {"$ref": "#/components/schemas/RequestId"}}
           ],
 "result": {
            "name": "result",
            "schema": {"allOf": [{"$ref": "#/components/schemas/Accepted"}, {"type": "object", "properties": {"request": {"type": "integer"}, "landing": {"$ref": "#/components/schemas/Landing"}}, "required": ["request", "landing"]}]}
           },
 "x-role": "member",
 "x-write": true,
 "x-since": 1,
 "errors": [{"$ref": "#/components/errors/refused"}, {"$ref": "#/components/errors/not_found"}],
 "x-gates": ["role", "state"]
}

// components.schemas.ChatMessage.properties.send_now, and add it to required
{
 "oneOf": [
           {"$ref": "#/components/schemas/Timestamp", "description": "When send now was asked for this message; null if it never was."},
           {"type": "null"}
          ]
}
```

`contract/fixtures/chat.json` (new):

```json
{"fixtures": [
 {"name": "chat.send_now.no_such_request", "spec": "chat.send_now", "about": "A message that does not exist is not_found.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "chat.send_now", "params": {"request": 999999999, "request_id": "conf-sn-1"}}, "error": {"code": -32001, "data": {"code": "not_found"}}}
 ]},
 {"name": "chat.send_now.needs_a_request_id", "spec": "chat.send_now", "about": "A write without a request_id is bad_request naming it.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "chat.send_now", "params": {"request": 1}}, "error": {"code": -32600, "data": {"code": "bad_request", "field": "request_id"}}}
 ]},
 {"name": "chat.send_now.an_agent_session_is_refused_by_role", "spec": "chat.send_now", "about": "It is a person's: a call from an agent session is refused, gate role.", "as": "agent", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "chat.send_now", "params": {"request": 1, "request_id": "conf-sn-3"}}, "error": {"code": -32002, "data": {"code": "refused", "gate": "role"}}}
 ]},
 {"name": "chat.send_now.marks_a_waiting_message", "spec": "chat.send_now", "about": "A message waiting for an agent at its usage limit stays queued, but is marked: the reply says queued, the message carries send_now, a second call is accepted again and the chat topic says so. Needs --var walled_agent.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "subscribe", "params": {"topics": ["chat"]}}, "result": {"cursor": {"$type": "integer"}}},
  {"send": {"jsonrpc": "2.0", "id": 3, "method": "chat.send", "params": {"agent": "{{walled_agent}}", "body": "conformance: send now", "request_id": "conf-sn-4"}}, "result": {"accepted": true, "request": {"$type": "integer", "$save": "req"}}},
  {"send": {"jsonrpc": "2.0", "id": 4, "method": "chat.send_now", "params": {"request": "{{req}}", "request_id": "conf-sn-5"}}, "result": {"accepted": true, "landing": "queued"}, "notices": [{"method": "event", "params": {"topic": "chat", "type": "chat.message", "data": {"landing": "queued", "send_now": {"$type": "timestamp"}}}}]},
  {"send": {"jsonrpc": "2.0", "id": 5, "method": "chat.send_now", "params": {"request": "{{req}}", "request_id": "conf-sn-6"}}, "result": {"accepted": true, "landing": "queued"}},
  {"send": {"jsonrpc": "2.0", "id": 6, "method": "chat.messages", "params": {"agent": "{{walled_agent}}", "scope": "mine", "limit": 20}}, "result": {"messages": {"$contains": {"landing": "queued", "send_now": {"$type": "timestamp"}}}}},
  {"send": {"jsonrpc": "2.0", "id": 7, "method": "chat.withdraw", "params": {"request": "{{req}}", "reason": "conformance", "request_id": "conf-sn-7"}}, "result": {"accepted": true}}
 ]},
 {"name": "chat.send_now.a_withdrawn_message_is_refused", "spec": "chat.send_now", "about": "A message that is no longer waiting is refused, gate state. Needs --var walled_agent.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "chat.send", "params": {"agent": "{{walled_agent}}", "body": "conformance: withdrawn", "request_id": "conf-sn-8"}}, "result": {"request": {"$type": "integer", "$save": "gone"}}},
  {"send": {"jsonrpc": "2.0", "id": 3, "method": "chat.withdraw", "params": {"request": "{{gone}}", "reason": "conformance", "request_id": "conf-sn-9"}}, "result": {"accepted": true}},
  {"send": {"jsonrpc": "2.0", "id": 4, "method": "chat.send_now", "params": {"request": "{{gone}}", "request_id": "conf-sn-10"}}, "error": {"code": -32002, "data": {"code": "refused", "gate": "state"}}}
 ]}
]}
```

## N2 · `Task.priority` and `task.edit` priority

**Why.** The `p` key and the priority column of the task table (`tasks_view.py:158`, `:191`). **Reuse.** `actions.edit_task(priority=)` (`actions/tasks.py:1489`) changes it, the CLI's `--priority` calls it, and `tasks.priority` holds it (default 2, `db/tasks.py:25`). The daemon's `task.edit` handler (`daemon/rpc_tasks.py`) does not pass it on yet. `task.new` and `task.begin` already take it.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `task.edit` | agent | yes | `task`, `owner?`, `title?`, `body?`, `time_box?`, `priority?`, `waits_on?`, `request_id` | `Accepted` | `refused`, `not_found` | state |

> Change a task: owner, title and priority by its opener or manager (its owner may record what it waits on), body and time box until it is delivered. The holder is told of every change but a priority.

- `Task.priority` is 1, 2 or 3 (1 high, written P1 to P3), always present, 2 for a task that never had one. It is on `tasks.list`, `task.get` and in `task.changed`. List order and paging do not change: a client sorts by priority, then by number.
- `task.edit` `priority` is changed by whoever may change the title: the opener or the manager, and the founder. Any other value (4, 0, "P1", a boolean) is `bad_request`, field `priority`, before the task is looked up.
- A priority that is already the task's, and the only thing asked, is `refused`, gate `state` ("already reads that way"). With other changes in the same call, they apply.
- The call needs one of `owner`, `title`, `body`, `time_box`, `priority`; the `bad_request` for none now names priority too.
- A priority change tells nobody (it moves the task in its owner's order, not its work). It is recorded in the task's events as other edits are.
- **Events.** Topic `tasks`, `task.changed` (Task), with the new `priority`.

```jsonc
// components.schemas.Task.properties.priority, and add it to required
{"type": "integer", "enum": [1, 2, 3], "description": "1 high, 2 default, 3 low (written P1-P3)."}

// methods.task.edit.params, after time_box
{
 "name": "priority",
 "required": false,
 "schema": {"type": "integer", "enum": [1, 2, 3], "description": "1 high, 2 default, 3 low (written P1-P3)."}
}

// methods.task.edit.summary
"Change a task: owner, title and priority by its opener or manager (its owner may record what it waits on), body and time box until it is delivered. The holder is told of every change but a priority."

// methods.task.edit.x-gates
["state"]
```

Append to `contract/fixtures/tasks.json`:

```json
{"fixtures": [
 {"name": "tasks.list.rows_carry_priority", "spec": "tasks.list", "about": "Every row has a priority, 1 high to 3 low.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "tasks.list", "params": {"all": true, "limit": 20}}, "result": {"items": {"$each": {"priority": {"$oneOf": [1, 2, 3]}}}, "more": {"$type": "boolean"}}}
 ]},
 {"name": "task.edit.priority_is_one_to_three", "spec": "task.edit", "about": "A priority that is not 1, 2 or 3 is bad_request naming the field.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "task.edit", "params": {"task": 1, "priority": 4, "request_id": "conf-pr-1"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "priority"}}},
  {"send": {"jsonrpc": "2.0", "id": 3, "method": "task.edit", "params": {"task": 1, "priority": "P1", "request_id": "conf-pr-2"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "priority"}}}
 ]},
 {"name": "task.edit.priority_changes_the_row", "spec": "task.edit", "about": "The opener sets a priority: the row and its detail carry it, the tasks topic says so, and the same priority again is refused, gate state. Needs --var peer_agent: an agent the caller may open a task for.", "as": "agent", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "subscribe", "params": {"topics": ["tasks"]}}, "result": {"cursor": {"$type": "integer"}}},
  {"send": {"jsonrpc": "2.0", "id": 3, "method": "task.new", "params": {"owner": "{{peer_agent}}", "title": "conformance: priority", "body": "Nothing to do; withdrawn by the fixture.", "time_box": "1h", "priority": 3, "request_id": "conf-pr-3"}}, "result": {"accepted": true, "task": {"$type": "integer", "$save": "t"}}},
  {"send": {"jsonrpc": "2.0", "id": 4, "method": "task.edit", "params": {"task": "{{t}}", "priority": 1, "request_id": "conf-pr-4"}}, "result": {"accepted": true}, "notices": [{"method": "event", "params": {"topic": "tasks", "type": "task.changed", "data": {"priority": 1}}}]},
  {"send": {"jsonrpc": "2.0", "id": 5, "method": "task.get", "params": {"task": "{{t}}"}}, "result": {"priority": 1}},
  {"send": {"jsonrpc": "2.0", "id": 6, "method": "task.edit", "params": {"task": "{{t}}", "priority": 1, "request_id": "conf-pr-5"}}, "error": {"code": -32002, "data": {"code": "refused", "gate": "state"}}},
  {"send": {"jsonrpc": "2.0", "id": 7, "method": "task.withdraw", "params": {"task": "{{t}}", "request_id": "conf-pr-6"}}, "result": {"accepted": true}}
 ]}
]}
```

## N5 · `stats.outlook`

**Why.** The outlook graph and the cost-per-day view (`outlook_view.py`); `stats.get` has 24 h and 7 d counts and two percentages, no curve. **Reuse.** `actions.weekly_outlook`, `agent_use`, `agent_overlay` and `daily_agent_cost` (`actions/details.py:451`), `usage.status.runway` and `describe_runway` (`usage/status.py:796`).

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `stats.outlook` | member | | `range`, `pick?`, `cost_days?` | `Outlook` | `not_found` | |

> The weekly headroom of all accounts together, recorded and guessed, each agent's use and cost, each account's runway, and optionally the cost per day.

- `range` is 12h, 24h, 3d, 7d or 14d, and the window is that far before `at` and the same after it. `past` is up to `at`, as the usage samples recorded it; `future` is the guess. Both are on one grid, `step_s` apart, about 96 points over the two. The bridge's own functions cut an account at the end of its month and when it is logged out; the daemon reuses them.
- `left_pct` is the percent of the week left, averaged over the accounts with a reading, null where none had one.
- `agents` are the agents' use over `[at - range, at]`, largest cost first, then name; `share` is 0 to 1, `week_pct` the estimate the bridge shows as `~ week %`. Token calls no worktree claims are `(unattributed)`. The client groups dotted names itself.
- `pick` is an agent, or `grp:<first part of a dotted name>` for a group, and the reply's `overlay` is what it had eaten of the combined week at each point of `past`, the same count and order. An agent that used nothing gives zeros. A name that is no team agent and no group is `not_found`. Without `pick`, `overlay` is null.
- `runway` is the bridge's runway for each account with a trusted reading, `text` being its one line; an account with a stale or unknown reading is left out.
- `cost_days` (7, 14 or 30) adds `cost`: one entry for each of the last that many local days, oldest first, today last, `by_agent` empty for a day with no call. It is the list price of the token log, not what the plan charged. Without it `cost` is null.
- The read parses the usage files (hundreds of ms): a reply up to 30 s old is allowed, and `at` says when it was made. **Events.** None; the bridge asks every 30 s and so can a client.

```jsonc
// methods, after stats.get
{
 "name": "stats.outlook",
 "summary": "The weekly headroom of all accounts together, recorded and guessed, each agent's use and cost, each account's runway, and optionally the cost per day.",
 "paramStructure": "by-name",
 "params": [
            {"name": "range", "required": true, "schema": {"type": "string", "enum": ["12h", "24h", "3d", "7d", "14d"], "description": "The window is this far before now and the same after it."}},
            {"name": "pick", "required": false, "schema": {"type": "string", "description": "An agent, or `grp:<first part of a dotted name>` for a group: its use is returned in `overlay`."}},
            {"name": "cost_days", "required": false, "schema": {"type": "integer", "enum": [7, 14, 30], "description": "Also return the cost of each of the last this many local days; without it `cost` is null."}}
           ],
 "result": {"name": "result", "schema": {"$ref": "#/components/schemas/Outlook"}},
 "x-role": "member",
 "x-write": false,
 "x-since": 1,
 "errors": [{"$ref": "#/components/errors/not_found"}],
 "description": "The headroom is the week left of each account with a reading, averaged: the past as the usage samples recorded it, the future as a guess (each account keeps the pace of the last day; a week that resets comes back used by that pace). Cost is the list price of the token log, not what the plan charged. The read parses the usage files: a reply up to 30 s old is allowed, and `at` says when it was made."
}

// components.schemas, new
{
 "OutlookPoint": {
                  "type": "object",
                  "properties": {"at": {"$ref": "#/components/schemas/Timestamp"}, "left_pct": {"oneOf": [{"type": "number", "description": "0-100; null when no account had a reading at that time."}, {"type": "null"}]}},
                  "required": ["at", "left_pct"]
                 },
 "OutlookAgent": {
                  "type": "object",
                  "properties": {"agent": {"type": "string", "description": "An agent's name; `(unattributed)` for token calls no worktree claims."}, "tokens": {"type": "integer"}, "cost_usd": {"type": "number", "description": "List price."}, "share": {"type": "number", "description": "0-1: its part of all agents' cost in the window."}, "week_pct": {"oneOf": [{"type": "number", "description": "Estimated points of the week it used in the window; null when unknown."}, {"type": "null"}]}},
                  "required": ["agent", "tokens", "cost_usd", "share", "week_pct"]
                 },
 "OutlookRunway": {
                   "type": "object",
                   "properties": {"account": {"type": "string"}, "short_left_pct": {"type": "number", "description": "Points of the shortest window left before the wall."}, "long_left_pct": {"oneOf": [{"type": "number", "description": "The same for the longest window."}, {"type": "null"}]}, "eta_s": {"oneOf": [{"type": "number", "description": "Seconds to the wall at the current burn; null when it is not burning."}, {"type": "null"}]}, "burning": {"type": "boolean"}, "past_reset": {"type": "boolean", "description": "The shortest window resets before the wall is reached."}, "long_s": {"oneOf": [{"type": "number", "description": "How long the longest window lasts at the current fill, when known and it does not reset first."}, {"type": "null"}]}, "long_label": {"type": "string", "description": "What the longest window is called, e.g. weekly."}, "text": {"type": "string", "description": "The one line to show, e.g. `~3h to wall`, `outlasts reset`, `no recent burn`, `at the wall`."}},
                   "required": ["account", "short_left_pct", "long_left_pct", "eta_s", "burning", "past_reset", "long_s", "long_label", "text"]
                  },
 "OutlookCost": {
                 "type": "object",
                 "properties": {"basis": {"type": "string", "enum": ["list_price"]}, "days": {"type": "array", "items": {"type": "object", "properties": {"day": {"type": "string", "description": "Local calendar day, YYYY-MM-DD."}, "by_agent": {"type": "object", "additionalProperties": {"type": "number"}, "description": "USD per agent; empty for a day with no call."}}, "required": ["day", "by_agent"]}, "description": "Oldest first, today last: one entry for every day asked."}},
                 "required": ["basis", "days"]
                },
 "Outlook": {
             "type": "object",
             "properties": {"range": {"type": "string", "enum": ["12h", "24h", "3d", "7d", "14d"]}, "at": {"$ref": "#/components/schemas/Timestamp", "description": "When this was computed; the boundary between `past` and `future`."}, "step_s": {"type": "integer", "description": "Seconds between points."}, "accounts": {"type": "integer", "description": "Accounts with a reading in the window."}, "past": {"type": "array", "items": {"$ref": "#/components/schemas/OutlookPoint"}, "description": "Recorded, up to `at`."}, "future": {"type": "array", "items": {"$ref": "#/components/schemas/OutlookPoint"}, "description": "Guessed, after `at`."}, "agents": {"type": "array", "items": {"$ref": "#/components/schemas/OutlookAgent"}, "description": "Largest cost first, then name."}, "overlay": {"oneOf": [{"type": "array", "items": {"type": "number"}, "description": "Points of the combined week the picked agent or group had eaten at each point of `past`, same count and order; null without `pick`."}, {"type": "null"}]}, "runway": {"type": "array", "items": {"$ref": "#/components/schemas/OutlookRunway"}, "description": "One per account with a trusted reading; an account whose reading is stale or unknown is left out, because no figure is better than a wrong one."}, "cost": {"oneOf": [{"$ref": "#/components/schemas/OutlookCost"}, {"type": "null"}]}},
             "required": ["range", "at", "step_s", "accounts", "past", "future", "agents", "overlay", "runway", "cost"]
            }
}
```

`contract/fixtures/stats.json` (new):

```json
{"fixtures": [
 {"name": "stats.outlook.shape", "spec": "stats.outlook", "about": "The window's two halves, each agent's use, each account's runway; cost and overlay are null unless asked for.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "stats.outlook", "params": {"range": "24h"}}, "result": {"$exact": true, "range": "24h", "at": {"$type": "timestamp"}, "step_s": {"$type": "integer"}, "accounts": {"$type": "integer"}, "past": {"$type": "array", "$each": {"at": {"$type": "timestamp"}, "left_pct": {"$oneOf": [{"$type": "number"}, {"$type": "null"}]}}}, "future": {"$type": "array", "$each": {"at": {"$type": "timestamp"}, "left_pct": {"$oneOf": [{"$type": "number"}, {"$type": "null"}]}}}, "agents": {"$type": "array", "$each": {"agent": {"$type": "string"}, "tokens": {"$type": "integer"}, "cost_usd": {"$type": "number"}, "share": {"$type": "number"}, "week_pct": {"$oneOf": [{"$type": "number"}, {"$type": "null"}]}}}, "overlay": {"$type": "null"}, "runway": {"$type": "array", "$each": {"account": {"$type": "string"}, "burning": {"$type": "boolean"}, "eta_s": {"$oneOf": [{"$type": "number"}, {"$type": "null"}]}, "text": {"$type": "string"}}}, "cost": {"$type": "null"}}}
 ]},
 {"name": "stats.outlook.range_is_one_of_the_named", "spec": "stats.outlook", "about": "A range that is not 12h, 24h, 3d, 7d or 14d is bad_request naming the field.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "stats.outlook", "params": {"range": "1h"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "range"}}}
 ]},
 {"name": "stats.outlook.cost_is_a_day_for_each_day", "spec": "stats.outlook", "about": "With cost_days the cost is list price, one entry per local day.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "stats.outlook", "params": {"range": "7d", "cost_days": 7}}, "result": {"cost": {"basis": "list_price", "days": {"$type": "array", "$each": {"day": {"$regex": "^\\d{4}-\\d{2}-\\d{2}$"}, "by_agent": {"$type": "object"}}}}}}
 ]},
 {"name": "stats.outlook.pick_must_be_known", "spec": "stats.outlook", "about": "A pick that names no agent or group is not_found.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "stats.outlook", "params": {"range": "24h", "pick": "no.such_agent"}}, "error": {"code": -32001, "data": {"code": "not_found"}}}
 ]},
 {"name": "stats.outlook.pick_gives_an_overlay", "spec": "stats.outlook", "about": "A picked agent's overlay is a list of numbers. Needs --var running_agent.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "stats.outlook", "params": {"range": "24h", "pick": "{{running_agent}}"}}, "result": {"overlay": {"$type": "array", "$each": {"$type": "number"}}}}
 ]},
 {"name": "stats.outlook.an_agent_session_is_refused_by_role", "spec": "stats.outlook", "about": "It is a person's screen: a call from an agent session is refused, gate role.", "as": "agent", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "stats.outlook", "params": {"range": "24h"}}, "error": {"code": -32002, "data": {"code": "refused", "gate": "role"}}}
 ]}
]}
```

## N6 · `Prompt.multi`

**Why.** AskUserQuestion's multi-select (`question_modal.py`): tick several options, optionally fill Other, submit once. A `PromptOption` can say each option but not that several may be picked, and `prompt.answer` takes one key. **Reuse.** `prompt_blocks.answer` already takes `{"choices": [...], "text": ...}` for a question, and `term.answer_question` sends the toggles and the submit, in two stages with a re-read when boxes and Other come together.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `prompt.answer` | member | yes | `agent`, `prompt`, `choice`, `request_id` | `Accepted` | `refused`, `not_found`, `stale_prompt` | role, terminal_lock, input_holder |

Only `choice` changes; it gains a shape:

> A PromptOption.key, or {text} for the free-text answer to a dialog that asks for one, or, on a prompt with `multi`, {keys, text?}; a text answer passes the same gates and the prompt check.

- `Prompt.multi` is true on a `question` whose screen is a multi-select, absent or false on every other prompt. On such a prompt `PromptOption.checked` says whether the box is checked on the screen now. The daemon sets `highlighted` from the checked box today and keeps doing so; `checked` is the field a client reads.
- `{keys, text?}` is the final state: the boxes named are checked, any other box that is checked on the screen is unchecked, and `text` fills Other as well (`choices` and `text` of `prompt_blocks.answer`). `keys` empty with no `text` submits with nothing checked, which is a real answer. A key is an option key of this prompt, named once; `esc` is not one (cancel stays the bare string `"esc"`).
- `bad_request`, field `choice`, and nothing sent: `{keys}` on a prompt whose `multi` is not true; a key the prompt does not offer, one named twice, or `esc`. A bare option key on a multi prompt is what it is today: that box only, then submit.
- Everything else is as for any answer: the prompt id is checked first (`stale_prompt` with the current prompt, nothing sent), then the three gates.
- **Events.** None new: the answered prompt's `chat.transcript` card turns `open: false`, as for any answer.

```jsonc
// components.schemas.Prompt.properties.multi, optional
{
 "type": "boolean",
 "description": "A question that takes several choices: pick any number of options, then submit once (prompt.answer {keys}). Absent or false: one choice."
}

// components.schemas.PromptOption.properties.checked, optional
{"type": "boolean", "description": "Multi question only: the box is checked on the screen now."}

// methods.prompt.answer.params.choice.schema.oneOf, one more member
{
 "type": "object",
 "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "The option keys to leave checked: the boxes checked now and not named are unchecked."},
                "text": {"type": "string", "description": "Also fill the question's Other row with this."}
               },
 "required": ["keys"]
}

// methods.prompt.answer.params.choice.schema.description
"A PromptOption.key, or {text} for the free-text answer to a dialog that asks for one, or, on a prompt with `multi`, {keys, text?}; a text answer passes the same gates and the prompt check."
```

`contract/prompts/question_multi_select.json` (new; the screen is the one `tests/session/test_ask_question.py` holds, and the daemon's screen watch reads it as `expect` says). `tests/daemon/test_prompt_fields.py` compares only kind, tool, command, reason and option keys: it also compares `multi` and `checked` for a capture that states them.

```json
{
 "name": "question_multi_select",
 "source": "tests/session/test_ask_question.py MULTI_SELECT",
 "screen": [
            "────────────────────────────────────────────────────────────",
            "←  ☒ Features  ✔ Submit  →",
            "",
            "Which features should we enable?",
            "",
            "  1. [✔] dark mode",
            "         system-wide dark theme",
            "  2. [ ] notifications",
            "         push alerts",
            "  3. [✔] offline sync",
            "         works without network",
            "❯ 4. [ ] Type something",
            "     Submit",
            "────────────────────────────────────────────────────────────",
            "  5. Chat about this",
            "",
            "Enter to select · ↑/↓ to navigate · ctrl+g to edit in VS Code · Esc to cancel"
           ],
 "expect": {
            "kind": "question",
            "tool": null,
            "command": null,
            "reason": null,
            "options": ["1", "2", "3", "esc"],
            "multi": true,
            "checked": ["1", "3"]
           }
}
```

`contract/fixtures/prompts.json` (new):

```json
{"fixtures": [
 {"name": "prompt.list.a_multi_select_question_says_so", "spec": "prompt.list", "about": "A question that takes several choices has multi true, and each option says whether its box is checked. Needs --var multi_agent: an agent whose screen shows a multi-select question.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "prompt.list", "params": {"agent": "{{multi_agent}}"}}, "result": {"prompts": [{"kind": "question", "multi": true, "options": {"$each": {"key": {"$type": "string"}}, "$contains": {"checked": {"$type": "boolean"}}}}]}}
 ]},
 {"name": "prompt.answer.keys_are_for_a_multi_select_only", "spec": "prompt.answer", "about": "{keys} on a prompt that is not a multi-select question is bad_request naming choice, and nothing is sent. Needs --var dialog_agent.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "prompt.list", "params": {"agent": "{{dialog_agent}}"}}, "result": {"prompts": [{"id": {"$type": "string", "$save": "pid"}}]}},
  {"send": {"jsonrpc": "2.0", "id": 3, "method": "prompt.answer", "params": {"agent": "{{dialog_agent}}", "prompt": "{{pid}}", "choice": {"keys": ["1"]}, "request_id": "conf-mu-1"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "choice"}}}
 ]},
 {"name": "prompt.answer.keys_are_option_keys", "spec": "prompt.answer", "about": "A key the prompt does not offer, one named twice, or esc is bad_request naming choice, and nothing is sent. Needs --var multi_agent.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "prompt.list", "params": {"agent": "{{multi_agent}}"}}, "result": {"prompts": [{"id": {"$type": "string", "$save": "pid"}}]}},
  {"send": {"jsonrpc": "2.0", "id": 3, "method": "prompt.answer", "params": {"agent": "{{multi_agent}}", "prompt": "{{pid}}", "choice": {"keys": ["1", "no.such_key"]}, "request_id": "conf-mu-2"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "choice"}}},
  {"send": {"jsonrpc": "2.0", "id": 4, "method": "prompt.answer", "params": {"agent": "{{multi_agent}}", "prompt": "{{pid}}", "choice": {"keys": ["1", "1"]}, "request_id": "conf-mu-3"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "choice"}}},
  {"send": {"jsonrpc": "2.0", "id": 5, "method": "prompt.answer", "params": {"agent": "{{multi_agent}}", "prompt": "{{pid}}", "choice": {"keys": ["esc"]}, "request_id": "conf-mu-4"}}, "error": {"code": -32602, "data": {"code": "bad_request", "field": "choice"}}}
 ]}
]}
```

## N7 · a `session` transcript item

**Why.** `SessionMark` and `HandoffModal` (`chat_page.py:718`, `:734`): a line where each session began, and the handoff it was given, read whole on a click. **Reuse.** `db.session_starts` (`db/handoff.py:58`; `ts`, `session_id`, `cause`, `resumed`, `text`, `actor`) and `session_marks` (`chat_page.py:277`).

`chat.transcript` already returns `TranscriptItem`s; no new method and no new param.

- `kind` gains `session`, and the item carries `session` (SessionStart): `session_id`, `cause`, `actor`, `handoff`, `handoff_truncated`. `at` is when the session began; `text` is absent.
- Only a start of a new conversation is an item (`resumed` is 0): a start that resumed the old conversation is not news. `cause` is the stored cause with an underscore for a space: restart, wake, fresh_close, move, handoff, launch; an open set, and a client shows an unknown one as it is.
- `handoff` is the record the session was given, whole up to 64 KiB, with `handoff_truncated` true when it was longer; null where there was none. A line is at most 1 MiB, so one item cannot carry more.
- A launch whose session has not announced itself has `session_id` null. The daemon sends the item again with the same `id` when the id is known, and a client replaces by `id`.
- `id` comes from the same sequence as the other items and is stable between calls; items are in order of `at`, as `before` and `since` already page them.
- **Events.** Topic `chat`, `chat.transcript` (TranscriptItem), when the start is recorded and again if its `session_id` becomes known. No fixture starts a session: the one below reads an agent that already has one.

```jsonc
// components.schemas.TranscriptItem.properties.kind.enum
["reply", "tool", "prompt", "task_event", "session"]

// components.schemas.TranscriptItem.properties.session, optional
{
 "$ref": "#/components/schemas/SessionStart",
 "description": "Where a new session began, drawn as a system line; `at` is when. Only a start of a new conversation is an item: one that resumed the old conversation is not."
}

// components.schemas.SessionStart, new
{
 "type": "object",
 "properties": {
                "session_id": {"oneOf": [{"type": "string", "description": "Null for a launch whose session has not announced itself yet; the item is sent again, same id, when it has."}, {"type": "null"}]},
                "cause": {"type": "string", "description": "Why it began; open set. restart, wake (after an idle close), fresh_close, move (to another account), handoff, launch (by hand)."},
                "actor": {"oneOf": [{"type": "string", "description": "Who asked for a restart; null for any other start."}, {"type": "null"}]},
                "handoff": {"oneOf": [{"type": "string", "description": "The handoff record the session was given, whole up to 64 KiB; null where there was none."}, {"type": "null"}]},
                "handoff_truncated": {"type": "boolean", "description": "The record was longer than 64 KiB and is cut."}
               },
 "required": ["session_id", "cause", "actor", "handoff", "handoff_truncated"]
}
```

Add to `contract/fixtures/chat.json`:

```json
{"fixtures": [
 {"name": "chat.transcript.no_such_agent", "spec": "chat.transcript", "about": "An agent the team does not have is not_found.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "chat.transcript", "params": {"agent": "no.such_agent"}}, "error": {"code": -32001, "data": {"code": "not_found"}}}
 ]},
 {"name": "chat.transcript.session_items_say_how_the_session_began", "spec": "chat.transcript", "about": "A new session is an item of kind session: why it began, who asked, and the handoff it was given. Needs --var session_agent: an agent that started a new session within its last 100 transcript items.", "as": "founder", "steps": [
  {"send": {"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"client": "conformance/1", "protocol": 1, "key": "{{founder_key}}"}}, "result": {}},
  {"send": {"jsonrpc": "2.0", "id": 2, "method": "chat.transcript", "params": {"agent": "{{session_agent}}", "limit": 100}}, "result": {"items": {"$contains": {"kind": "session", "at": {"$type": "timestamp"}, "session": {"$exact": true, "session_id": {"$oneOf": [{"$type": "string"}, {"$type": "null"}]}, "cause": {"$type": "string"}, "actor": {"$oneOf": [{"$type": "string"}, {"$type": "null"}]}, "handoff": {"$oneOf": [{"$type": "string"}, {"$type": "null"}]}, "handoff_truncated": {"$type": "boolean"}}}}}}
 ]}
]}
```
