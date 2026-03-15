---
name: feynrules-validator
description: Validate FeynRules .fr model files for physical consistency using Mathematica via Magnus cloud. Triggers when the user wants to check a .fr model file for Hermiticity, diagonal mass/quadratic terms, and kinetic term normalization.
---

# FeynRules Validator

## Overview

This skill validates FeynRules `.fr` model files for physical consistency by running four standard checks in both Feynman and Unitary gauge (8 checks total) using remote Mathematica execution via the Magnus cloud platform.

Validation is a critical step between writing a `.fr` model and generating a UFO model — it catches errors in the Lagrangian before they propagate to event generation.

## Workflow

### Step 1: Prepare the Model

Ensure the `.fr` file is complete and saved to disk. Know the exact Lagrangian symbol name (e.g., `LSnew`, `Lag`, `LBSM`).

### Step 2: Run Validation

Execute the `validate-feynrules` blueprint using `magnus run` (see magnus skill):

```bash
magnus run validate-feynrules -- --model path/to/model.fr --lagrangian LSnew
```

**Parameters**:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--model` | Yes | Path to the `.fr` model file |
| `--lagrangian` | Yes | Exact Lagrangian variable name defined in the `.fr` file |

The `.fr` file is automatically uploaded via the FileSecret mechanism.

### Step 3: Interpret Results

The blueprint returns a JSON result (check with `magnus job result <job-id>`) containing:

- **`success`** (bool): `True` iff all 4 Unitary gauge checks pass. Feynman gauge results are informational only.
- **`verdict`** (str): Intelligent human-readable summary explaining the outcome — covers Goldstone mixing, field mixing, etc.
- **`feynman_gauge`** / **`unitary_gauge`**: Per-gauge results with 4 checks each:
  - `hermiticity`
  - `diagonal_quadratic_terms`
  - `diagonal_mass_terms`
  - `kinetic_term_normalisation`
- **`model_loading`**: Whether the model loaded successfully

### Step 4: Fix and Re-validate

If validation fails:
1. Read the `verdict` field for guidance on what to fix
2. Edit the `.fr` file to correct the issues
3. Re-run validation

## Model Type Auto-Detection

The validator auto-detects whether a model is:
- **Standalone**: Contains `M$GaugeGroups` — loaded directly
- **BSM extension**: No `M$GaugeGroups` — the built-in SM.fr is loaded first automatically

## Success Criteria

- **Success = True** when all 4 Unitary gauge checks pass
- Feynman gauge failures are **expected** for models with spontaneous symmetry breaking (Goldstone boson artifacts)
- See [references/validation_checks.md](references/validation_checks.md) for detailed check descriptions and pass criteria

## Reference Documentation

- See [references/validation_checks.md](references/validation_checks.md) for detailed explanation of each check, pass criteria, and gauge-specific behavior
