---
name: ufo-generator
description: Generate UFO (Universal FeynRules Output) model directories from FeynRules .fr files via Magnus cloud. Triggers when the user has a validated .fr model and needs a UFO model for MadGraph5.
---

# UFO Generator

## Overview

This skill generates UFO (Universal FeynRules Output) model directories from FeynRules `.fr` files using remote Mathematica execution via the Magnus cloud platform. UFO models are the standard interchange format between model-building tools (FeynRules) and Monte Carlo event generators (MadGraph5, Herwig, Sherpa).

## Workflow

### Step 1: Prepare the .fr File

Ensure the `.fr` file is validated (via feynrules-validator skill) and saved to disk. Know the Lagrangian symbol name (e.g., `LSnew`, `LBSM`).

### Step 2: Generate the UFO Model

Execute the `generate-ufo` blueprint using `magnus run` (see magnus skill):

```bash
magnus run generate-ufo -- --model path/to/model.fr --lagrangian LSnew --output path/to/MyModel_UFO
```

**Parameters**:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--model` | Yes | Path to the `.fr` model file |
| `--lagrangian` | Yes | Lagrangian symbol from the `.fr` file |
| `--output` | Yes | Output path for the generated UFO directory |
| `--restriction` | No | Path to `.rst` restriction file |

The `.fr` file (and `.rst` if provided) is automatically uploaded via the FileSecret mechanism. On success, the UFO directory is automatically downloaded to `--output`.

**WARNING**: If `--output` points to an existing directory, it will be **deleted and replaced** by the download.

Model type is auto-detected:
- With `M$GaugeGroups`: loaded directly as standalone model
- Without `M$GaugeGroups`: BSM extension — SM.fr and SM restrictions (Massless.rst, DiagonalCKM.rst) are loaded automatically

### Step 3: Read UFO Output Files

After successful generation, you **must** read the UFO files to understand the model before using it with MadGraph5:

1. **`particles.py`** — particle definitions with MG5 names and PDG codes
   - Extract `name` field: the particle name to use in MG5 `generate` commands
   - Extract `pdg_code`: needed for `set param_card MASS <pdg> <value>`

2. **`parameters.py`** — parameter definitions with SLHA block info
   - Extract `lhablock` + `lhacode`: needed for `set param_card <block> <code> <value>`
   - Extract `value`: default parameter values

This step is critical — particle names in MG5 come from the `name` field in `particles.py`, not from the ClassName in the `.fr` file.

### Step 4: Verify UFO integrity

Check the result JSON (`magnus job result <job-id>`):
- `success` (bool): Whether generation succeeded
- `ufo_path` (str): Path to the downloaded UFO directory
- Any warnings about the generation process

If warnings are present, review the UFO files carefully before proceeding to MadGraph5.

## Reference Documentation

- See [references/ufo_format.md](references/ufo_format.md) for detailed UFO directory structure, how to read particles.py and parameters.py, and mapping to MadGraph5 `set param_card` commands
