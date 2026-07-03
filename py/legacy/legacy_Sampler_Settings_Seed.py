#
# Seed functionality adapted from rgthree

import random
from datetime import datetime
from ...core import CATEGORY, SAMPLERS_COMFY, SCHEDULERS_ANY
from ...core.logger import log
from typing import Any
from comfy_api.latest import io #type: ignore

_LOG_PREFIX = "Sampler"
# Some extension must be setting a seed as server-generated seeds were not random. We'll set a new
# seed and use that state going forward.
initial_random_state = random.getstate()
random.seed(datetime.now().timestamp())
eclipse_seed_random_state = random.getstate()
random.setstate(initial_random_state)


def new_random_seed():
    # Gets a new random seed from the eclipse_seed_random_state and resetting the previous state.
    global eclipse_seed_random_state
    prev_random_state = random.getstate()
    random.setstate(eclipse_seed_random_state)
    seed = random.randint(0, 2**64 - 1)
    eclipse_seed_random_state = random.getstate()
    random.setstate(prev_random_state)
    return seed

class RvSettings_Sampler_Settings_Seed(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="Sampler Settings+Seed [Eclipse]",
            display_name="⚠ Sampler Settings+Seed",
            category=CATEGORY.MAIN.value + CATEGORY.DEPRECATED.value,
            is_deprecated=True,
            description="DEPRECATED — replace with the current equivalent node. All legacy nodes will be removed in v4.0.0.",
            inputs=[
                io.Boolean.Input("allow_overwrite", default=True, label_on="yes", label_off="no", tooltip="When enabled, allows direct inputs to IO nodes to overwrite this node's values."),
                io.Combo.Input("sampler_name", options=SAMPLERS_COMFY, tooltip="Select the sampler algorithm."),
                io.Combo.Input("scheduler", options=SCHEDULERS_ANY, tooltip="Select the scheduler algorithm."),
                io.Int.Input("steps", default=20, min=1, step=1, tooltip="Number of sampling steps."),
                io.Float.Input("cfg", default=3.50, min=1.0, step=0.1, tooltip="Classifier-Free Guidance scale."),
                io.Float.Input("guidance", default=3.50, min=0, step=0.1, tooltip="Flux guidance scale."),
                io.Float.Input("denoise", default=1.0, min=0, max=1.0, step=0.1, tooltip="Denoise strength (0-1)."),
                io.Int.Input("seed", default=0, min=-3, max=2**64 - 1, tooltip="Random seed for generation. Use -1 for random, -2 to increment, -3 to decrement."),
            ],
            outputs=[
                io.Custom("PIPE").Output("pipe"),
            ],
            hidden=[io.Hidden.prompt, io.Hidden.extra_pnginfo, io.Hidden.unique_id],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs) -> Any:
        # Forces a changed state if we happen to get a special seed, as if from the API directly.
        seed = kwargs.get("seed", 0)
        if seed in (-1, -2, -3):
            return new_random_seed()
        return seed

    @classmethod
    def execute(cls, allow_overwrite: bool, sampler_name: Any, scheduler: Any, steps: int, cfg: float,
                guidance: float, denoise: float, seed: int = 0) -> io.NodeOutput:
        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        unique_id = cls.hidden.unique_id

        # We generate random seeds on the frontend in the seed node before sending the workflow in for
        # many reasons. However, if we want to use this in an API call without changing the seed before
        # sending, then users _could_ pass in "-1" and get a random seed used and added to the metadata.
        # Though, this should likely be discouraged for several reasons (thus, a lot of logging).
        if seed in (-1, -2, -3):
            log.warning(_LOG_PREFIX, f'Got "{seed}" as passed seed. ' +
                  'This shouldn\'t happen when queueing from the ComfyUI frontend.')
            if seed in (-2, -3):
                log.warning(_LOG_PREFIX, f'Cannot {"increment" if seed == -2 else "decrement"} seed from ' +
                     'server, but will generate a new random seed.')

            original_seed = seed
            seed = new_random_seed()
            log.msg(_LOG_PREFIX, f'Server-generated random seed {seed} and saving to workflow.')
            log.warning(_LOG_PREFIX, f'NOTE: Re-queues passing in "{seed}" and server-generated random seed won\'t be cached.')

            if unique_id is None:
                log.warning(_LOG_PREFIX, 'Cannot save server-generated seed to image metadata because ' +
                     'the node\'s id was not provided.')
            else:
                if extra_pnginfo is None:
                    log.warning(_LOG_PREFIX, 'Cannot save server-generated seed to image workflow ' +
                         'metadata because workflow was not provided.')
                else:
                    workflow_node = next(
                        (x for x in extra_pnginfo['workflow']['nodes'] if str(x['id']) == str(unique_id)), None)
                    if workflow_node is None or 'widgets_values' not in workflow_node:
                        log.warning(_LOG_PREFIX, 'Cannot save server-generated seed to image workflow ' +
                             'metadata because node was not found in the provided workflow.')
                    else:
                        for index, widget_value in enumerate(workflow_node['widgets_values']):
                            if widget_value == original_seed:
                                workflow_node['widgets_values'][index] = seed

                if prompt is None:
                    log.warning(_LOG_PREFIX, 'Cannot save server-generated seed to image API prompt ' +
                         'metadata because prompt was not provided.')
                else:
                    prompt_node = prompt[str(unique_id)]
                    if prompt_node is None or 'inputs' not in prompt_node or 'seed' not in prompt_node['inputs']:
                        log.warning(_LOG_PREFIX, 'Cannot save server-generated seed to image API prompt ' +
                             'metadata because node was not found in the provided prompt.')
                    else:
                        prompt_node['inputs']['seed'] = seed

        pipe = {
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "steps": int(steps),
            "cfg": float(cfg),
            "guidance": float(guidance),
            "denoise": float(denoise),
            "seed": int(seed),
            "_allow_overwrite": allow_overwrite,  # Flag for IO nodes
        }
        return io.NodeOutput(pipe)
