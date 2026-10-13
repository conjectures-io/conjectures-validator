# Build a Conjectures chat-log file — instructions for an AI agent

Give these instructions to your AI agent (Claude Code, Codex, ChatGPT or similar) together
with the files listed under *Inputs*, and ask it: "Follow these instructions to build my
Conjectures chat-log JSON." Check the result yourself before uploading it.

## Your task

Turn the contributor's exported AI conversation into one JSON file that matches
`trajectory.schema.json` and passes every rule below. A reviewer checks that these are the
original chat logs that produced the proof, not a recreation, so copy what happened and never
improve it.

## Inputs (ask the contributor for anything missing)

Wherever these instructions say to ask the contributor and you cannot, make the conservative
choice (leave it out, or `null` with a reason) and say what you chose in your report.

1. **The conversation(s)** in which the proof was found, in the tool's own format:
   - **Claude Code:** session files are `~/.claude/projects/<folder>/<session-id>.jsonl`, where
     `<folder>` is the working directory with `/` replaced by `-`. If you are Claude Code,
     you can read these yourself. Subagent sessions live in `<session-id>/subagents/`; include
     those that worked on the proof as extra runs.
   - **Codex CLI:** session files are `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. If you are
     Codex, you can read these yourself.
   - ChatGPT: Settings → Data controls → Export data; use `conversations.json` from the zip.
   - Claude: Settings → Privacy → Export data; use `conversations.json` from the zip.
   - API scripts: the request/response logs they saved.

   To find the right session, search the files for the theorem name or a line of the final
   proof, then confirm with the contributor. Ask for the provider's share link too, if any.
2. **The final proof file** exactly as it will be (or was) submitted.
3. **The values shown on the Conjectures page:** `task_id`, `task_bundle_sha256`,
   `final_proof_sha256`, and `submission_id` — `null` for a new bounty (logs go in before the
   proof), or the submission's UUID for the earlier-proof bonus.
4. Optionally: Lean toolchain, Mathlib commit, and any code or config used.

Download `template.json`, `trajectory.schema.json` and `synthetic-example.json` from the
Conjectures chat-log page. Start from the template; the example shows a complete (made-up) file.

## Rules

**Copy, never invent.** Message text, tool calls and tool results go in verbatim, in the
order they happened, including failed attempts, errors and dead ends. Do not summarize,
reword, reorder, fix typos or fill gaps. If something is not in the export, use `null` with a
short `unknown_reason`, or list it under `missing`.

**Only what the user could see.** Set every event's `visibility` to `"user_visible"`. Leave out hidden system
prompts and model reasoning or "thinking" blocks, even if the export contains them.

**Only the relevant conversations.** Include every conversation that led to the proof and
nothing else from the account.

**Remove secrets.** Replace passwords, API keys, tokens and personal details the contributor
does not want shared with `[REDACTED]`, and add one entry per redaction to `redactions`
(`field_or_event`, `reason`, `impact_on_reproduction`). Usernames inside file paths can stay
unless the contributor asks otherwise. Ask the contributor about anything unclear.

## Reading Claude Code and Codex sessions

Copy the original session file somewhere outside the folder you will upload from, and hash
that untouched copy first. Then read it line by line; each line is one JSON record with its own
`timestamp`. Copy tool inputs and outputs exactly as stored: if the file holds them as a JSON
string, keep that string; do not parse, unwrap or pretty-print them.

- **Claude Code:**
  - Use records whose `type` is `user` or `assistant`.
  - `message.content` is either a plain string (treat it as one `text` block) or a list of blocks.
  - Map the blocks:
    - `text` → `user_message` or `assistant_message`;
    - `tool_use` → `tool_call`, using the block's `id` as `tool_call_id`, its `name` as
      `tool_name`, and its input as the content, verbatim JSON;
    - `tool_result` → `tool_result`, matched on `tool_use_id`, even when `is_error` is true;
      the error text itself is the content.
  - Skip `thinking` blocks and every other record type (attachments, costs, titles, queue
    entries). Those are bookkeeping, not the chat.
  - Records with `"isSidechain": true` belong to subagents: make them a separate run.
- **Codex:**
  - Use `response_item` records.
  - `payload.type` `message` with role `user` or `assistant` → the message (its `input_text`
    or `output_text` parts).
  - `function_call` or `custom_tool_call` → `tool_call` (`call_id`), and
    the matching `…_output` → `tool_result`.
  - Skip `reasoning` items, `developer` or system messages, and every other record type
    (`event_msg`, `turn_context`, token counts, session metadata). Read `turn_context` only for
    the model name and `session_meta` only for the CLI version.
  - If a `compacted` record shows earlier history was summarized away, list it in `missing`.
- In both: if the work spans several sessions, add each as its own run, in time order.
  If the contributor typed into the session itself, those are user messages; edits they made
  outside it are not in the log, so say so in `reproduction.limitations`.

## Filling the file

- `schema_version`: `"conjectures-trajectory/v1"`; `synthetic`: `false`.
- `submission_id`, `task_id`, `task_bundle_sha256`, `final_proof_sha256`: exactly the values
  from the Conjectures page.
- `exported_at`: when the contributor exported the conversation, ISO 8601 with timezone. Must
  be at or after every event time and not in the future. For Claude Code or Codex sessions read
  straight from disk, use the time you copied the session files. Otherwise, if the contributor
  doesn't know, use the export file's creation time.
- `runs`: one entry per conversation/session.
  - `id`: e.g. `run-1`.
  - `provider`, `model`: from the export, or `null` + `unknown_reason`.
  - `version`: the model's exact snapshot or date if the export shows it; otherwise `null` +
    `unknown_reason`. The app or CLI version goes in `interface`, not here.
  - `interface`: e.g. "ChatGPT web", "Claude Code CLI 2.1.280", "Codex CLI 0.120.0".
  - `account_agreement`: the plan used, e.g. "ChatGPT Plus" or "Claude Max".
  - `terms_reference`: the provider terms for that plan, e.g. "OpenAI Terms of Use" or
    "Anthropic Consumer Terms". Session files rarely show either; ask, or use `null` + reason.
  - `settings`: model settings if known; otherwise "Not shown in export".
  - `export_method`: how it was exported, plus the share link if there is one.
  - `source_export_sha256`: `sha256:` + hex SHA-256 of the original exported file, unmodified,
    even if it also holds other conversations or the secrets you redacted. If there is no file,
    set it to `null` and explain in `source_export_unavailable_reason`. The contributor keeps
    that original file private: a reviewer may ask for it.
- `events`: one per visible message or tool step, in order.
  - `sequence`: 1, 2, 3, … with no gaps; `id`: unique, e.g. `event-1`.
  - `kind`: `user_message`, `assistant_message`, `tool_call`, `tool_result`, `failure` or
    `revision`. A tool's error stays its `tool_result`. Use `failure` only for an error the app
    itself showed outside any message or tool result, and `revision` only for an edit the log
    itself records as such. Never add either to describe what happened.
  - `timestamp`: the export's time for that message, converted to ISO 8601 with timezone
    (ChatGPT `create_time` is Unix seconds); keep the precision the export has, milliseconds
    included. Use `null` + `unknown_reason` only when the export really has no time. Known times
    must never go backwards.
  - `run_id`: the run it belongs to. Required on every assistant message.
  - `tool_call_id` and `tool_name`: only on `tool_call`/`tool_result`, and only for tool steps
    that are in the export. Compiler output the user pasted in stays a `user_message`. Each
    result follows its call with the same id, run and tool name. If a result is missing, say so
    in `missing`.
  - `content`: the verbatim text. If it is really empty, use `""` and set `empty_content` to `true`.
  - `parent_event_id`: for regenerated or edited branches, the earlier event they branch from.
    Otherwise omit it or use `null`.
  - `artifact_ids`: artifacts this event produced or showed. **The event where the final proof
    appears must list the final-proof artifact and have a real timestamp.** That is when the chat
    produced the proof. For an earlier proof it must be before the proof's submission time. Use
    the first event after which the proof is in its final form: the message that contains it
    in full, or, when the proof was written by tools (Claude Code `Write`/`Edit`, Codex
    `apply_patch`), the last tool call that wrote or patched the proof file. If no event gets
    it exactly (say the contributor tidied it afterwards), use the closest one and explain the
    difference in `reproduction.limitations`. It is fine that the log contains the proof: it is
    private to reviewers.
- `artifacts`:
  - Exactly one `final_proof`, with `sha256` equal to `final_proof_sha256`. Prefer setting
    `availability` to `"authoritative_submission"` with `content: null`, which points at the
    submitted proof instead of copying it.
  - Other code, config or logs: `"embedded"` with the exact UTF-8 `content` and its SHA-256, or
    `"omitted"` with an `omission_reason` plus an entry in `missing`.
  - `path`: a relative label like `Solution.lean`. Letters, digits, `_ . - /` only, no `..`.
- `reproduction`: `lean_toolchain` (e.g. `leanprover/lean4:v4.27.0`), `mathlib_commit`,
  `task_repository_commit` and `code_commit` (each a value or `null` + reason), plus
  `environment`, `commands` (a list; `["Not recorded"]` if nobody recorded one), `expected_result`
  and `limitations`. Unknown is fine; say why. Use `limitations` to say what you deliberately
  left out (hidden system messages, reasoning, unrelated chats). Those are not gaps, so they do
  not go in `missing`.
- `completeness`: `complete_export` if `missing` is empty; otherwise `gaps_disclosed`. (The
  template's value is only a placeholder.)
- `permissions`: copy it from the template unchanged. The website fills it from the
  contributor's own checkbox.
- No fields other than those in the schema.

## Check before handing it back

1. Validate against `trajectory.schema.json`. For example, in Python with `jsonschema`:
   `jsonschema.validate(data, schema, format_checker=jsonschema.FormatChecker())`. Format checks
   only run if their helper packages are installed, so also check by hand that every timestamp
   parses as ISO 8601 with a timezone and that `submission_id` is `null` or a UUID.
2. Recompute every SHA-256 over the exact UTF-8 bytes and compare.
3. Check these rules too, which the schema alone does not:
   - sequences are contiguous;
   - known times never go backwards and are no later than `exported_at`;
   - every tool result matches an earlier call;
   - there is at least one user and one assistant message;
   - one final proof, attached to a dated event;
   - omissions are listed in `missing`.
4. Keep the file under 32 MiB, 10,000 events, 100 runs and 1,000 artifacts.
5. Tell the contributor what you left out or redacted, and where any timestamps were unknown.

The contributor uploads the file on the Conjectures site and agrees to the chat-log terms
there. You never upload it or accept terms for them.
