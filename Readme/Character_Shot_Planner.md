# Character Shot Planner

**Character Shot Planner [Eclipse]** creates compatible prompt candidates for a
recurring character and remembers every reserved camera combination. It runs
locally without a model, account, API key, or network service.

Each planned shot has three candidates: close, mid, and wide, with different
poses and expressions. One is selected for generation. The node returns the
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
reload reports the current pose/expression counts. Invalid files produce a
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

Each pose can also have `"category": "everyday"` or `"category": "sports"`.
Omitting it means `everyday`, so existing personal files still load. The node's
`pose_category` filter combines with `body_mode`; `all` includes both categories.
For boxing and martial arts, use **sports + standing + wide selection** to show
complete stances, footwork and kicks. Close sports shots show athletic preparation
and shoulder/neck warm-ups, not off-frame actions. Seated/lying sports include
compatible stretches and recovery poses. The filter does not change clothing or
the scene; describe those separately if needed.

The supplied everyday poses vary benches, stools, sofas, steps, ottomans, floor
cushions and chairs. Seated reclining keeps the torso raised on a sloped support;
lying keeps the body horizontal on a mat, blanket, sofa or daybed. Pose cooldowns
apply to pose IDs, not furniture, so the same support can still recur.

Camera entries have `family`, `text`, and `distances`. The family controls the
camera cooldown. Expression entries are simply `"stable_id": "description"`.
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
Older personal pools remain valid but need lying/sports entries to use those
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
`count = 0` means all remaining shots. Invalid or empty ranges raise an error rather
than wrapping or repeating the last item.

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
| `count` | 1–100 selected shots; each reserves three candidates. |
| `character_lock` | Required description placed first in every prompt. Describe identity without forcing front-facing poses or eye contact. |
| `outfit_lock` | Optional fixed wardrobe. Empty leaves clothing to the generator and references; it does not randomize clothing. |
| `scene` | Shared scene and lighting instructions. |
| `body_mode` | `any`, `seated`, `standing`, or `lying`. Any includes all postures; seated includes supported reclining. Standing permits walking and sports stances in wide shots. |
| `pose_category` | `all`, `everyday`, or `sports`. Combines with body mode. Sports includes solo boxing, martial arts and exercise; everyday keeps casual poses. |
| `selection` | `balanced` distributes selections across close/mid/wide using project history. Alternatively select the close, mid, or wide candidate for every shot. |
| `choices` | Optional exact sequence of `close`, `mid`, or `wide`, separated by commas, spaces, or newlines. Supply one value per shot. Overrides `selection`. |
| `seed` | Fixed planner seed, also used to derive each candidate's generation seed. Can be connected to an existing Seed node. |
| `pose_cooldown` | Number of previous selected shots whose poses cannot be offered again. Default 3. |
| `expression_cooldown` | Number of previous selected shots whose expressions cannot be offered again. Default 3. |
| `camera_cooldown` | Number of previous selected shots whose eye/high/low/table camera families cannot be offered again. Default 2. |
| `operation` | `preview` computes without reserving; `reserve` atomically saves the selected and skipped candidates. |

## Preview, select, reserve, replay

1. Connect `report` to Show Any and set `operation` to `preview`.
2. Execute the planner/report branch only. Review the three candidates per shot.
3. Adjust the locks, settings, or `choices`. For three shots, an example is
   `wide, close, mid`. Re-preview after changes: choices affect cooldowns and may
   change the candidates in later sets.
4. Set `operation` to `reserve` before generating images. With unchanged settings
   and unchanged project history, the preview and reserved candidates match.
5. Requeue with the same settings and batch ID to reproduce the reserved plan.
6. For fresh shots, change `batch_id`, for example to `batch-002`.

Preview is provisional. Another reservation in the same project may change the
next preview/reservation. Preview outputs are ordinary outputs and can generate
images if you connect and execute the generation branches; that does not record
them in the ledger. Use reserve for real generation.

After reservation, changing the seed, count, locks, cooldowns, body mode, selection
or choices automatically selects a numbered variant of the requested batch ID.
For example, changes under `batch-001` use `batch-001-2`, then `batch-001-3`.
Existing reservations remain intact. Repeating the same changed settings finds
and replays their existing variant; restoring the original settings replays the
original batch. Concurrent retries share one reservation.

The input widget stays at the base ID. The report, plan's `batch_id`, and all shot
IDs show the actual resolved ID; `requested_batch_id` records the input value.
Preview resolves the same way without saving history. Very long IDs use a shortened
prefix with a hash before the number to remain within the 80-character limit.
Changing a connected global seed also changes the planner settings, so it reserves
a new variant. Each new variant consumes camera history just like a manual batch.
Pool-file edits alone still require a new base ID because existing batches replay
their saved pools. Use a new base ID for fresh shots with otherwise identical settings.

Reservations describe planned shots. A failed/cancelled generation does not
release them. Reuse the same plan to retry. There is no automatic project reset.

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
A different pose or expression cannot make a used camera key available again.
Cooldowns are separate temporary exclusions measured in selected shots, including
earlier batches. These rules apply to planner output; unrelated reference shots
elsewhere in a workflow are not imported into its ledger.

The default pools provide 225 close, 270 mid, and 225 wide camera keys. Because
every set needs one of each, 225 sets is a combinatorial upper bound, **not a
promised usable capacity**. Cooldowns and previous reservations can make a batch
impossible sooner. In that case the entire new batch fails without partial
reservation. Reduce cooldowns or start a separately named project; strict camera
exclusions are never silently relaxed.

For repeated large batches, consider `camera_cooldown=1`. Wide shots have only eye/high/low families; excluding the
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
