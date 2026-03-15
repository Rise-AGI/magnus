---
name: feynrules-model-generator
description: Generate FeynRules .fr model files from LaTeX Lagrangian descriptions. Triggers when the user provides a Lagrangian in LaTeX notation and wants it converted to a FeynRules model file for particle physics simulations.
---

# FeynRules Model Generator

## Overview

This skill converts LaTeX Lagrangian descriptions into complete, validated FeynRules `.fr` model files. These files define Beyond-the-Standard-Model (BSM) particle physics models that extend the Standard Model (SM) with new particles, couplings, and interactions.

The generated `.fr` files are used downstream for validation (feynrules-validator skill) and UFO generation (ufo-generator skill).

This is a **pure LLM generation task** — no remote execution or Magnus blueprints are involved.

## Workflow

Work through these steps **one at a time**. Do not try to write the complete `.fr` file in one shot — copy the skeleton first, then edit each section incrementally.

### Step 1: Analyze the Lagrangian

- Identify all new fields (scalars, fermions, vectors) and their quantum numbers
- Identify all coupling constants and their chirality structure (left/right projectors)
- Determine if "+ h.c." is present (affects Hermitian conjugate handling)
- Map physics notation to FeynRules symbol conventions

### Step 2: Map Symbols to FeynRules Conventions

- Use the SM symbol conventions built into FeynRules (see reference doc):
  - `ee` = electromagnetic coupling, `gw` = weak SU(2)_L coupling, `gs` = strong SU(3)_C coupling
  - `g1` = hypercharge U(1)_Y coupling, `sw`/`cw` = sine/cosine of Weinberg angle
- Assign PDG codes > 9000000 for generic BSM particles; use established PDG reservations where they exist (e.g. 5000039 for KK graviton)
- Define new coupling parameters with `InteractionOrder -> {NP, 1}`

### Step 3: Create the .fr file from the skeleton

Copy [templates/skeleton.fr](templates/skeleton.fr) into the workspace as your starting `.fr` file. Fill in `M$ModelName` and `M$Information` with the model name and metadata. Add any needed index definitions.

### Step 4: Define particle classes

Edit the `M$ClassesDescription` section. For each BSM particle, define the appropriate class (F[], S[], V[], T[]) with all required attributes. See [references/feynrules_syntax.md](references/feynrules_syntax.md) section 5 for the full attribute list for each particle type.

### Step 5: Define parameters

Edit the `M$Parameters` section. For each new coupling constant:
- Set `ParameterType -> External` with `BlockName`, `OrderBlock`, `Value`
- Include `InteractionOrder -> {NP, 1}` for every new physics coupling (both External and Internal)
- Do **NOT** add Mass or Width symbols here — they are already defined by the particle class

For derived parameters, use `ParameterType -> Internal` with a `Value` expression.

See [references/feynrules_syntax.md](references/feynrules_syntax.md) section 6 for External/Internal parameter examples, including mixing matrices with `Unitary -> True`.

### Step 6: Write the Lagrangian

Edit the Lagrangian section. Key rules:
- Use `Block[{indices}, ...]` to declare dummy indices
- Use explicit `*` for all multiplication
- If the Lagrangian has "+ h.c.", use the Ltmp pattern:
  ```
  LNPtmp := Block[{...}, <non-hermitian part only>];
  LNP := LNPtmp + HC[LNPtmp];
  ```
- If there is NO "+ h.c.", write the Lagrangian directly (do NOT add HC)

See [references/feynrules_syntax.md](references/feynrules_syntax.md) sections 7-8 for Lagrangian syntax and a complete BSM example.

### Step 7: Validate

After writing the `.fr` file, use the `validate-feynrules` Magnus blueprint (see feynrules-validator skill) to validate the model:
```
magnus run validate-feynrules -- --model <path-to-fr-file> --lagrangian <lagrangian-symbol>
```

Additionally, manually verify:
- Every spinor index appears exactly twice per monomial (proper contraction)
- Every monomial has zero total electric charge
- HC[] is only used when the Lagrangian explicitly contains "+ h.c."

## Key Conventions

- **Do NOT generate the SM part** — only the BSM extension
- **Use explicit multiplication** (`*`) instead of implicit juxtaposition
- **Fermion bilinears**: `psibar[sp1].Ga[mu,sp1,sp2].psi[sp2]` or dot notation `psibar.Ga[mu].psi`
- **Projectors**: `ProjM` = (1-gamma5)/2 (left), `ProjP` = (1+gamma5)/2 (right)
- **HC[] usage**: Define non-Hermitian part as `Ltmp`, then `L := Ltmp + HC[Ltmp]`
- **Non-selfconjugate fields**: `X` = positive charge, `Xbar` = negative charge (canonical convention)
- **(L -> R) substitution**: Replace ALL left-handed objects simultaneously — projectors, fields, AND coupling parameters
- **Width of BSM particles**: If the particle is expected to decay, define Width as an **external parameter** (e.g. `Width -> {WSnew, 0.04}`) or use `Width -> {WSnew, Internal}`. Do NOT use `Width -> 0` — this tells MG5 the particle is stable, and MG5 will silently override any `set param_card DECAY` attempt to match the model's zero-width expression

## Reference Documentation

- See [references/feynrules_syntax.md](references/feynrules_syntax.md) for the complete FeynRules `.fr` file syntax specification

## Templates

- [templates/skeleton.fr](templates/skeleton.fr) — Copyable starting point for a BSM extension `.fr` file with all required sections and TODO markers
