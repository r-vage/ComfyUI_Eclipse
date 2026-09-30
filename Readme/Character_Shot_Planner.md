# Character Shot Planner

**Character Shot Planner [Eclipse]** creates compatible prompt candidates for a
recurring character and remembers every reserved camera combination. It runs
locally without a model, account, API key, or network service.

Each planned shot has three candidates: close, mid, and wide, with different
poses and, in random expression mode, different expressions. One is selected for generation. The node returns the
selected prompts and their matching seeds and shot IDs as ComfyUI lists, plus a
complete plan and a readable candidate report.

New prompts use separate character/reference, wardrobe, camera/framing, pose,
scene/lighting, anatomy, surface-text, quality and final-output sections. Multiline
reference instructions remain intact. Previously reserved batches keep their saved
prompt formatting; choose a new batch ID to apply formatting changes when the
settings are otherwise unchanged.

It plans prompts; it does not lock an image's identity, inspect generated anatomy,
detect image duplicates, or record whether an image was accepted. Keep your
reference-image conditioning and review the results.

## Customize the examples

Edit these files inside the Eclipse installation:

| File | Editable content |
| --- | --- |
| `prompts/shot_planner/camera.json` | Framing descriptions, camera heights and families, allowed distances, subject orientations, lenses and compositions. |
| `prompts/shot_planner/poses.json` | Pose descriptions with allowed distances and body modes. |
| `prompts/shot_planner/expressions.json` | Expression IDs and descriptions. |
| `prompts/shot_planner/rules.json` | Hand/arm staging, no-text instructions, quality/style wording and close-shot body descriptions. |

Save your edits, then click **Reload planner files** on the planner. A successful
reload reports the current pose/expression counts and refreshes the expression
dropdown without changing its selected value. Invalid files produce a
file-specific error; fix the file and click again. New batches also read the files
automatically, so no restart is needed after editing. Restart ComfyUI once after
installing the node/extension update.

Existing workflows receive the button automatically. No rewiring or additional
node inputs are needed.

Keep `schema_version` at `1`. JSON uses double quotes and does not support comments
or trailing commas. Each object key is a stable ID used in history. Change its
description to improve wording; give genuinely new options new IDs. Renaming an
old option merely to evade history defeats the no-repeat rule.

For example, a pose entry in `poses.json` is:

```json
"walking": {
  "distances": ["wide"],
  "body_modes": ["standing"],
  "text": "Walking slowly with arms relaxed, both legs and feet clearly visible"
}
```

You can add a seated pose like this alongside the existing entries:

```json
"seated_open_book": {
  "distances": ["mid", "wide"],
  "body_modes": ["seated"],
  "text": "Seated with an open blank book resting on the lap, one hand on the book and the other resting separately on a thigh"
}
```

Only `close`, `mid`, and `wide` distances and `seated`/`standing`/`lying` body modes are
supported. Set compatibility according to what will be visible. For example,
walking needs wide framing; table-height camera options normally suit close/mid
shots. The planner follows your edited matrix and cannot infer anatomy from prose.

Each pose can also have `"category": "everyday"`, `"category": "sports"`, or `"category": "sexy"`.
Omitting it means `everyday`, so existing personal files still load. The node's
`pose_category` filter combines with `body_mode`; `all` includes all three categories.
For boxing and martial arts, use **sports + standing + wide selection** to show
complete stances, footwork and kicks. Close sports shots show athletic preparation
and shoulder/neck warm-ups, not off-frame actions. Seated/lying sports include
compatible stretches and recovery poses. The filter does not change clothing or
the scene; describe those separately if needed.

**Sexy** contains suggestive solo glamour poses for standing, seated and lying
subjects. Close portraits vary shoulder/head posture; mid shots show torso and
arm placement; wide poses include complete leg positions. Wardrobe and facial
expression remain independent. Each posture has enough compatible examples for
the default pose cooldown.

Wide glamour poses include forward hinges, a bent-knee wall lean, sitting
astride a chair, folded floor seating, sitting on the heels, lying on the back
with knees apart, prone raised feet, and low kneeling with the chest or forearms
supported on the floor. The low kneeling examples belong to `lying` because the
upper body is supported near floor level. These entries describe body placement
without specifying gender, clothing or a facial expression. Close portraits keep
the head-and-shoulders crop; leg-dependent poses are wide-only. Camera orientations
provide independent front, side and rear views, rather than duplicating a pose
for each angle.

The supplied everyday poses vary benches, stools, sofas, steps, ottomans, floor
cushions and chairs. Seated reclining keeps the torso raised on a sloped support;
lying keeps the body horizontal on a mat, blanket, sofa or daybed. Pose cooldowns
apply to pose IDs, not furniture, so the same support can still recur.

Camera entries have `family`, `text`, and `distances`. The family controls the
camera cooldown. Expression entries are simply `"stable_id": "description"`.
The expression control offers `random`, `off`, and these IDs. Random preserves
distinct candidates and expression cooldowns. A fixed ID applies its wording to
every candidate, ignoring expression cooldown. Off omits planner-generated
expression wording and preserves your character, wardrobe and scene text.
`random` and `off` are reserved control IDs and cannot be expression-file keys.
If a selected ID is removed, it stays selected after reload: new allocations
report a clear error until you restore the ID or select another option. Saved
batches still replay their snapshots, including removed fixed expressions.
The optional `camera.json.body_overrides` object provides posture-specific text:
for example, `body_overrides.lying.cameras.eye` describes a camera beside the
horizontal subject. Its `distances`, `cameras` and `orientations` maps use existing
IDs; omitted entries fall back to the ordinary text. Remove matching overrides
when deleting an ID. Overrides change wording, not camera keys or reservations.
`rules.json.body` needs a posture description for every body mode used by poses.
At least three expressions and three jointly compatible poses are needed for a
candidate set; cooldowns usually require larger pools. Custom pools may exhaust
sooner than the defaults. The loader also bounds file size (256 KiB per file),
category size (128 entries) and total camera combinations (50,000).

To change photographic output to an illustration style, edit `quality` in
`rules.json` **and** the distance descriptions in `camera.json` where they say
photograph/portrait. The character, outfit and scene fields remain editable on
the node. Optional staging, no-text and quality rules can be empty strings.

Reserved batches retain a snapshot of their pools and their resolved prompts.
Editing/removing an option does not change a reserved batch or release its camera
keys. Reuse its batch ID to replay, or choose a **new batch ID** to use the edited
files. This also allows old batches to replay while a current file contains a
typo; new batches and the reload button will report that typo.

Defaults are distributed under `.defaults/prompts/shot_planner/*.json.example`.
The editable directory is initialized when absent. Existing files are preserved,
including malformed files; a missing file in an existing directory is reported
rather than silently replaced. To restore a file deliberately, copy its matching
`.example` file into `prompts/shot_planner/` and remove the `.example` suffix.
Keep personal edits in the editable files, not the distributed defaults.
Older personal pools remain valid but need lying/sports/sexy entries to use those
filters. Merge the new pose entries, the `lying` body rule and camera overrides
from the matching examples, or deliberately restore all matching defaults.

## Basic wiring

```text
Character Shot Planner.prompts → text encoder → reference conditioning → sampler
Character Shot Planner.seeds   → sampler.seed
Character Shot Planner.report → Show Any
```

Keep prompt and seed list order identical. Use a one-image latent per list item;
a larger latent batch creates multiple images for every planned shot.

For multiple generation branches, connect `plan` to **Shot Plan Slice [Eclipse]**.
Each slice returns aligned `prompts`, `seeds`, and `shot_ids`. `start` is zero-based;
`count = 0` means all remaining shots. Invalid ranges raise an error rather than
wrapping or repeating the last item. For a final batch shortened by coverage
completion, slices truncate safely and branches past the end are silently blocked.

For example, a 40-shot plan can feed two slices:

| Branch | start | count |
| --- | ---: | ---: |
| Variation 1 | 0 | 20 |
| Variation 2 | 20 | 20 |

## Controls

| Control | Meaning |
| --- | --- |
| `project` | Name of the character/project whose reservation history is shared. Connect a character-name string if useful. |
| `batch_id` | Base ID such as `batch-001`. Same settings replay; changed settings automatically resolve to `batch-001-2`, `batch-001-3`, etc. Change the base for fresh shots with identical settings. |
| `count` | Up to 1–100 selected shots per batch; coverage completion can return fewer. |
| `character_lock` | Required description placed first in every prompt. Describe identity without forcing front-facing poses or eye contact. |
| `outfit_lock` | Optional fixed wardrobe. Empty leaves clothing to the generator and references; it does not randomize clothing. |
| `scene` | Shared scene and lighting instructions. |
| `body_mode` | `any`, `seated`, `standing`, or `lying`. Any includes all postures; seated includes supported reclining. Standing permits walking and sports stances in wide shots. |
| `expression` | `random` (default), `off`, or an ID from expressions.json. Fixed/off ignore expression cooldown. |
| `pose_category` | `all`, `everyday`, `sports`, or `sexy`. Combines with body mode and sits immediately above selection. |
| `selection` | `balanced` distributes selections across close/mid/wide using project history. Alternatively select the close, mid, or wide candidate for every shot. |
| `choices` | Optional exact sequence of `close`, `mid`, or `wide`, separated by commas, spaces, or newlines. Supply one value per shot. Overrides `selection`. |
| `seed` | Fixed planner seed, also used to derive each candidate's generation seed. Can be connected to an existing Seed node. |
| `pose_cooldown` | Number of previous selected shots whose poses cannot be offered again. Default 3. |
| `expression_cooldown` | In random mode only, previous selected shots whose expressions cannot be offered again. Default 3; remains visible in all modes. |
| `camera_cooldown` | Number of previous selected shots whose eye/high/low/table camera families cannot be offered again. Default 2. |
| `stop_when` | `poses` (new-node default), `expressions`, `poses_and_expressions`, `cameras`, or `never`. See below. |
| `operation` | `preview` ignores saved history and computes from current files without reserving; `reserve` atomically saves the selected and skipped candidates. |

## Preview, select, reserve, replay

**Stop when** controls the finite list to finish:

- **poses**: stop after every compatible pose ID has appeared in a selected output.
- **expressions**: stop after every expression has appeared in a selected output.
  A fixed expression or off has one variant, so this mode finishes after one
  matching selected shot.
- **poses_and_expressions**: finish both lists. The shorter list repeats while
  the longer one finishes; this does not require every pose/expression pairing.
- **cameras**: retain the legacy unique-camera restriction.
- **never**: keep planning new batches, cycling depleted camera pools.

In reserve mode, pose/expression coverage uses selected reservations in project history, filtered
by current body mode, pose category and possible output distances. Skipped
candidates do not consume that coverage. Balanced selection prefers distances
with uncovered compatible poses. Explicit selection/choices keep their requested
distances; unreachable poses do not prevent completion. Adding new IDs extends
coverage. As elsewhere in this node, reservation is not proof of image generation.

In every mode except cameras, each distance uses unused camera combinations
before cycling its least-used combinations. A short close/wide camera list can
repeat while the longer mid list continues. Camera cooldown relaxes only when
necessary, with a warning; pose and random-expression cooldowns remain in force.

In reserve mode, on coverage completion the planner returns the remaining shots, even if fewer
than `count`, saves that final batch in reserve mode, and shows a yellow ComfyUI
popup. Automatic queueing stops without cancelling generation of those final
shots. Shot Plan Slice truncates to available shots; branches beyond the end are
silently blocked. A new request after completion generates nothing and shows a
warning. The saved final batch still replays exactly when requested again.
Camera/pose/expression compatibility or cooldown exhaustion also stops cleanly
with a yellow warning and saves none of the incomplete batch. Invalid files,
removed expression IDs and malformed ledgers still report actionable errors.

Existing workflows migrate automatically, including old positional values,
connected inputs and subgraphs. They default to expression=random and
stop_when=cameras to preserve their old settings and saved-batch replay. Choose
poses to adopt the new default behavior; this creates a new batch variant.

1. Connect `report` to Show Any and set `operation` to `preview`.
2. Execute the planner/report branch only. Review the three candidates per shot.
3. Adjust the locks, settings, or `choices`. For three shots, an example is
   `wide, close, mid`. Re-preview after changes: choices affect cooldowns and may
   change the candidates in later sets.
4. Set `operation` to `reserve` before generating images. With unchanged settings
   and empty project history, the preview and reserved candidates match.
5. Requeue with the same settings and batch ID to reproduce the reserved plan.
6. For fresh shots, change `batch_id`, for example to `batch-002`.

Preview starts with empty history, ignores all saved batches/reservations, and uses
the current editable files. It never reads or updates the ledger. Coverage limits
still apply within the preview: for example, 19 matching poses can produce a final
19-of-50 plan. This shows an informational toast and leaves automatic queueing
enabled. Choose `stop_when=never` to cycle beyond coverage. Preview outputs can
generate images when connected to generation branches, but do not reserve them.
Reserve uses project history, so its candidates may differ when history exists.

After reservation, changing the seed, count, locks, cooldowns, body mode, expression,
pose category, stop mode, selection or choices automatically selects a numbered
variant of the requested batch ID.
For example, changes under `batch-001` use `batch-001-2`, then `batch-001-3`.
Existing reservations remain intact. Repeating the same changed settings finds
and replays their existing variant; restoring the original settings replays the
original batch. Concurrent retries share one reservation.

The input widget stays at the base ID. The report, plan's `batch_id`, and all shot
IDs show the actual resolved ID; `requested_batch_id` records the input value.
Preview always uses the requested base ID without consulting saved variants. Very long IDs use a shortened
prefix with a hash before the number to remain within the 80-character limit.
Changing a connected global seed also changes the planner settings, so it reserves
a new variant. Each new variant consumes camera history just like a manual batch.
Pool-file edits alone still require a new base ID because existing batches replay
their saved pools. Use a new base ID for fresh shots with otherwise identical settings.

Reservations describe planned shots. A failed/cancelled generation does not
release them. Reuse the same plan in reserve mode to retry, or reset deliberately
when you want a fresh start.

## Resetting reservations

Stop queueing, then click **Reset reservations** on the planner. Enter the project
name in the confirmation dialog. If `project` is connected, use the actual connected
name (for example, your character name), not the inactive widget value. Reset clears
all saved batches and their coverage for that project while keeping its name. You
can reuse the same batch IDs. Other projects, generated images and editable prompt
files stay intact. The cleared plans can no longer replay; back up their ledger first
if you need them later. Reset does not cancel an already running generation.

Changing the batch ID or clicking **Reload planner files** does not reset history.
Reservations persist across restarts until reset or removal; the project name itself
is not permanently unavailable. For manual cleanup, stop queueing and back up or
delete **only the project ledger path shown in the report** under
`output/eclipse/shot_ledgers/`. Do not delete `prompts/shot_planner/`: those files
contain the editable pose, expression and camera lists, not reservations. A malformed
ledger is preserved by the reset button and can be backed up/removed manually.

## History and compatibility

Ledgers live under the configured ComfyUI output directory:

```text
output/eclipse/shot_ledgers/<project-name-hash>.json
```

The report shows the exact ledger path. Files retain the project label, batch
settings, candidate prompts, attributes, seeds, and selected/skipped status.
They use locked atomic writes. A malformed ledger fails without being replaced.
Back up this folder along with workflows to preserve history. Reusing a project
label shares its history; use a new label for a different character or fresh
project. Merely saving another workflow filename does not create new history.

The permanent camera key is:

```text
distance + camera height + subject orientation + lens family + composition
```

All three candidates reserve their keys, including the two skipped candidates.
In cameras stop mode, a different pose or expression cannot make a used key
available again. Other stop modes allow camera cycling without deleting history.
Cooldowns are separate temporary exclusions measured in selected shots, including
earlier batches. These rules apply to planner output; unrelated reference shots
elsewhere in a workflow are not imported into its ledger.

The default pools provide 225 close, 270 mid, and 225 wide camera keys. Because
every set needs one of each, in cameras stop mode 225 sets is a combinatorial upper bound, **not a
promised usable capacity**. Cooldowns and previous reservations can make a batch
impossible sooner. In that case the new batch stops with a yellow warning and
without partial reservation. Choose a pose/expression stop mode to let camera
pools cycle, reduce cooldowns, or start a separately named project.

For repeated large batches in cameras stop mode, consider `camera_cooldown=1`. Wide shots have only eye/high/low families; excluding the
previous two selected families can force the remaining family even when its
unique combinations are nearly depleted. Lowering this cooldown retains the
permanent no-repeat camera rule. A new batch ID shares the same project history
and does not replenish its pools. Tune pose and expression cooldowns separately.

Compatibility rules include table-height cameras only for close/mid shots,
walking only in wide shots with feet visible, body-mode filtering, and actions
appropriate to the visible crop. The planner favors less-used subject orientations
and placements. Rear views remain suitable for separate controlled reference
shots; the variation pool uses visible facial expressions.

Every prompt includes simple hand/arm staging and asks for blank, unprinted
clothing, signs, menus, packaging, book covers, screens, and labels. These are
generation instructions, not guaranteed defect removal. Lens and composition
differences also need to be verified in the generated images.
