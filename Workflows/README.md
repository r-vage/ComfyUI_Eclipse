# Get All Active shared-chain preview

Open [GetAllActive_Shared_Chains_Preview.json](GetAllActive_Shared_Chains_Preview.json) in ComfyUI, or drag it onto the canvas. Requires **Eclipse 4.4.5 or newer** and built-in ComfyUI nodes only. No model downloads or input files are needed. It uses small solid-color images and works in classic canvas and Nodes 2.0.

The workflow includes notes, numbered groups, and two independent shared chains. Reload the original JSON to reset your experiments.

To build your own workflow, see [Starting a new workflow](../Readme/GetFirst_GetAllActive.md#starting-a-new-workflow) for creating setters first or planning a shared list before setters exist, choosing priority order, and joining new getters.

## Start with the colored previews

Click **Run**. The four Preview Image nodes show:

| Group | Member list | Initial preview | After muting `Set_demo_red` |
| --- | --- | --- | --- |
| 1 — Full chain | red → green → blue | Red | Green |
| 2 — Starts at green | green → blue | Green | Green |
| 3 — Excludes green | red → blue | Red | Blue |
| 5 — Compatible removal | red → green → blue | Red | Green |

Select `Set_demo_red` and mute it with **Ctrl+M**, then Run again to see fallback. Undo the mute before starting another experiment. Group 4 is an unwired member: its list synchronizes without creating connections.

## Insert a shared variable

1. Right-click the getter in group 1 and choose **Sync chain → Edit shared chain**.
2. Insert `demo_gold` between `demo_red` and `demo_green`. Its source and setter already exist.
3. Review the preview, then **Apply**. Groups 1, 3, and 4 gain the variable. The wired members retain their links and gain a trailing connection. Group 2 starts at green, so it stays unchanged. Group 5 belongs to the other chain.
4. Mute `Set_demo_red` and Run: groups 1 and 3 show gold; groups 2 and 5 show green.

**Cancel** leaves the workflow unchanged. One undo reverses the shared edit. **Member options** changes only one getter's start or exclusions. **Unlink** preserves its current list and wires. To try creation, unlink two compatible getters, select them, and choose **Create from selected getters**.

## Compare the two removal paths

Use the **Safe removal comparison** chain on the right. Its upper getter mixes IMAGE and LATENT variables; group 5 excludes the latent variable and contains only images.

1. Open **Edit shared chain** from either member and delete `demo_green`.
2. **Apply**. The mixed member disconnects only the green output. Red, latent, blue, and the extra blue branch retain their destinations. The collapsed target nodes make the remaining wires easy to follow.
3. Group 5 compacts the compatible image values through existing positions and disconnects its last output.
4. Undo once to restore both members and their wires.

The collapsed targets are wiring examples. They do not require a sampler or VAE to run the four image previews.

For an atomic-rejection example, delete `demo_green` and add `demo_gold` in the same mixed-chain draft. **Apply** is blocked by the incompatible update; **Apply removals only** still removes green safely.

Deleting `Set_demo_green` demonstrates automatic cleanup in both chains. Renaming a setter updates the linked references. Save/reload preserves chain definitions, member options, and ordinary variable lists.
