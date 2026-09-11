# Wildcard Processor List

`Wildcard Processor List [Eclipse]` expands one prompt or a newline-separated set of prompts with one deterministic wildcard seed. It has no populated-text preview and returns the same processed result in two forms:

- `string` preserves the processed text's line endings, blank lines, indentation, trailing spaces, and other layout.
- `list` contains each non-empty processed line as a separate ComfyUI list item. Whitespace-only lines are omitted, while spacing on retained lines is unchanged.

A source with no newline produces one string and one list item. The complete source is processed as one batch in source order, so a seed drives one continuous sequence of wildcard choices across every line.

## Inputs

| Input | Type | Purpose |
| --- | --- | --- |
| `wildcard_text` | STRING, multiline | One prompt or multiple newline-separated prompts using Eclipse wildcard syntax. |
| `substitutions` | STRING, optional connection | Quoted, case-sensitive replacement assignments. |
| `seed` | INT, socketless | Local fixed, random, increment, or decrement seed. |
| `wildcards` | COMBO | Inserts a wildcard token at the last cursor or selection in the text editor, or appends it when no valid position is available. It returns to `Select a Wildcard` after insertion and when repairing legacy null state during copy/paste or reload. |
| `seed_input` | INT, optional connection | External queue-time seed that overrides the local seed. |

The local seed controls match Wildcard Processor: **Randomize Each Time**, **New Fixed Random**, and **Use Last Queued Seed**. Connecting `seed_input` hides those controls until the connection is removed. Resolved seeds are retained when queueing, saving and reloading the workflow, cloning the node, or changing between classic and Nodes 2.0 rendering.

Right-click the node and toggle **Wrap long lines** to switch the multiline editor between wrapped text and horizontal scrolling. The choice is stored per node and survives workflow reloads, cloning, and renderer changes without altering the prompt text.

Opening the wildcard picker can move focus away from the editor. The node remembers the last valid cursor or selection, inserts the chosen wildcard there, and places the cursor after it. Selected text is replaced. If the prompt changed after the position was remembered, the wildcard is appended instead of applying a stale position.

## Substitutions

Assignments use an identifier, a colon, and a quoted value. Separate assignments with commas or newlines outside quoted values:

```text
S1:"a female character", S2:"wearing blue jeans, a white shirt"
```

Reference a value explicitly in `wildcard_text`:

```text
portrait of {{S1}}
{{S2}}
```

Keys are case-sensitive. When a key is assigned more than once, the final assignment wins. Missing references remain unchanged, and replacements are single-pass: a value containing `{{OTHER}}` does not recursively resolve it.

Commas and newlines inside quotes belong to the value. `\"` decodes to a quote and `\\` decodes to one backslash. Other backslash sequences remain literal, so this assignment preserves the escaped parentheses exactly:

```text
S1:"\(any test\)"
```

Substitutions run before wildcard expansion. A value such as `COLOR:"__colors__"` can therefore introduce file wildcard, inline choice, weighted, quantity, or nested wildcard syntax. Malformed assignments are ignored with a concise warning that does not include prompt contents.

## Layout behavior

The node does not clean, join, trim, or normalize the processed text. For example, this source keeps its blank line, indentation, and CRLF or LF line endings in `string`:

```text
first __subject__

  second {close-up|wide shot}  
```

The `list` output contains the two non-empty processed lines, including the two leading and trailing spaces on the second line.

For a live populated preview or an editable fixed result, use [Wildcard Processor](Wildcard_Processor.md). For syntax and wildcard-library locations, see that guide's wildcard syntax and library sections.
