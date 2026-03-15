# MadAnalysis5 Reference Guide

This document provides a reference for using MadAnalysis5 in **normal mode** for event-level analysis in high-energy physics.

## Overview

MadAnalysis5 is a framework for phenomenological analysis at particle colliders. Normal mode provides a command-line interface for:
- Importing event samples (LHE, HepMC, LHCO, ROOT)
- Defining particle labels and multiparticle groups
- Plotting kinematic observables
- Applying event/object-level selections (cut-flow)
- Producing HTML/LaTeX analysis reports

## Execution

MadAnalysis5 is executed via the Magnus cloud platform using the `madanalysis-process` blueprint. See the madanalysis-analyzer SKILL.md for the full `magnus run` workflow.

**CRITICAL: Do NOT write `submit` in your analysis script.** The cloud runner automatically appends `submit analysis_output`. Any user-written `submit` lines are stripped before execution.

### Analysis Levels

| Level | Flag | Input Format | Description |
|-------|------|-------------|-------------|
| `parton` | `-P` | `.lhe`, `.lhe.gz` | Parton-level (no jet clustering) |
| `hadron` | `-H` | `.hepmc`, `.hepmc.gz` | Hadron-level (FastJet available) |
| `reco` | `-R` | `.lhco`, `.lhco.gz`, `.root` | Reconstruction-level (detector objects) |

### Event File Location

After `madgraph-launch`, event files are in `<output>/Events/run_XX/`. Auto-detection priority (prefer higher-level formats):

1. `tag_*_delphes_events.lhco.gz` — reco-level, LHCO
2. `tag_*_delphes_events.root` — reco-level, ROOT
3. `tag_*_pythia8_events.hepmc.gz` — hadron-level
4. `unweighted_events.lhe.gz` — parton-level (always available)

### event_index.yaml Format

If an `event_index.yaml` exists, it maps process names to event file locations:

```yaml
pp_to_tt_3l:
  - 14TeV:
      path: simulation/3l+1b+nj@14TeV/events/ttbar
      runs:
        - path: /abs/path/to/Events/run_01
          nevents: 100
pp_to_tS_3l_decay:
  - 14TeV:
      path: simulation/scalar-3l+1b+nj@14TeV/events/decay
      runs:
        - path: /abs/path/to/Events/run_01
          nevents: 100
          mH0: 20.0
        - path: /abs/path/to/Events/run_02
          nevents: 100
          mH0: 40.0
```

Signal processes may have multiple runs corresponding to different mass points via parameter scan.

---

## MA5 Command Reference (Normal Mode)

### import - Load Event Files

```
import <filepath> [as <dataset_name>]
```

```
import /path/to/events.lhco.gz as signal
import /path/to/bkg_events.lhco.gz as background
```

Multiple files imported with the same dataset name are combined.

**Supported formats**: `.lhe(.gz)`, `.hep(.gz)`, `.hepmc(.gz)`, `.lhco(.gz)`, `.root`

### set - Configure Properties

#### Dataset Properties

```
set <dataset>.type = signal          # or: background
set <dataset>.xsection = 0.0454     # cross section in pb
set <dataset>.weight = 1.0
set <dataset>.title = "t\\bar{t}"
set <dataset>.linecolor = red
set <dataset>.linestyle = solid      # solid, dashed, dotted, dash-dotted
set <dataset>.backcolor = yellow
set <dataset>.backstyle = solid
```

#### Main Session Properties

```
set main.normalize = lumi            # none, lumi, lumi_weight
set main.lumi = 100                  # luminosity in fb^-1
set main.stacking_method = stack     # stack, superimpose, normalize2one
set main.logX = true
set main.logY = true
set main.graphic_render = matplotlib # matplotlib, root, none
set main.SBratio = S/sqrt(S+B)      # S/B, S/sqrt(B), S/sqrt(S+B)
```

### define - Particle Labels

```
define <label> = <particle1> <particle2> ...
```

```
define mu = mu+ mu-
define l = e+ e- mu+ mu-
define invisible = invisible 9000005    # extend invisible for BSM
```

#### Predefined Labels (Reco Mode)

| Label | Object |
|-------|--------|
| `j` | jets |
| `b` | b-tagged jets |
| `nb` | non-b-tagged jets |
| `e-` / `e+` | electrons / positrons |
| `mu-` / `mu+` | muons / antimuons |
| `ta-` / `ta+` | taus / antitaus |
| `a` | photons |

#### Predefined Multiparticle Labels

| Label | Contents |
|-------|----------|
| `l+` | e+, mu+ |
| `l-` | e-, mu- |
| `invisible` | neutrinos + neutralino1 + gravitino |
| `hadronic` | quarks + gluon |

### plot - Create Histograms

```
plot <OBSERVABLE>(<particles>) [nbins] [xmin] [xmax] [options]
```

```
plot PT(mu[1]) 50 0 500
plot M(mu+ mu-) 100 0 200 [logY]
plot MET 50 0 500
plot N(j) 15 0 15
plot DELTAR(e-[1], j[1]) 40 0 10
```

#### Single-Particle Observables

| Observable | Description | Units |
|-----------|-------------|-------|
| `E` | Energy | GeV |
| `M` | Invariant mass | GeV |
| `PT` | Transverse momentum | GeV |
| `PX`, `PY`, `PZ` | Momentum components | GeV |
| `ET` | Transverse energy | GeV |
| `MT` | Transverse mass | GeV |
| `ETA` | Pseudorapidity | -- |
| `PHI` | Azimuthal angle | rad |
| `Y` | Rapidity | -- |
| `P` | Total momentum | GeV |

#### Two-Particle Observables

| Observable | Description |
|-----------|-------------|
| `DELTAR(<p1>, <p2>)` | Delta R = sqrt(deta^2 + dphi^2) |
| `DPHI_0_PI(<p1>, <p2>)` | Delta phi in [0, pi] |
| `DPHI_0_2PI(<p1>, <p2>)` | Delta phi in [0, 2pi] |
| `DETA(<p1>, <p2>)` | Delta eta |

#### Global Event Observables

| Observable | Description | Units |
|-----------|-------------|-------|
| `MET` | Missing transverse energy | GeV |
| `MHT` | Missing hadronic HT | GeV |
| `THT` | Total hadronic HT (scalar sum of jet PT) | GeV |
| `TET` | Total transverse energy | GeV |
| `SQRTS` | Center-of-mass energy | GeV |
| `N(<particle>)` | Particle multiplicity | -- |

#### Observable Prefixes

| Prefix | Meaning | Example |
|--------|---------|---------|
| `s` | Scalar sum | `sPT(j j)` |
| `d` | Difference | `dPT(j[1], j[2])` |
| `r` | Ratio | `rPT(j[1], j[2])` |

#### Particle Ranking

```
mu[1]     # leading (highest-PT) muon
mu[2]     # sub-leading muon
j[1]      # leading jet
```

Ranking is by descending transverse momentum.

### select / reject - Event Selection

```
# Event-level cuts
select <OBSERVABLE>(<particles>) <operator> <value>
reject <OBSERVABLE>(<particles>) <operator> <value>

# Object-level cuts (filter candidates)
select (<particle>) <OBSERVABLE> <operator> <value>
reject (<particle>) <OBSERVABLE> <operator> <value>

# Range syntax
select <value1> <op1> <OBSERVABLE>(<particles>) <op2> <value2>
```

**Operators**: `<`, `>`, `<=`, `>=`, `=`, `!=`

```
select N(mu) >= 2
select N(j) >= 4
select PT(mu[1]) > 25
reject MET < 50
select 80 < M(mu+ mu-) < 100
select (mu) PT > 25              # keep only muons with PT > 25
reject (j) |ETA| > 2.5           # reject forward jets
reject DELTAR(e-[1], j[1]) < 0.4
```

**Important**: Selections are applied sequentially (AND logic). Order matters for cut-flow.

### submit - Run the Analysis

```
submit [<output_directory_name>]
```

This compiles the analysis, runs over all datasets, normalizes histograms, and generates reports (HTML, LaTeX, PDF).

> **When using the `madanalysis-process` blueprint**: Do NOT write `submit` in your script. The cloud runner automatically appends `submit analysis_output`. Any user-written `submit` lines are stripped before execution.

### display - Inspect Session

```
display main
display <dataset_name>
display_datasets
display_particles
display_multiparticles
```

### Other Commands

```
remove <dataset_name>
reset
resubmit
open                    # open HTML report
help [command]
```

---

## Output Format

After `submit`, the output directory contains:

```
<output_dir>/
  Output/
    SAF/
      <dataset>/
        <dataset>.saf             # Global info (xsection, nevents)
        <analysis>_0/
          Cutflows/
            <dataset>.saf         # Cut-flow counters
          Histograms/
            histos.saf            # Histogram data
  HTML/
    MadAnalysis5job_*/
      index.html                  # Main report
  LaTeX/
    MadAnalysis5job_*/
      main.tex
```

### SAF Cut-Flow Format

```
<InitialCounter>
"Initial number of events"
198320    0
...
</InitialCounter>
<Counter>
"N(mu)>=2"
105988    0
...
</Counter>
```

---

## Complete Analysis Examples

### Example 1: Single Background Sample (Reco-Level)

```
import /path/to/ttbar/Events/run_01/tag_1_delphes_events.lhco.gz as ttbar
set ttbar.type = background
set ttbar.xsection = 831.76
set ttbar.title = "t\\bar{t}"
set ttbar.linecolor = blue

set main.lumi = 100
set main.normalize = lumi

plot PT(mu[1]) 50 0 500
plot PT(j[1]) 50 0 500
plot MET 50 0 500
plot N(j) 15 0 15
plot N(b) 10 0 10
plot M(mu+ mu-) 100 0 500

select N(mu) >= 2
select PT(mu[1]) > 25
select PT(mu[2]) > 15
select N(j) >= 1
```

> When using the `madanalysis-process` blueprint, omit `submit` — the runner appends it automatically.

### Example 2: Signal vs Background Comparison

```
import /path/to/signal.lhco.gz as sig
import /path/to/ttbar.lhco.gz as bkg_tt
import /path/to/wjets.lhco.gz as bkg_wj

set sig.type = signal
set sig.title = "Signal (m=100 GeV)"
set sig.linecolor = red
set bkg_tt.type = background
set bkg_tt.title = "t\\bar{t}"
set bkg_tt.linecolor = blue
set bkg_wj.type = background
set bkg_wj.title = "W+jets"
set bkg_wj.linecolor = green

set main.lumi = 100
set main.normalize = lumi
set main.stacking_method = stack

plot PT(mu[1]) 50 0 500
plot MET 50 0 500
plot M(mu+ mu-) 50 0 200

select N(mu) >= 2
select PT(mu[1]) > 25
select 80 < M(mu+ mu-) < 100
```

> When using the `madanalysis-process` blueprint, omit `submit` — the runner appends it automatically.

### Example 3: Parton-Level Quick Check

```
import /path/to/unweighted_events.lhe.gz as sample
set sample.type = signal

plot PT(mu+) 50 0 500
plot M(mu+ mu-) 100 0 500
plot ETA(mu+) 50 -5 5
```

> When using the `madanalysis-process` blueprint, omit `submit` — the runner appends it automatically.

### Example 4: Multi-Sample Analysis via Magnus

When analysing multiple event files (e.g., from an `event_index.yaml`), build the MA5 script with `{EVENTS_DIR}` placeholders and run via the `madanalysis-process` blueprint:

```bash
# Pass the process directory (containing Events/) as --events
# The script uses {EVENTS_DIR} which resolves to the uploaded events path
magnus run madanalysis-process -- \
  --events /path/to/pp_ttbar \
  --script "$(cat <<'SCRIPT'
import {EVENTS_DIR}/Events/run_01/tag_1_delphes_events.lhco.gz as bkg_tt
set bkg_tt.type = background
set bkg_tt.xsection = 831.76

set main.lumi = 100
set main.normalize = lumi

plot MET 50 0 500
plot M(mu+ mu-) 50 0 200
select N(mu) >= 2
select PT(mu[1]) > 25
SCRIPT
)" \
  --output ./analysis_output \
  --level reco
```

> The runner automatically appends `submit analysis_output` — do NOT include it in the script.

---

## Troubleshooting

### Common Errors

1. **"Cannot open file"**
   - Check file path exists and has correct permissions
   - Verify file extension matches the analysis level (LHE for parton, LHCO/ROOT for reco)

2. **"Unknown observable"**
   - Check observable name spelling (case-sensitive: `PT` not `pt`)
   - Some observables are level-specific (e.g., `b` jets only in reco mode)

3. **"No dataset imported"**
   - Ensure `import` command comes before `plot`/`select`/`submit`

4. **Level mismatch**
   - LHCO/ROOT files require `--level reco` (`-R`)
   - LHE files require `--level parton` (`-P`)
   - HepMC files require `--level hadron` (`-H`)

### Performance Tips

1. For quick validation, use a single small event file first
2. Use `set main.graphic_render = none` to skip plot rendering if only cut-flow is needed
3. For large samples, increase the Magnus timeout: `magnus run madanalysis-process --timeout 3600 -- ...`
