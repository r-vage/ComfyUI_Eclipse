# Image Review Filter

**Image Review Filter [Eclipse]** splits images into kept and rejected lists using
one aligned JSON report per image. Find it under **Eclipse → Image → Batch
Operations**. Inference stays in SmartLLM or another upstream reporting node.

Connect SmartLLM's **image** output to **images** and its **text** output to
**reports**. Use the outputs from the same execution. A batch with eight images
requires eight report strings, not one string containing an array of reports.
Different counts stop execution with an error rather than guessing alignment.

Each report has this format:

```json
{"verdict":"reject","reasons":["A clearly visible third arm extends from the left shoulder."]}
```

- `pass`: keep the image. An empty `reasons` list is allowed.
- `review`: keep the image and flag it for manual review.
- `reject`: reject only when the complete report is valid and contains at least
  one nonblank reason.

Verdicts are exact lowercase strings. Reasons must be a list of nonblank strings.
An enclosing JSON or plain code fence is accepted. Invalid JSON, duplicate fields,
unknown verdicts, missing fields and incomplete reasons become `review` with an
explanation. Prose surrounding JSON and arrays of JSON objects are not accepted.
Model-provided reasons are displayed as text; this node does not verify their
accuracy or infer a verdict from prose.

The **kept** and **rejected** outputs are Python image lists of single-frame
`[1,H,W,C]` tensors. Batches, lists and mixed dimensions are supported, and each
group preserves input order, pixels, dtype and device. There is no resizing,
overlay, image saving or placeholder insertion. Connect each output to **Preview
Image (DOM)**, which also handles empty lists. Previews create temporary files.

The **report** output connects to **Show Any**. It lists totals, each original
one-based input image number, verdict, reasons, and destination preview position:

```text
Totals: 3 images | Kept: 2 (pass: 1, review: 1) | Rejected: 1

1. PASS → Kept #1: No defects reported.
2. REJECT → Rejected #1: A clearly visible third arm extends from the left shoulder.
3. REVIEW [MANUAL REVIEW] → Kept #2: The overlapping fingers are ambiguous.
```

Numbers refer to images entering the filter, in flattened input order. If an
upstream selector chooses a subset, these are the selected image numbers, not
filenames or original folder indices.

## Anatomy review workflow

The standalone user workflow **Image Anatomy Review - SmartLLM Test.json** uses:

```text
Load Files From Folder (Step) → Image Selector → Smart LM Loader → Image Review Filter
                                                                   ├─ Kept — pass + review
                                                                   ├─ Rejected
                                                                   └─ Show Any (numbered report)
```

1. Enter a folder path in the loader. The initial range is frames `0–7` (up to
   eight images), sorted by name, with a fixed batch index.
2. Queue, select images, and click **Confirm** to continue.
3. Read the numbered report and inspect both previews. `review` always stays kept.

The model dropdown selects the registered vision model
`huihui_ai-Qwen3.8-27B-abliterated-Ollama`. SmartLLM uses **Direct Chat**, an editable
system prompt, seed `42`, sampling disabled, an 8192-token context and up to 1024
output tokens. Additional tasks and few-shot examples are disabled. The system
prompt requests one short JSON object per image and checks visible extra heads,
duplicate bodies, extra/fused/disconnected limbs and malformed hands or feet.
Cropping, occlusion, unusual poses and unseen anatomy are not defects by themselves.

The loader uses `resize_mode = list`. **Image Selector still resizes selected
images to the first selected image's dimensions.** Bypass it when mixed input
dimensions must be preserved through the workflow. The filter preserves the
images it receives. The workflow contains no image-saving, deletion or regeneration
nodes. Model judgments can be wrong; this workflow is a way to inspect those
judgments, not a guarantee of anatomical correctness.

Restart ComfyUI after installing the new node. SmartLLM and its registered Ollama
vision model must be available separately; Eclipse does not manage that runtime.
