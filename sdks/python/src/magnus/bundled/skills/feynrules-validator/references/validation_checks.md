# Validation Checks Reference

This document explains the four checks performed by `validate_feynrules()` and their pass criteria.

## Overview

The validator runs 4 checks in both Feynman and Unitary gauge (8 checks total). Success is determined by the **Unitary gauge** results only — Feynman gauge is informational.

## Check 1: Hermiticity (`CheckHermiticity`)

**What it checks**: Whether the Lagrangian L satisfies L = L†. A Hermitian Lagrangian ensures unitarity of the S-matrix.

**Pass criteria**:
- `True` (TrueQ) — Lagrangian is Hermitian
- Empty list `{}` — No non-Hermitian terms found
- `0` — Equivalent to Hermitian

**Failure modes**:
- Returns a list of non-Hermitian terms that need to be fixed
- Common fix: Add proper `HC[]` terms or fix index contractions

**Result fields**:
- `passed` (bool)
- `is_hermitian` (bool)
- `non_hermitian_terms` (list, if not Hermitian)

## Check 2: Diagonal Quadratic Terms (`CheckDiagonalQuadraticTerms`)

**What it checks**: Whether the quadratic terms (mass-like terms proportional to field²) in the Lagrangian are diagonal in field space. Non-diagonal quadratic terms indicate field mixing that should be resolved.

**Pass criteria**:
- `True` (TrueQ) — All quadratic terms are diagonal
- Empty list `{}` — No off-diagonal terms
- `0` — Equivalent to diagonal
- `Null` — Check not applicable (e.g., no quadratic terms)

**Failure modes**:
- Returns off-diagonal quadratic terms
- In **Feynman gauge**: Goldstone-gauge boson mixing is expected and benign
- In **Unitary gauge**: Indicates a real problem in the model

**Result fields**:
- `passed` (bool)
- `warning` (str, optional — explains Goldstone artifacts in Feynman gauge)

## Check 3: Diagonal Mass Terms (`CheckDiagonalMassTerms`)

**What it checks**: Whether the mass matrix is diagonal after field redefinitions. Similar to Check 2 but specifically for the mass matrix structure.

**Pass criteria**:
- `True` (TrueQ) — Mass terms are diagonal
- Empty list `{}` — No off-diagonal mass terms
- `0` — Equivalent to diagonal
- `Null` — Check not applicable

**Failure modes**:
- Returns off-diagonal mass terms
- In **Feynman gauge**: Goldstone boson contributions are expected
- In **Unitary gauge**: Indicates mass matrix diagonalization issues

**Result fields**:
- `passed` (bool)

## Check 4: Kinetic Term Normalisation (`CheckKineticTermNormalisation`)

**What it checks**: Whether kinetic terms are properly normalized (coefficient = 1 for canonical normalization). Improperly normalized kinetic terms lead to incorrect propagators.

**Pass criteria**:
- Anything **except** `False`, `$Failed`, or `$Aborted` passes
- `True` — Properly normalized
- A list of normalization factors — Acceptable (FeynRules can handle rescaling)
- `Null` — Check not applicable

**Failure modes**:
- `False` — Kinetic terms have incorrect normalization
- `$Failed` — Check could not be completed (syntax error in model)
- `$Aborted` — Check timed out

**Result fields**:
- `passed` (bool)
- `warning` (str, optional)

## Gauge-Specific Behavior

### Unitary Gauge (determines success)
- Goldstone bosons are absent
- All 4 checks should pass for a correct model
- This is the physically meaningful gauge for validation

### Feynman Gauge (informational)
- Goldstone bosons are present as explicit degrees of freedom
- Check 2 (diagonal quadratic terms) commonly fails due to Goldstone-gauge boson mixing — this is expected and benign
- Check 3 (diagonal mass terms) may also show Goldstone-related entries
- Feynman gauge failures do NOT indicate model errors when Unitary gauge passes

## Interpreting the Verdict

The `verdict` field provides an intelligent summary:

- **"Model passes all checks"** — Everything is correct
- **"Feynman gauge shows Goldstone mixing..."** — Expected SSB artifacts, model is fine
- **"Hermiticity violation found..."** — Fix HC[] terms in the Lagrangian
- **"Off-diagonal mass terms in Unitary gauge..."** — Mass matrix diagonalization error
- **"Kinetic term normalization failed..."** — Check field normalizations

## Examples

### Successful BSM Extension
```
Unitary gauge: 4/4 checks passed
Feynman gauge: 2/4 checks passed (Goldstone mixing expected)
Success: True
Verdict: "Model is physically consistent. Feynman gauge shows expected
          Goldstone-gauge boson mixing from spontaneous symmetry breaking."
```

### Failed — Non-Hermitian Lagrangian
```
Unitary gauge: 3/4 checks passed (Hermiticity failed)
Success: False
Verdict: "Hermiticity check failed. Non-Hermitian terms found: [list].
          Ensure HC[] is applied correctly."
```
