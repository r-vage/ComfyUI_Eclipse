# ReadPromptFiles Node - User Guide

## Overview
The `ReadPromptFiles` node loads prompts from multiple text files and lets you navigate through them using different selection modes.

## Features
- **Multiple file support**: Load prompts from multiple text files
- **Navigation modes**: Random, increment, decrement, or fixed index selection
- **File watching**: Automatically detects when files are updated
- **Path flexibility**: Supports quoted paths for files with spaces

## Input Parameters

### file_paths (Text Area)
List of file paths to your prompt files, one per line:
```
C:\prompts\nature.txt
"C:\prompts\with spaces\fantasy.txt"
D:\prompts\sci-fi.txt
```

### index (Number)
- **0, 1, 2...**: Select specific prompt by line number
- **-1**: Random selection each time  
- **-2**: Auto-increment (next prompt each run)
- **-3**: Auto-decrement (previous prompt each run)
- **-4**: Shuffle (random without repeat until all prompts seen)

### log_prompt (Checkbox)
When enabled, prints the selected prompt to the console for debugging.

### stop_at_end (Checkbox, default: True)
Stops workflow execution when increment reaches the end or decrement reaches the start. Prevents infinite looping in batch mode.

### seed_input (Number, Optional, Force Input)
When connected, special navigation modes (-1, -2, -3, -4) only advance when the seed value changes. Useful for syncing prompt iteration with seed iteration.

## Navigation Buttons

### 🎲 Randomize Each Time
Sets the index to random mode (-1). Each time you run the workflow, a different random prompt will be selected.

### 🎲 New Fixed Random  
Generates a random prompt index and sets it as a fixed value. The same prompt will be used until you change it.

### ♻️ (Use Last Queued Index)
When using navigation modes, this button shows the actual index that was last used and lets you reuse that specific prompt.

## Example Usage

### 1. Create Your Prompt Files

**nature_prompts.txt:**
```
a beautiful sunset over mountains
a serene forest with morning mist  
a crystal clear lake reflecting clouds
ancient redwood trees in fog
wildflowers in a meadow
```

**fantasy_prompts.txt:**
```
a magical castle floating in clouds
a dragon soaring above ruins
an enchanted forest with glowing mushrooms
a wizard's tower under starlight
crystal caves with mystical light
```

### 2. Configure the Node

**File Paths:**
```
C:\prompts\nature_prompts.txt
C:\prompts\fantasy_prompts.txt  
```

**Index Options:**
- `0` = First prompt ("a beautiful sunset over mountains")
- `5` = Sixth prompt ("a magical castle floating in clouds") 
- `-1` = Random prompt each time
- `-2` = Next prompt each time (0→1→2→3...)
- `-3` = Previous prompt each time (...3→2→1→0)
- `-4` = Shuffle (random order, no repeats until all seen)

### 3. Workflow Examples

**Random Generation:**
Set index to `-1` and each run will use a different random prompt from your combined files.

**Sequential Processing:**  
Set index to `-2` to automatically cycle through all prompts one by one for batch processing.

**Specific Prompt:**
Set index to a number like `3` to always use the 4th prompt in your combined list.

## Tips

1. **File Organization**: Keep different themes in separate files (nature.txt, fantasy.txt, portraits.txt)

2. **Path Spaces**: Use quotes around paths with spaces: `"C:\My Prompts\fantasy.txt"`

3. **Prompt Counting**: All non-empty lines across all files are combined into one numbered list

4. **File Updates**: The node automatically detects when you edit your prompt files

5. **Navigation**: Use the buttons for quick switching between random and fixed modes

## Common Workflows

### Batch Generation
- Set index to `-2` (increment)
- Run multiple times to generate images with different prompts sequentially

### Inspiration Mode  
- Set index to `-1` (random)
- Generate variations with randomly selected prompts

### Targeted Creation
- Set index to specific number 
- Fine-tune generation with the exact prompt you want