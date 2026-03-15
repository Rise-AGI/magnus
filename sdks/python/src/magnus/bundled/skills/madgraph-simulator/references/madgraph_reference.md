# MadGraph5 Reference Guide

This document provides a comprehensive reference for using MadGraph5_aMC@NLO for event generation in high-energy physics simulations.

## Overview

MadGraph5_aMC@NLO is a framework for automatic generation of matrix elements and event generation for particle physics processes. It supports:
- Leading-order (LO) and next-to-leading-order (NLO) calculations
- Parton shower matching (MC@NLO, POWHEG)
- Integration with Pythia8 for hadronization
- Integration with Delphes for detector simulation

## Execution

MadGraph5 is executed via the Magnus cloud platform using two blueprints:

1. **`madgraph-compile`** — handles `import model`, multiparticle definitions, `generate`/`add process`, and `output`
2. **`madgraph-launch`** — handles `launch` with shower/detector/parameter settings

See the madgraph-simulator SKILL.md for the full `magnus run` workflow and the launch_commands state machine.

The sections below document **MG5 syntax and physics content** — process definitions, parameter settings, decay chains, etc. — which you need to construct correct blueprint arguments.

## Understanding UFO Models (IMPORTANT - Read First!)

Before generating processes with a BSM model, you MUST read the UFO model files to understand:
1. **Particle names** - What particles exist and their MG5 names
2. **Parameter names** - What parameters can be set and their block names

### Reading UFO Model Files

A UFO model directory contains these key files:

```
MyModel_UFO/
├── particles.py      # Particle definitions (names, PDG codes, masses)
├── parameters.py     # Parameter definitions (names, blocks, default values)
├── vertices.py       # Interaction vertices
├── couplings.py      # Coupling definitions
└── param_card.dat    # Default parameter card (if exists)
```

**Step 1: Read particles.py** to find particle names:
```python
# Example from particles.py
S = Particle(pdg_code = 50001,
             name = 'h0',           # <-- This is the MG5 particle name!
             antiname = 'h0',
             mass = Param.mH0,      # <-- Mass parameter name
             width = Param.WH0,     # <-- Width parameter name
             ...)
```

**Step 2: Read parameters.py** to find parameter names and blocks:
```python
# Example from parameters.py
mH0 = Parameter(name = 'mH0',
                lhablock = 'MASS',
                lhacode = [50001],   # PDG code
                value = 150.0,
                ...)

YQLct = Parameter(name = 'YQLct',
                  lhablock = 'YQLU',  # <-- Block name for param_card
                  lhacode = [2, 3],   # <-- Indices in block
                  value = 0.001,
                  ...)
```

### Common Particle Name Mappings

| Physics | MG5 Name | Anti-particle |
|---------|----------|---------------|
| top quark | `t` | `t~` |
| bottom quark | `b` | `b~` |
| charm quark | `c` | `c~` |
| up quark | `u` | `u~` |
| down quark | `d` | `d~` |
| strange quark | `s` | `s~` |
| electron | `e-` | `e+` |
| muon | `mu-` | `mu+` |
| tau | `ta-` | `ta+` |
| electron neutrino | `ve` | `ve~` |
| muon neutrino | `vm` | `vm~` |
| tau neutrino | `vt` | `vt~` |
| W boson | `w+` | `w-` |
| Z boson | `z` | `z` |
| photon | `a` | `a` |
| gluon | `g` | `g` |
| Higgs | `h` | `h` |

---

## Basic Workflow

### 1. Import Model

```
import model <model_name>
```

Common models:
- `sm` - Standard Model (use for SM background processes)
- `sm-no_b_mass` - SM with massless b quarks
- `heft` - Higgs Effective Field Theory
- `mssm` - Minimal Supersymmetric Standard Model
- Custom UFO: `import model /path/to/UFO_model` (use for BSM signal processes)

**IMPORTANT**:
- Use `import model sm` for Standard Model background processes (ttbar, ttV, tZ, VV, tW, etc.)
- Use `import model /path/to/UFO` for BSM signal processes that involve new particles

### 2. Define Multiparticles

Before generating processes, define useful multiparticle labels:

```
define l+ = e+ mu+ ta+
define l- = e- mu- ta-
define vl = ve vm vt
define vl~ = ve~ vm~ vt~
define j = g u c d s u~ c~ d~ s~
define p = g u c d s u~ c~ d~ s~ b b~
```

These allow compact process definitions like `p p > t t~, t > b l+ vl`.

### 3. Generate Process

```
generate <process>
```

Process syntax:
- Particles: `p` (proton), `e+`, `e-`, `mu+`, `mu-`, `t`, `t~` (anti-top), `w+`, `w-`, `z`, `h`, `a` (photon), `g` (gluon)
- Arrow: `>` separates initial and final states
- Comma: `,` for decay chains
- Multiparticle labels: `j` (jet), `l+` (e+ mu+), `l-` (e- mu-)

Examples:
```
generate p p > e+ e-           # Drell-Yan
generate p p > t t~            # Top pair production
generate p p > h, h > b b~     # Higgs to bb
generate p p > w+ j            # W + jet
```

### 4. Decay Chain Syntax (IMPORTANT)

MadGraph supports specifying particle decays using comma syntax. This is essential for processes with specific final states.

**Basic syntax**: `generate <production>, <particle> > <decay products>`

**Single decay**:
```
generate p p > t t~, t > b w+
```

**Multiple decays** (chain them with commas):
```
generate p p > t t~, t > b w+, t~ > b~ w-
```

**Nested decays** (decay products of decays):
```
generate p p > t t~, t > b w+, w+ > l+ vl, t~ > b~ w-, w- > l- vl~
```

**Using multiparticles in decays**:
```
# First define multiparticles
define l+ = e+ mu+ ta+
define l- = e- mu- ta-
define vl = ve vm vt
define vl~ = ve~ vm~ vt~
define j = g u c d s u~ c~ d~ s~

# Then use in process
generate p p > t t~, t > b l+ vl, t~ > b~ l- vl~
```

**Complex decay chains** (BSM example with scalar S decaying to muons):
```
# Signal: pp -> tt, one top decays to b+l+nu, other to j+S, S->mu+mu-
generate p p > t t~, t > b l+ vl, t~ > j h0, h0 > mu+ mu-
add process p p > t t~, t~ > b~ l- vl~, t > j h0, h0 > mu+ mu-
```

**Important notes**:
- Each particle can only appear once on the left side of a decay
- Use `add process` to include charge-conjugate processes
- The decay products must be kinematically allowed

### 5. Add Additional Processes

```
add process <process>
```

Combine multiple processes (important for charge-conjugate states):
```
generate p p > w+ j
add process p p > w- j
```

**Example - Complete ttbar dileptonic**:
```
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
generate p p > t t~, t > b l+ vl, t~ > b~ l- vl~
```

### 6. Output Project

```
output <directory>
```

Creates project directory with Cards/, SubProcesses/, Events/, etc.

### 5. Launch Event Generation

```
launch <directory>
```

After launch, use `set` commands to configure parameters, then `0` to start.

## Important Files

### Cards Directory

| File | Description |
|------|-------------|
| `proc_card_mg5.dat` | Process definition |
| `param_card.dat` | Model parameters (masses, couplings) |
| `run_card.dat` | Run parameters (energy, cuts, events) |

### Events Directory

After running `launch`:
```
Events/
└── run_01/
    ├── unweighted_events.lhe.gz    # Generated events
    └── run_01_tag_1_banner.txt     # Run configuration
```

## Key Parameters

### Collider Settings

| Parameter | Description | Default (LHC 13 TeV) |
|-----------|-------------|---------------------|
| `ebeam1` | Beam 1 energy (GeV) | 6500 |
| `ebeam2` | Beam 2 energy (GeV) | 6500 |
| `lpp1` | Beam 1 type (1=proton) | 1 |
| `lpp2` | Beam 2 type (1=proton) | 1 |

### Event Generation

| Parameter | Description | Default |
|-----------|-------------|---------|
| `nevents` | Number of events | 10000 |
| `iseed` | Random seed (0=auto) | 0 |

### Kinematic Cuts

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ptj` | Min jet pT (GeV) | 20 |
| `etaj` | Max jet |eta| | 5.0 |
| `ptl` | Min lepton pT (GeV) | 10 |
| `etal` | Max lepton |eta| | 2.5 |
| `drjj` | Min Delta R (jet-jet) | 0.4 |
| `drll` | Min Delta R (lep-lep) | 0.4 |

### PDF Settings

To use a non-default PDF set (e.g. for lepton-initiated processes), pass `--pdf <set_name>` to the `madgraph-launch` blueprint and configure the run card:

```
set run_card pdlabel lhapdf
set run_card lhaid <LHAPDF_ID>
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `pdlabel` | PDF type (`lhapdf` for LHAPDF sets, `nn23lo1` for built-in) | `nn23lo1` |
| `lhaid` | LHAPDF set ID number | 230000 |

**Common PDF sets** (pass to `--pdf`):

| PDF Set Name | LHAID | Description |
|---|---|---|
| `LUXlep-NNPDF31_nlo_as_0118_luxqed` | 82400 | Proton with lepton PDFs (LUXlep) — for lepton-initiated processes |
| `NNPDF31_nlo_as_0118` | 303400 | NNPDF3.1 NLO |
| `NNPDF31_lo_as_0118` | 315000 | NNPDF3.1 LO |
| `CT18NLO` | 14400 | CT18 NLO |

### Standard Model Masses (param_card)

| Parameter | Description | Value (GeV) |
|-----------|-------------|-------------|
| `MT` | Top mass | 172.5 |
| `MZ` | Z mass | 91.1876 |
| `MW` | W mass | 80.379 |
| `MH` | Higgs mass | 125.0 |
| `MB` | Bottom mass | 4.7 |

### Electroweak Parameters (param_card)

| Parameter | Description | Default Value |
|-----------|-------------|---------------|
| `aEWM1` | Inverse of α_EW at M_Z | 127.9 |
| `Gf` | Fermi constant | 1.16637e-5 |
| `aS` | Strong coupling α_s(M_Z) | 0.1181 |

**Setting electroweak parameters**:
```
set param_card SMINPUTS 1 127.9    # aEWM1 = 1/α_EW(M_Z)
set param_card SMINPUTS 2 1.16637e-5  # Gf
```

### Auto-Width Calculation

For BSM particles, you can let MadGraph automatically calculate decay widths:

```
# Set width to Auto for automatic calculation
set param_card DECAY <pdg_code> Auto

# Example: Auto-calculate width for particle with PDG 50001
set param_card DECAY 50001 Auto

# Or using parameter name (if known)
set WH0 Auto
```

**Important**: Auto-width requires that all decay channels are kinematically open and the couplings are set correctly.

### Parameter Scan

To scan over multiple values of a parameter:

```
# Syntax: set param_card <block> <code> scan:[v1,v2,v3,...]
set param_card MASS 50001 scan:[20,40,60,80,100,120,140,160]

# Or using parameter name
set mH0 scan:[20,40,60,80,100,120,140,160]
```

This generates separate runs for each parameter value, with output in `Events/run_01/`, `Events/run_02/`, etc.

---

## Pythia8 Parton Shower

To enable Pythia8 for parton showering and hadronization:

### In launch command
```
launch <output_dir>
shower=Pythia8
```

### Pythia8 Output
With Pythia8 enabled, you get:
- `Events/run_XX/tag_1_pythia8_events.hepmc.gz` - Showered events in HepMC format

---

## Delphes Detector Simulation

To enable Delphes for fast detector simulation:

### In launch command
```
launch <output_dir>
shower=Pythia8
detector=Delphes
# Then select Delphes card when prompted, or specify path:
/path/to/delphes_card_CMS.tcl
```

### Available Delphes Cards
- `CMS` - CMS detector card (shortcut)
- `ATLAS` - ATLAS detector card (shortcut)
- Full path: `/path/to/MG5/Delphes/cards/delphes_card_CMS.tcl`

### Delphes Output
With Delphes enabled, you get:
- `Events/run_XX/tag_1_delphes_events.root` - Reconstructed events in ROOT format

### Complete Example with Pythia8 + Delphes
```
import model sm
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
generate p p > t t~, t > b l+ vl, t~ > b~ l- vl~
output pp_ttbar_dilep
launch pp_ttbar_dilep
shower=Pythia8
detector=Delphes
done
CMS
done
set nevents 1000
set ebeam1 7000
set ebeam2 7000
set param_card MASS 6 172.76
set param_card MASS 5 4.2
set param_card SMINPUTS 1 127.9
done
```

---

## Common Processes

### Standard Model (Background Processes)

| Process | Command |
|---------|---------|
| Drell-Yan (ee) | `generate p p > e+ e-` |
| Drell-Yan (mumu) | `generate p p > mu+ mu-` |
| Top pair | `generate p p > t t~` |
| Single top (t-ch) | `generate p p > t j` |
| Higgs (ggF) | `generate p p > h` |
| Higgs (VBF) | `generate p p > h j j` |
| W pair | `generate p p > w+ w-` |
| Z pair | `generate p p > z z` |
| W+jets | `generate p p > w+ j` |

### Common SM Background Processes with Decays

**ttbar dileptonic** (both tops decay leptonically):
```
import model sm
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
generate p p > t t~, t > b l+ vl, t~ > b~ l- vl~
output pp_ttbar_dilep
```

**ttV (ttbar + vector boson)**:
```
import model sm
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~

# ttZ with Z->ll
generate p p > t t~ z, t > b l+ vl, t~ > b~ l- vl~, z > l+ l-
add process p p > t t~ z, t > b l+ vl, t~ > b~ l- vl~, z > vl vl~
output pp_ttZ

# ttW
generate p p > t t~ w+, t > b l+ vl, t~ > b~ l- vl~, w+ > l+ vl
add process p p > t t~ w-, t > b l+ vl, t~ > b~ l- vl~, w- > l- vl~
output pp_ttW
```

**tZ + jet**:
```
import model sm
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
define j = g u c d s u~ c~ d~ s~
generate p p > t z j, t > b l+ vl, z > l+ l-
add process p p > t~ z j, t~ > b~ l- vl~, z > l+ l-
output pp_tZj
```

**VV (diboson)**:
```
import model sm
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~

# WW -> lvlv
generate p p > w+ w-, w+ > l+ vl, w- > l- vl~
output pp_WW

# WZ -> lvll
generate p p > w+ z, w+ > l+ vl, z > l+ l-
add process p p > w- z, w- > l- vl~, z > l+ l-
output pp_WZ

# ZZ -> llll or llvv
generate p p > z z, z > l+ l-, z > l+ l-
add process p p > z z, z > l+ l-, z > vl vl~
output pp_ZZ
```

**tW (single top + W)**:
```
import model sm
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
generate p p > t w-, t > b l+ vl, w- > l- vl~
add process p p > t~ w+, t~ > b~ l- vl~, w+ > l+ vl
output pp_tW
```

### BSM Signal Process Examples

**Scalar S produced with top, S->mumu**:
```
import model /path/to/UFO
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
define j = g u c d s u~ c~ d~ s~

# pp -> t S, t->blv, S->mumu (read UFO to find S particle name, e.g., h0)
generate p p > t h0, t > b l+ vl, h0 > mu+ mu-
add process p p > t~ h0, t~ > b~ l- vl~, h0 > mu+ mu-
output pp_tS_Smumu
```

**Scalar S from top decay in ttbar**:
```
import model /path/to/UFO
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
define j = g u c d s u~ c~ d~ s~

# pp -> tt, one t->blv, other t->jS, S->mumu
generate p p > t t~, t > b l+ vl, t~ > j h0, h0 > mu+ mu-
add process p p > t t~, t~ > b~ l- vl~, t > j h0, h0 > mu+ mu-
output pp_tt_tojS_Smumu
```

---

## Complete Script Examples

### Standard Model ttbar

```
# ttbar.mg5
import model sm
generate p p > t t~
output pp_ttbar
launch pp_ttbar
set nevents 100000
set ebeam1 6500
set ebeam2 6500
0
```

### BSM Model Simulation

```
# bsm_simulation.mg5
import model /path/to/MyModel_UFO
generate p p > x1 x1~
output pp_x1x1
launch pp_x1x1
set MX1 500
set gX 0.3
set nevents 10000
set ebeam1 6500
set ebeam2 6500
0
```

### BSM Signal with Mass Scan, Pythia8, and Delphes

```
# Complete BSM signal generation with mass scan
import model /path/to/ScalarModel_UFO
define l+ = e+ mu+
define l- = e- mu-
define vl = ve vm
define vl~ = ve~ vm~
define j = g u c d s u~ c~ d~ s~

# Signal: pp -> t S, t->blv, S->mumu
generate p p > t h0, t > b l+ vl, h0 > mu+ mu-
add process p p > t~ h0, t~ > b~ l- vl~, h0 > mu+ mu-
output pp_tS_signal

launch pp_tS_signal
shower=Pythia8
detector=Delphes
done
CMS
done
# Physics parameters
set nevents 100
set ebeam1 7000
set ebeam2 7000
# SM parameters
set param_card SMINPUTS 1 127.9
set param_card MASS 6 172.76
set param_card MASS 5 4.2
# BSM couplings (read from UFO parameters.py to find correct names)
set param_card YQLU 1 3 0.0
set param_card YQLU 2 3 0.001
set param_card YLLE 2 2 1.0
set param_card YQLD 3 3 0.0
# Mass scan for scalar S (PDG code from particles.py)
set param_card MASS 50001 scan:[20,40,60,80,100,120,140,160]
# Auto-calculate width
set param_card DECAY 50001 Auto
done
```

### Multiple Processes

```
# w_jets.mg5
import model sm
generate p p > w+ j
add process p p > w- j
output pp_wj
launch pp_wj
set nevents 50000
0
```

## Troubleshooting

### Common Errors

1. **"No valid diagram"**
   - Check particle names and conservation laws
   - Verify model supports the interaction

2. **"Model not found"**
   - Check UFO path is correct
   - Ensure UFO contains required files (particles.py, vertices.py, etc.)

3. **Timeout**
   - Increase timeout parameter
   - Reduce number of events for testing

4. **Memory issues**
   - Reduce number of final-state particles
   - Use appropriate cuts

### Performance Tips

1. Start with small `nevents` (100-1000) for testing
2. Use appropriate kinematic cuts to reduce phase space
3. For large samples, consider gridpack mode

## Output Files Summary

After successful generation:

```
<output_dir>/
├── Cards/
│   ├── param_card.dat      # Model parameters used
│   ├── run_card.dat        # Run configuration used
│   └── proc_card_mg5.dat   # Process definition
├── Events/
│   └── run_01/
│       ├── unweighted_events.lhe.gz  # Generated events
│       └── run_01_tag_1_banner.txt   # Full configuration
└── HTML/
    └── index.html          # Results summary
```

The main output is `Events/run_XX/unweighted_events.lhe.gz` containing the generated events in Les Houches Event format.
