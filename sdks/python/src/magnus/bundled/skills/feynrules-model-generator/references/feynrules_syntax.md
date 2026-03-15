# FeynRules Technical Reference

This document contains detailed technical specifications for generating FeynRules `.fr` model files.

## Role
- You are a particle physics expert, proficient in FeynRules. 

## Complete .fr File Structure

A complete FeynRules model file must contain the following sections in order:

### 1. Model Information

```mathematica
M$ModelName = "Model Name";

M$Information = {
  Authors      -> {"Author1", "Author2"},
  Version      -> "1.0",
  Date         -> "DD. MM. YYYY",
  Institutions -> {"Institution1", "Institution2"},
  Emails       -> {"email1@example.com", "email2@example.com"},
  URLs         -> "http://example.com/model"
};
```

### 2. Gauge Groups (if applicable)

```mathematica
M$GaugeGroups = {
  U1Y  == {
    Abelian          -> True,
    CouplingConstant -> g1,
    GaugeBoson       -> B,
    Charge           -> Y
  },
  SU2L == {
    Abelian           -> False,
    CouplingConstant  -> gw,
    GaugeBoson        -> Wi,
    StructureConstant -> Eps,
    Representations   -> {Ta, SU2D},
    Definitions       -> {Ta[a_,b_,c_] -> PauliSigma[a,b,c]/2, FSU2L[i_,j_,k_] :> I Eps[i,j,k]}
  },
  SU3C == {
    Abelian           -> False,
    CouplingConstant  -> gs,
    GaugeBoson        -> G,
    StructureConstant -> f,
    Representations   -> {T, Colour},
    SymmetricTensor   -> dSUN
  }
};
```

### 3. Index Definitions

```mathematica
(* Define index ranges *)
IndexRange[Index[SU2W      ]] = Unfold[Range[3]];
IndexRange[Index[SU2D      ]] = Unfold[Range[2]];
IndexRange[Index[Gluon     ]] = NoUnfold[Range[8]];
IndexRange[Index[Colour    ]] = NoUnfold[Range[3]];
IndexRange[Index[Generation]] = Range[3];
IndexRange[Index[Flavor    ]] = Range[3];  (* For custom flavor indices *)

(* Define index styles for display *)
IndexStyle[SU2W,       j];
IndexStyle[SU2D,       k];
IndexStyle[Gluon,      a];
IndexStyle[Colour,     m];
IndexStyle[Generation, f];
IndexStyle[Flavor,     i];
```

**Standard Index Conventions:**
- `sp`, `sp1`, `sp2`: Spinor indices (for fermion bilinears)
- `ii`, `jj`: Flavor/generation indices
- `cc`: Color indices (SU(3) fundamental)
- `aa`: Gluon/adjoint color indices
- `mu`, `nu`: Lorentz vector indices

### 4. Interaction Order Hierarchy (for MadGraph)

```mathematica
M$InteractionOrderHierarchy = {
  {QCD, 1},
  {QED, 2},
  {NP, 2}  (* New Physics, if applicable *)
};
```

### 5. Particle Classes (M$ClassesDescription)

#### Vector Bosons V[n]

```mathematica
V[1] == {
  ClassName       -> A,
  SelfConjugate   -> True,
  Mass            -> 0,
  Width           -> 0,
  ParticleName    -> "a",
  PDG             -> 22,
  PropagatorLabel -> "a",
  PropagatorType  -> W,
  PropagatorArrow -> None,
  FullName        -> "Photon"
},

(* Massive vector with internal mass *)
V[3] == {
  ClassName        -> W,
  SelfConjugate    -> False,
  Mass             -> {MW, Internal},
  Width            -> {WW, 2.085},
  ParticleName     -> "W+",
  AntiParticleName -> "W-",
  QuantumNumbers   -> {Q -> 1},
  PDG              -> 24,
  PropagatorLabel  -> "W",
  PropagatorType   -> Sine,
  PropagatorArrow  -> Forward,
  FullName         -> "W"
}
```

#### Fermions F[n]

```mathematica
(* Massless fermion with generation index *)
F[1] == {
  ClassName        -> vl,
  ClassMembers     -> {ve, vm, vt},
  Indices          -> {Index[Generation]},
  FlavorIndex      -> Generation,
  SelfConjugate    -> False,
  Mass             -> 0,
  Width            -> 0,
  QuantumNumbers   -> {LeptonNumber -> 1},
  PropagatorLabel  -> {"v", "ve", "vm", "vt"},
  PropagatorType   -> S,
  PropagatorArrow  -> Forward,
  PDG              -> {12, 14, 16},
  ParticleName     -> {"ve", "vm", "vt"},
  AntiParticleName -> {"ve~", "vm~", "vt~"},
  FullName         -> {"Electron-neutrino", "Mu-neutrino", "Tau-neutrino"}
},

(* Massive fermion with multiple indices *)
F[3] == {
  ClassName        -> uq,
  ClassMembers     -> {u, c, t},
  Indices          -> {Index[Generation], Index[Colour]},
  FlavorIndex      -> Generation,
  SelfConjugate    -> False,
  Mass             -> {Mu, {MU, 2.55*^-3}, {MC, 1.27}, {MT, 172}},
  Width            -> {0, 0, {WT, 1.50833649}},
  QuantumNumbers   -> {Q -> 2/3},
  PropagatorLabel  -> {"uq", "u", "c", "t"},
  PropagatorType   -> Straight,
  PropagatorArrow  -> Forward,
  PDG              -> {2, 4, 6},
  ParticleName     -> {"u", "c", "t"},
  AntiParticleName -> {"u~", "c~", "t~"},
  FullName         -> {"u-quark", "c-quark", "t-quark"}
}
```

#### Scalars S[n]

```mathematica
(* Real scalar (self-conjugate) *)
S[1] == {
  ClassName       -> H,
  SelfConjugate   -> True,
  Mass            -> {MH, 125},
  Width           -> {WH, 0.00407},
  PropagatorLabel -> "H",
  PropagatorType  -> D,
  PropagatorArrow -> None,
  PDG             -> 25,
  ParticleName    -> "H",
  FullName        -> "H"
},

(* Complex scalar *)
S[3] == {
  ClassName        -> GP,
  SelfConjugate    -> False,
  Mass             -> {MW, Internal},
  QuantumNumbers   -> {Q -> 1},
  Width            -> {WW, 2.085},
  PropagatorLabel  -> "GP",
  PropagatorType   -> D,
  PropagatorArrow  -> None,
  PDG              -> 251,
  ParticleName     -> "G+",
  AntiParticleName -> "G-",
  FullName         -> "GP"
}
```

#### Tensor Bosons (Spin-2) T[n]

Spin-2 particles (e.g. graviton, KK graviton) are rank-2 symmetric tensors with two Lorentz indices. Use the class `T[n]`; the field in the Lagrangian is written with two indices, e.g. `G[mu, nu]`.

```mathematica
(* Massive spin-2 particle, e.g. first KK graviton mode *)
T[1] == {
  ClassName       -> G,
  SelfConjugate   -> True,
  Mass            -> {MG, 600},
  Width           -> {WG, 10},
  PDG             -> 5000039,
  ParticleName    -> "G",
  PropagatorLabel -> "G",
  PropagatorType  -> U,
  PropagatorArrow -> None,
  FullName        -> "Graviton"
}
```

- **PropagatorType**: For massive spin-2, `U` is commonly used (spin-2 unitary propagator). Model-specific implementations may use other types.
- **Lagrangian**: The spin-2 field appears as `G[mu, nu]` (symmetric in μ, ν). Typical coupling is to the energy-momentum tensor, e.g. \(-\frac{1}{\Lambda} T^{\mu\nu} h_{\mu\nu}\), which involves derivatives of matter fields (see **Partial derivatives** below).

#### Unphysical Fields

Unphysical fields are gauge eigenstates that get rotated to mass eigenstates:

```mathematica
F[11] == {
  ClassName      -> LL,
  Unphysical     -> True,
  Indices        -> {Index[SU2D], Index[Generation]},
  FlavorIndex    -> SU2D,
  SelfConjugate  -> False,
  QuantumNumbers -> {Y -> -1/2},
  Definitions    -> {
    LL[sp1_, 1, ff_] :> Module[{sp2}, ProjM[sp1, sp2] vl[sp2, ff]],
    LL[sp1_, 2, ff_] :> Module[{sp2}, ProjM[sp1, sp2] l[sp2, ff]]
  }
}
```

### 6. Parameters (M$Parameters)

#### External Parameters

Every **new physics coupling** parameter (External) must include the `InteractionOrder` attribute, e.g. `InteractionOrder -> {NP, 1}`. Mass/width and mixing parameters are not couplings (see below).

```mathematica
M$Parameters = {
  (* External parameter with SLHA block *)
  aEWM1 == {
    ParameterType    -> External,
    BlockName        -> SMINPUTS,
    OrderBlock       -> 1,
    Value            -> 127.9,
    InteractionOrder -> {QED, -2},
    Description      -> "Inverse of the EW coupling constant at the Z pole"
  },

  (* Mass parameter *)
  MZ == {
    ParameterType -> External,
    BlockName     -> MASS,
    OrderBlock    -> 23,
    Value         -> 91.1876,
    TeX           -> Subscript[M, Z],
    Description   -> "Z boson mass"
  },

  (* Coupling constant for new physics *)
  gNP == {
    ParameterType    -> External,
    BlockName        -> NPINPUTS,
    OrderBlock       -> 1,
    Value            -> 0.1,
    InteractionOrder -> {NP, 1},
    TeX              -> Subscript[g, NP],
    Description      -> "New physics coupling"
  }
};
```

#### Internal Parameters

Internal parameters that are **new physics couplings** (e.g. derived from or equal to an External coupling, or matrix elements of a coupling matrix used in the Lagrangian) **must** also include `InteractionOrder`, e.g. `InteractionOrder -> {NP, 1}`. **Do not** set `InteractionOrder` for **mixing** parameters (e.g. mixing angles, rotation angles, elements of unitary mixing matrices that diagonalize mass matrices).

```mathematica
(* mixing angles/parameters — not a coupling, no InteractionOrder *)
sw == {
  ParameterType -> Internal,
  Value         -> Sqrt[sw2],
  TeX           -> Subscript[s, w],
  Description   -> "Sine of the Weinberg angle"
},

(* Coupling matrix — has InteractionOrder *)
yl == {
  ParameterType    -> Internal,
  Indices          -> {Index[Generation], Index[Generation]},
  Definitions      -> {yl[i_?NumericQ, j_?NumericQ] :> 0 /; (i =!= j)},
  Value            -> {yl[1,1] -> Sqrt[2] yme/vev, yl[2,2] -> Sqrt[2] ymm/vev, yl[3,3] -> Sqrt[2] ymtau/vev},
  InteractionOrder -> {QED, 1},
  TeX              -> Superscript[y, l],
  Description      -> "Lepton Yukawa couplings"
},

(* New physics Internal coupling — must have InteractionOrder *)
yR == {
  ParameterType    -> Internal,
  Value            -> yL,
  InteractionOrder -> {NP, 1},
  TeX              -> Subscript[y, R],
  Description      -> "Right-handed coupling, equal to yL"
}
```

#### Mixing Matrices

External mixing matrices use `Indices` and `Unitary -> True`:

```mathematica
(* 3x3 unitary mixing matrix — no InteractionOrder (not a coupling) *)
VNP == {
  ParameterType -> External,
  Indices       -> {Index[Generation], Index[Generation]},
  Unitary       -> True,
  Value         -> {
    VNP[1,1] -> 1.0, VNP[1,2] -> 0.0, VNP[1,3] -> 0.0,
    VNP[2,1] -> 0.0, VNP[2,2] -> 1.0, VNP[2,3] -> 0.0,
    VNP[3,1] -> 0.0, VNP[3,2] -> 0.0, VNP[3,3] -> 1.0
  },
  Description -> "New physics mixing matrix"
}
```

### 7. Lagrangian

#### Basic Lagrangian Syntax

```mathematica
(* Simple Yukawa interaction *)
LYukawa = -ySL * lbar[sp, ii].ProjM[sp, sp2].l[sp2, jj] * S + HC[...];

(* With Block for local variables — only indices in Block[{...}, expression] *)
LYukawa := Block[{sp, ii, jj},
  -ySL * lbar[sp, ii].ProjM[sp, sp2].l[sp2, jj] * S + HC[-ySL * lbar[sp, ii].ProjM[sp, sp2].l[sp2, jj] * S]
];
```

#### Common FeynRules Functions

| Function | Description | Example |
|----------|-------------|---------|
| `Ga[mu]` | Gamma matrix | `psibar.Ga[mu].psi` |
| `Ga[mu, sp1, sp2]` | Gamma with explicit spinor indices | `Ga[mu, sp1, sp2]` |
| `ProjM[sp1, sp2]` | Left projector (1-γ5)/2 | `lbar[sp1].ProjM[sp1, sp2].l[sp2]` |
| `ProjP[sp1, sp2]` | Right projector (1+γ5)/2 | `lbar[sp1].ProjP[sp1, sp2].l[sp2]` |
| `DC[field, mu]` | Covariant derivative | `DC[Phi, mu]` |
| `FS[V, mu, nu]` | Field strength tensor | `FS[B, mu, nu]` |
| `HC[expr]` | Hermitian conjugate | `HC[Lagrangian]` |
| `del[field, mu]` | Partial derivative | `del[Phi, mu]` |
| `IndexDelta[i, j]` | Kronecker delta | `IndexDelta[ii, jj]` |

#### Partial derivatives (del)

The partial derivative with respect to \(x^\mu\) is `del[expr, mu]`, where `mu` is a Lorentz index. Common uses:

```mathematica
(* Scalar kinetic term: (1/2)(∂_μ φ)(∂^μ φ) *)
LkinS := 1/2 * del[S, mu] * del[S, mu];

(* Fermion kinetic term: i ψ̄ γ^μ ∂_μ ψ *)
LkinF := I * lbar.Ga[mu].del[l, mu];

(* Derivative of a vector field (e.g. in field strength) *)
del[V[nu], mu] - del[V[mu], nu]

(* Energy-momentum tensor type coupling for spin-2: T^μν contains derivatives.
   For a Dirac fermion ψ, T^μν ∝ (i/4)[ψ̄ γ^μ ∂^ν ψ - (∂^ν ψ̄)γ^μ ψ + (μ↔ν)].
   In FeynRules, one writes the derivative on the fermion with del: *)
(* Example: one term of the symmetric tensor (ψ̄ γ^μ ∂^ν ψ) *)
term := (I/4) * psibar.Ga[mu].del[psi, nu];
```

- **Syntax**: `del[field, mu]` differentiates the **field** with respect to the Lorentz index `mu`. For fermions, use `del[psi, mu]` (and `del[psibar, mu]` for the conjugate when needed).
- **Indices**: All Lorentz indices (`mu`, `nu`, etc.) in the Lagrangian must be contracted; derivative indices are no exception.

#### Fermion Bilinear Notation

```mathematica
(* Dot notation for spinor contractions *)
psibar.psi           (* Scalar: ψ̄ψ *)
psibar.Ga[mu].psi    (* Vector: ψ̄γμψ *)
psibar.ProjM.psi     (* Left-chiral: ψ̄PLψ *)
psibar.ProjP.psi     (* Right-chiral: ψ̄PRψ *)

(* Explicit spinor index notation (alternative) *)
psibar[sp1] * ProjM[sp1, sp2] * psi[sp2]
```


### 8. Complete BSM Model Example

Here's a minimal example adding a new scalar `S` that couples to leptons:

```mathematica
(* ===== Model Information ===== *)
M$ModelName = "SM + Scalar S";

M$Information = {
  Authors      -> {"Author"},
  Version      -> "1.0",
  Date         -> "01. 01. 2024"
};

(* ===== Index Definitions ===== *)
IndexRange[Index[Generation]] = Range[3];
IndexStyle[Generation, f];

(* ===== Interaction Orders ===== *)
M$InteractionOrderHierarchy = {
  {QCD, 1},
  {QED, 2},
  {NP, 2}
};

(* ===== New Particle ===== *)
M$ClassesDescription = {
  S[100] == {
    ClassName       -> Snew,
    SelfConjugate   -> True,
    Mass            -> {MSnew, 100},
    Width           -> {WSnew, 1},
    PDG             -> 9000001,
    ParticleName    -> "Snew",
    FullName        -> "New Scalar"
  }
};

(* ===== New Parameters ===== *)
M$Parameters = {
  yS == {
    ParameterType    -> External,
    BlockName        -> NPINPUTS,
    OrderBlock       -> 1,
    Value            -> 0.1,
    InteractionOrder -> {NP, 1},
    TeX              -> Subscript[y, S],
    Description      -> "Scalar-lepton coupling"
  }
};

(* ===== Lagrangian ===== *)
LSnew := Block[{sp, sp2, ii, jj},
  -yS * lbar[sp, ii].ProjM[sp, sp2].l[sp2, jj] * Snew * IndexDelta[ii, jj] +
  HC[-yS * lbar[sp, ii].ProjM[sp, sp2].l[sp2, jj] * Snew * IndexDelta[ii, jj]]
];
```



## Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| Uncontracted indices | Free indices in Lagrangian | Ensure all indices are summed |
| Unknown field | Field not in M$ClassesDescription | Add particle definition |
| PDG collision | Duplicate PDG code | Use unique PDG > 9000000 for generic BSM; use established reservations where applicable |

## PDG Code Conventions for BSM

- Use PDG codes > 9000000 for generic new particles
- Use established PDG reservations where they exist (e.g. 5000039 for KK graviton, 5000001-5000004 for KK quarks)
- Common conventions for generic BSM:
  - 9000001-9000010: New scalars
  - 9000011-9000020: New fermions
  - 9000021-9000030: New vectors

## Requirement
- Do not generate the SM part of the Lagrangian.

- Convert according to the provided Lagrangian, and do not make any modifications to the Lagrangian.

- Use the default SM symbol convention of FeynRules for the SM particles.

- **CRITICAL RULE about SM coupling constants**: When converting a Lagrangian into FeynRules code, you must map the physics notation for SM coupling constants to the **correct** FeynRules built-in symbol names. The common mappings are:

  | Meaning | FeynRules symbol | **NOT** |
  |---|---|---|
  | electromagnetic coupling | `ee` | |
  | weak (SU(2)\_L) coupling | `gw` | `gs` |
  | strong (SU(3)\_C) coupling | `gs` | `gw` |
  | hypercharge (U(1)\_Y) coupling | `g1` | |
  | sine/cosine of Weinberg angle | `sw` / `cw` | |

  **Be especially careful** not to confuse `gw` (weak) with `gs` (strong). In the standard FeynRules SM:
  - `gw` is the SU(2)\_L gauge coupling (the \(g\) that appears in W/Z boson interactions).
  - `gs` is the SU(3)\_C gauge coupling (the \(g_s\) that appears in QCD/gluon interactions).


- **CRITICAL RULE about spin (Lorentz spinor) indices**:
  - If you write explicit spin indices (e.g. `sp`, `sp1`, `sp2`), then **within each monomial term** every spin index symbol must appear **exactly twice** (once as an upper/left index and once as a lower/right index in the sense of matrix multiplication), i.e. it is properly contracted.
  - **Never reuse** the same spin index label across different factors in a way that makes it appear 3+ times (this typically indicates an invalid contraction).
  - Prefer a clear chain like `... lbar[sp1].Ga[mu,sp1,sp2].ProjM[sp2,sp3].l[sp3] ...` (GOOD) and avoid patterns like `ProjM[sp2, sp1] * ... * l[sp1, ...]` where `sp1` gets reused improperly (BAD).
  
- **CRITICAL RULE about Hermitian conjugate (HC)**:
  - First, carefully check whether the given Lagrangian **explicitly** contains a "+ h.c." term.
  - If **YES** (the Lagrangian explicitly contains "+ h.c."):
    - You MUST first define the **non-Hermitian part only** as a temporary expression (e.g. `Ltmp` / `LWpTmp` / `LIntTmp`) that corresponds exactly to the provided `L` (without any conjugate added).
    - Then define the full Lagrangian as `L_NP := Ltmp + HC[Ltmp]`.
    - **FORBIDDEN patterns** (cause double counting / wrong structure):
      - Defining `L := ...` and then writing `L_NP := L + HC[L]`.
      - Writing `L_NP := ( ... + HC[...]) + HC[( ... + HC[...])]` or any nested `HC[HC[...]]` constructions.
      - Applying `HC[...]` to an expression that already contains the conjugate part (i.e. already Hermitian).
  - If **NO** (the Lagrangian does **NOT** contain "+ h.c."): you **MUST NOT** add `HC[...]` to the Lagrangian under any circumstances. Write the Lagrangian exactly as provided. Do **NOT** add Hermitian conjugate terms based on your own physical reasoning or to enforce Hermiticity — the given Lagrangian is already self-conjugate or is intentionally written without h.c.

- **CRITICAL RULE about non-selfconjugate charged fields (`X` vs `Xbar`)**:
  - For every non-selfconjugate charged field `X` you introduce (`SelfConjugate -> False` and `QuantumNumbers -> {Q -> qX}` with `qX != 0`), you MUST explicitly define a single canonical convention: **`X` denotes the field with charge `qX`**, and its antiparticle is represented by `Xbar`.
  - **Default writing rule**: define `X` for positive charge `qX`. In this case, the default FeynRules syntax implies `Xbar` for negative charge `-qX`.
  - **Charge-conservation sanity check (must pass for every monomial)**:
    - Compute the total electric charge of each monomial term (field charges + bilinear charges). It MUST be zero.
    - If a term contains `X` with charge `qX`, then the rest of the term must carry charge `-qX`.
    - If you ever write `Xbar` explicitly, you must justify it by an explicitly provided anti-field term; otherwise it is forbidden.
  - **Hard fail conditions**:
    - Any monomial with non-zero total charge.
    - Mixing conventions (sometimes `X` means `qX`, sometimes means `-qX`).
- **CRITICAL RULE — Particle mass and width must NOT appear in M$Parameters**:
  - In `M$ClassesDescription`, each particle already specifies `Mass -> {M symbol, default value}` and `Width -> {W symbol, default value}`. Those symbols (e.g. MS, WS, MX, WX) are **already defined by the particle block**.
  - You **MUST NOT** add any symbol to `M$Parameters` that is used as the **first element** of `Mass` or `Width` in any particle in `M$ClassesDescription`. In other words: **no mass or width parameter entries in M$Parameters** for particles you define. Omitting them is correct; adding them is redundant and forbidden.
  - Before writing `M$Parameters`, check: for every particle you defined with `Mass -> {MXX, ...}` and `Width -> {WYY, ...}`, ensure **MXX and WYY do not appear** in the `M$Parameters` list.

- \((L \to R)\) means: add the analogous term obtained by replacing **every** left-handed (L) object with its right-handed (R) counterpart. **All** of the following substitutions must be applied **simultaneously**:
    1. **Projection operators**: \(P_L \to P_R\).
    2. **Chiral fields**: every field carrying an L subscript is replaced by its R counterpart (e.g. \(\psi_L \to \psi_R\)), and likewise for conjugate fields.
    3. **Coupling parameters / mixing angles**: every parameter whose subscript or label denotes "left-handed" must be replaced by the corresponding independent "right-handed" parameter (e.g. \(s_L \to s_R\), \(c_L \to c_R\), \(\theta_L \to \theta_R\), \(g_L \to g_R\), \(parameter_L \to parameter_R\), \(parameterL \to parameterR\), etc.).
  In short, the substitution is **not** limited to fields and projection operators — it extends to **all** quantities that carry a left/right chirality label.

- If a parameter is input parameter, it should be treated as an External parameter of FeynRules.
  - If no additional description for a coupling is provided, the coupling is also assumed to be an External parameter of FeynRules

- **CRITICAL RULE — All new physics coupling parameters must have InteractionOrder**: Every parameter in `M$Parameters` that is a **new physics coupling** (i.e. a constant that appears in the Lagrangian multiplying fields in the new physics sector, including Yukawa-like or BSM couplings) **must** include the `InteractionOrder` attribute, consistent with `M$InteractionOrderHierarchy` — **for both External and Internal parameters**. For example, use `InteractionOrder -> {NP, 1}` for new physics couplings. **Do not omit** `InteractionOrder` from any coupling definition: if an Internal parameter is defined as `Value -> someExternalCoupling` or represents a coupling matrix element (e.g. yl and yR in "#### Internal Parameters" section), it must also have `InteractionOrder -> {NP, 1}`. **Exception:** do **not** set `InteractionOrder` for **mixing** parameters (mixing angles, rotation angles, elements of mixing matrices that diagonalize masses, e.g. sw in "#### Internal Parameters" section). Mass/width entries are not couplings (and must not appear in M$Parameters for particles you define; see above). All coupling examples in this document include `InteractionOrder` — your output must do the same for every new physics coupling, whether External or Internal.

- **CRITICAL RULE — Real coupling matrices must declare `ComplexParameter -> False`**: For any coupling parameter defined as a matrix (`Indices -> {Index[...], Index[...]}`), if the matrix is real and symmetric (e.g. a real Yukawa coupling matrix), you **must** include `ComplexParameter -> False` in its parameter definition. Without this, FeynRules treats the parameter as complex, causing `Conjugate[y[i,j]]` to remain unevaluated. This breaks the Hermiticity check (FeynRules validator will report `L - L† ≠ 0`). Example:

  ```mathematica
  yS == {
    ParameterType    -> Internal,
    Indices          -> {Index[Generation], Index[Generation]},
    ComplexParameter -> False,
    ...
  }
  ```

- When writing the Lagrangian, use the multiplication symbol “*”, which is adopted in Mathematica, to explicitly indicate standard scalar multiplication. Do not use implicit multiplication (juxtaposition/space).

- **Do not put FeynmanGauge (or any gauge option) inside Lagrangian Blocks**: When you define a Lagrangian with `L := Block[{indices}, ...]`, the body of the Block must be **only** the Lagrangian expression (the sum of terms). **Do not** add `FeynmanGauge`, `FeynmanGauge == True`, or any other gauge-fixing or option symbol inside the Block. These do not belong in the Lagrangian definition and can break the model. Correct: `Block[{sp, sp2, ii, jj}, -(...) * S * (...)]`. Wrong: `Block[{...}, FeynmanGauge; -(...)]` or `Block[{...}, FeynmanGauge == True; ...]`.