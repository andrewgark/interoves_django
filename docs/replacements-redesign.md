# Замены: one-copy layout and shared drafts

Status: draft for review. Applies to tasks with `task_type='replacements_lines'`.

## 1. Goals

- Show the task text once, with each slot as an editable element, not as two columns.
- Keep unsubmitted guesses ("drafts") on the server and sync them live between teammates.
- Add quick ways to fill several slots that share the same encoded word.
- Leave checking, scoring, attempts and the task format unchanged.

## 2. Terms

- **Slot**: a place in a line to be restored. The task parser already produces the slots (`_…_` fragments and runs of 2+ capital letters).
- **Encoded word**: the text shown in a slot in the task (FOO). For `_76_` it is `76`.
- **Guess**: the value currently in a slot (BAR).
- **Slot state**:
  - *untouched*: no guess;
  - *entered*: a guess that is not confirmed correct, either never checked or checked and rejected;
  - *correct*: confirmed by a check.
- **Same word**: two slots have the same word when their encoded words are equal after normalisation (section 6). Different word forms (РАБОТЕ / РАБОТЯЩЕГО) don't count as equal.

## 3. Layout

- Each task line is shown once, as running text. Fixed text stays plain, and each slot is drawn as described in section 4.
- At the end of each line there is a narrow column with:
  - the check button,
  - the attempt counter (`n` or `n/max` in tournaments),
  - «Ответ», under the same rules as now,
  - the result message for that line.
  On narrow screens this column moves under its line.
- The input-mode bar («По словам / По строкам / Весь текст») stays above the text (section 8).
- The word list (section 7) goes below the text.

## 4. Slot appearance

| State | Shows | CSS | Clickable |
|---|---|---|---|
| untouched | FOO | `encoded-replacement` | yes |
| entered | BAR, and FOO small underneath | `unsubmitted-replacement` / `encoded-replacement-sub` | yes, opens the editor |
| correct | BAR, and FOO small underneath | `correct-replacement` / `encoded-replacement-sub` | no |

- Clickable slots are `<button>`s with a rounded border, a pointer cursor and a hover shade. Correct slots are plain `<span>`s.
- In a correct slot, BAR is the canonical answer (the first alternative in `checker_data`), the same as solved slots show today.
- Encoded words, guesses and answers are always shown in capitals (`text-transform: uppercase`), as solved answers already are. Guesses are stored as typed.
- There is no separate "incorrect" state in this version. The saved progress only records solved slots, so after a failed check the slots simply stay *entered*.

### 4.1 Styles and themes

- All styles for this game go into a new file, `static/css/replacements.css`.
  - `task_group.html` loads it only on pages with a Замены task, with the usual `?v=<deploy version>` suffix, the same way `html_forms_graph_demo.css` is loaded.
  - The existing replacements rules move there from `new.css`.
- Colours come only from the site's theme variables (`--surface`, `--text`, `--muted`, `--border`, `--accent`, `--success-soft-bg`, `--success-soft-border`, `--focus-ring`, `--warning`). Those already switch between light and dark through `html.new-ui-dark`.
- The game adds one variable of its own, `--repl-draft`, the colour of unsubmitted guesses:
  - light: `#3b6ea5`;
  - dark: `#6b9fd4`, set under `html.new-ui-dark`.
  These are the colours Лесенка already uses for its blue.
- No other colour is hard-coded.

**Text styles**

| Class | Colour | Weight | Size |
|---|---|---|---|
| `encoded-replacement` | `--text` | 600 | 1em |
| `unsubmitted-replacement` | `--repl-draft` | 700 | 1em |
| `correct-replacement` | `--accent` | 800 | 1em |
| `encoded-replacement-sub` | `--muted` | 500 | 0.7em, line-height 1 |

**Slot box (`repl-slot`)**
- Layout: `inline-flex`, stacked vertically and centred, padding `0.1rem 0.4rem`, radius `var(--control-radius)`, `vertical-align: middle`.
- Untouched: border `1px solid var(--border)`, background `--surface`, `cursor: pointer`.
- Hover: background `color-mix(in srgb, var(--accent) 8%, var(--surface))`, border `--accent`.
- Keyboard focus: a 2px `--focus-ring` outline.
- Entered: like untouched, but the border is `color-mix(in srgb, var(--repl-draft) 45%, var(--border))`.
- Correct: border `--success-soft-border`, background `--success-soft-bg`, `cursor: default`, no hover effect.
- Highlighted while hovering or focusing a chip: border `--warning` and background `color-mix(in srgb, var(--warning) 15%, var(--surface))`. A tint, not an outline, so it stays visible on a slot that has the focus ring or is being edited.
- Slot state is never shown by colour alone. Untouched slots show one line of text, entered and correct slots show two, and correct slots also have a filled background.

**Editor and chips**
- The editor box uses background `--surface`, border `--border`, the site's control radius and a shadow. The bottom panel on narrow screens uses the same.
- Chip parts are buttons with the slot-box border and hover rules, joined into one group with shared borders. A completed chip uses the correct-slot colours.

**Contrast**
- Each text colour has a contrast of at least 4.5:1 on its background in both themes, computed from the current theme values:

| Pair | Light | Dark |
|---|---|---|
| unsubmitted on surface | 5.3 | 6.2 |
| encoded-sub on surface | 6.0 | 6.6 |
| correct on correct background | 5.1 | 5.1 |
| encoded-sub on correct background | 5.1 | 4.8 |

- The screenshots for the PR cover both themes.

## 5. Slot editor (popover)

**Opening and closing**
- Clicking an untouched or entered slot opens a small box under it. The box doesn't block the page.
- On narrow screens it is a panel fixed to the bottom of the screen, so the on-screen keyboard doesn't cover it.
- Only one editor is open at a time.

**Contents**
- The encoded word FOO, as a heading.
- A text input, pre-filled with the current guess and focused with its text selected.
- The scope choice (5.1).
- «OK» and «Стереть» (clear) buttons.

**Actions**
- Enter or «OK» saves. Escape or clicking outside closes without saving.
- Saving an empty input, or pressing «Стереть», clears the target slots.
- Saving never checks anything; it only changes the shared drafts (section 9).

### 5.1 Scope

Let FOO be the clicked slot's encoded word and `v` its current guess, which may be empty. The scope is a row of radio buttons:

| Option | Target slots | Label example |
|---|---|---|
| Только здесь | the clicked slot | «Только здесь» |
| Same value | every non-correct FOO slot whose guess equals `v` (all empty FOO slots when `v` is empty) | «Все FOO с «BAR» (2)» / «Все пустые FOO (3)» |
| Все нерешённые | every non-correct FOO slot | «Все нерешённые FOO (3)» |

- The number is how many slots will change, including the clicked one.
- If two options target exactly the same slots, only the broader-sounding one is shown.
- If only one distinct option is left (e.g. FOO occurs once), the row is hidden.
- Correct slots are never changed by any option.

**Default and memory**
- The default is *same value*.
- The browser (`localStorage`) remembers the last choice between *only here* and *same value*.
- *Все нерешённые* is never remembered. It overwrites different guesses, possibly a teammate's, so it must be chosen each time; the next editor starts from the remembered default.

## 6. Comparing words and guesses

- Encoded words and guesses are compared after normalisation: trim leading and trailing spaces, collapse inner runs of spaces to one, and convert to upper case (`toLocaleUpperCase('ru')` in JS, `str.upper()` in Python).
- So `bar`, `Bar ` and `BAR` count as the same guess, and `_foo_` and `FOO` count as the same word.
- Chips and guess lists are deduplicated by the normalised form and show it in capitals.
- This only decides which slots count as having the same word or value. Checking still uses the existing normalisation (`clean_text`).

## 7. Word list (below the text)

- There is one chip per distinct encoded word, deduplicated by section 2 and sorted with Russian collation (`localeCompare(…, 'ru')`).
- A chip is a small group of separate buttons: the word itself, then each distinct guess occurring in that word's slots, e.g. `FOO │ BAR │ BAZ`.
  - Each guess is listed once.
  - It uses the correct style if any slot with that guess is correct, otherwise the unsubmitted style.
  - If a guess is correct in some slots and unchecked in others, it is still listed once, in the correct style, with a small dot.
  - Empty slots are not listed.
- A chip whose slots are all correct is shown as completed, and none of its parts can be clicked.
- Hovering over or focusing any part of a chip highlights the FOO slots in the text (tinted background, see 4.1).

**Clicking the word FOO** opens the editor without the scope row. Its target is:
- all empty FOO slots, if any are empty;
- otherwise all non-correct FOO slots.

The editor states this in one line, e.g. «Заполнит 3 пустых FOO» or «Заменит 2 нерешённых FOO». The input is pre-filled when all target slots hold the same guess.

**Clicking a guess** (e.g. BAZ) opens the editor without the scope row. The target is every non-correct FOO slot whose guess is BAZ, e.g. «Заменит 1 FOO с «BAZ»». A guess that is correct in all its slots can't be clicked.

## 8. Input modes

All three modes stay. They are different views of the same shared guesses.

- **По словам**: the layout from sections 3–7.
- **По строкам**: the same layout, plus a text field and a «Подставить» button under each unsolved line. The field accepts the current formats: Tab-separated, quoted, or the whole restored line.
- **Весь текст**: one textarea above the text, one line per task line, with «Подставить» in the mode bar.
- **When switching modes**, the text fields are filled from the current guesses, as they are today.
- **What the text fields hold:** their contents stay local to the browser (`localStorage`, as today) and are not shared. Only the guesses they produce are shared.
- **«Подставить»** parses the text with the existing parser (`replacements_input_parse.js`). It writes the parsed values into the non-correct slots of the affected lines as one shared change. Correct slots are skipped. The existing rule for empty parsed values is kept.
- This is also how a solution prepared elsewhere, e.g. in a spreadsheet, is imported.

## 9. Shared drafts

**Model:** `ReplacementsUiState`, following `RaddleUiState`.
- Identity: team, user or anonymous player; task; game; mode (`general` / `tournament`); replay slot.
- Fields:
  - `guesses`: JSON object `{"<line>:<slot>": "BAR"}`;
  - `revision`: a number;
  - `updated_by`;
  - timestamps.
- It is separate from `ChainTaskState`, so saving drafts never interferes with checking.

**Endpoint:** `POST /send_replacements_ui/<task_id>/`. It uses the same context parameters as the other play endpoints.
- Body: `changes = {"<line>:<slot>": "BAR" | null}`, where `null` clears a slot.
- The server locks the row, applies the changes slot by slot, increases `revision`, and returns the full `guesses` and `revision`.
- Every change from one editor save or one «Подставить» is sent as a single request.

**Merging**
- Changes are applied per slot, so edits to different slots never overwrite each other.
- For the same slot, the last change to reach the server wins.
- Changes to slots that are already correct are ignored.
- Edits are refused after an official game has ended, the same as Лесенка.

**Live updates**
- After saving, the server pushes `{guesses, revision}` to the team through `track_actor_task_change`, the same way Лесенка drafts are synced.
- Clients apply a push only when its `revision` is newer than theirs, then redraw the slots and chips without reloading the card.
- If the editor is open on a slot a teammate just changed, the editor stays open and shows the teammate's new value under the input.
- The push is sent only after the database transaction commits (`transaction.on_commit`).
- After a WebSocket reconnect, a lost message or returning to the tab, the client fetches the current drafts. The existing task-card recovery endpoint (`/games/<game_id>/live-state/`) will include them.
- Team, personal and anonymous drafts are never mixed.

**Lifetime**
- Drafts are tied to `task.attempt_revision`. When the task is edited, old drafts are ignored.
- A slot's draft is no longer shown once that slot is correct.
- The current browser-only storage of word-box values is removed.

## 10. Checking (unchanged behaviour)

- A line is checked only by its check button.
- The confirmation dialog before a check and its "don't show again" setting stay.
- The request is the current one: `{"line_index": i, "answers": [...]}`. It contains the line's guesses, and correct slots send their canonical answer, as today. If every non-correct slot is empty, the request is refused as now.
- The checker, `ChainTaskState`, scoring, the per-line tournament attempt limit and the «Ответ» endpoint don't change.
- After a check, the card is redrawn as today. The redraw includes the shared drafts, so wrong guesses stay as *entered*.

## 11. Not in this version

- A style for incorrect guesses.
- Undo for changes that fill several slots.
- Fixing `merge_state`, which loses `solved_slots` when accounts are merged.
- Rewriting the outdated rules page `section_tutorial_replacements`.

## 12. Implementation outline

| Area | Change |
|---|---|
| `games/puzzles/replacements/lines/__init__.py` | Also return each slot's encoded word. |
| `games/task_group/replacements_lines_task_data.py` | Add slot states, encoded words, word-list data and shared drafts. |
| New model, migration, view and URL | `ReplacementsUiState` and `/send_replacements_ui/<task_id>/`, following Лесенка. |
| `static/templates/task-content/task-replacements-lines.html` | Rewritten for the one-copy layout. |
| `static/js/task_group_page.js` (replacements part) | Editor, scopes, word list, sync, modes and submit. |
| `static/css/replacements.css` (new) | All Замены styles, light and dark (4.1). Old replacements rules move here from `new.css`. |
| `static/templates/site/task_group.html` | Load `replacements.css` on pages with a Замены task. |
| Tests | Model and endpoint (merge, refusals, live push), page rendering, scope and chip logic in JS, and a two-browser team test in `games.tests.test_realtime_browser`. |
| `docs/realtime-architecture.md` | Record that Замены drafts are synced. |

## 13. Testing

- Django tests through `./run_tests.sh`.
- JS tests for the scope and chip logic, run with Node.
- Manual check on a local server with sample tasks, in two browser sessions of one team:
  - each slot state;
  - each scope;
  - chip clicks;
  - «Подставить» in both text modes;
  - checking a line;
  - tournament attempt limits;
  - narrow screens;
  - light and dark themes.
