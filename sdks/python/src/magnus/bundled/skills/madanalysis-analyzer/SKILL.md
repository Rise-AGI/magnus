---
name: madanalysis-analyzer
description: Run MadAnalysis5 analysis on generated event files via Magnus cloud. Triggers when the user wants to analyze Monte Carlo events, create kinematic distributions, apply event selections, or produce cut-flow reports. Supports parton-level (LHE), hadron-level (HepMC), and reconstruction-level (LHCO/ROOT) analysis.
---

# MadAnalysis Analyzer

## Overview

This skill runs MadAnalysis5 in normal mode for post-generation analysis of Monte Carlo event files. It uses the `madanalysis-process` blueprint on the Magnus cloud platform (see magnus skill).

**CRITICAL: Do NOT write `submit` in your analysis script.** The cloud runner automatically appends `submit analysis_output`. Any user-written `submit` lines are stripped before execution. This differs from standalone MadAnalysis5 usage where `submit` is required.

## Workflow

### Step 1: Locate Event Files

Find the event files from the MadGraph output directory. After `madgraph-launch`, events are in:

```
<output>/Events/run_XX/
├── unweighted_events.lhe.gz              # parton-level
├── tag_1_pythia8_events.hepmc.gz         # hadron-level (if Pythia8)
├── tag_1_delphes_events.lhco.gz          # reco-level LHCO (if Delphes)
└── tag_1_delphes_events.root             # reco-level ROOT (if Delphes)
```

Choose the appropriate file based on your analysis level.

### Step 2: Build the Analysis Script

Write MA5 commands as a multi-line string. Use `{EVENTS_DIR}` as a placeholder for the events directory path — it is replaced at runtime with the actual download location on the cloud.

```
import {EVENTS_DIR}/Events/run_01/tag_1_delphes_events.lhco.gz as signal
set signal.type = signal
set main.lumi = 100

plot PT(mu[1]) 50 0 500
plot MET 50 0 500
plot M(mu+ mu-) 100 0 200

select N(mu) >= 2
select PT(mu[1]) > 25
```

**CRITICAL: Do NOT write `submit`** — the cloud runner automatically appends `submit analysis_output` to your script. Any user-written `submit` lines are stripped before execution.

### Step 3: Run the Analysis

Execute the `madanalysis-process` blueprint using `magnus run` (see magnus skill):

```bash
magnus run madanalysis-process -- \
  --events path/to/pp_ttbar \
  --script "import {EVENTS_DIR}/Events/run_01/unweighted_events.lhe.gz as sample
set sample.type = signal
plot PT(mu+) 50 0 500
plot M(mu+ mu-) 100 0 200
select N(mu+) >= 1" \
  --output path/to/ma5_output \
  --level parton
```

**Parameters**:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--events` | Yes | Path to the process directory from `madgraph-launch` (contains `Events/`) |
| `--script` | Yes | MA5 commands with `{EVENTS_DIR}` placeholder. Do NOT include `submit`. |
| `--output` | Yes | Where to download the analysis output directory |
| `--level` | Yes | Analysis level: `parton`, `hadron`, or `reco` |

The events directory is uploaded via FileSecret. On success, the analysis output is downloaded to `--output`.

**WARNING**: If `--output` points to an existing directory, it will be **deleted and replaced** by the download.

**Downloaded directory structure** (example: `--output path/to/ma5_output`):
```
path/to/ma5_output/
└── analysis_output/
    └── Output/
        └── HTML/
            └── MadAnalysis5job_0/
                ├── index.html
                └── selection_N/         # one per selection cut
                    └── *.png            # histogram images
```

## Analysis Levels

| Level | Flag | Input Formats | Use When |
|-------|------|---------------|----------|
| `parton` | `-P` | `.lhe`, `.lhe.gz` | Parton-level events (no shower/detector) |
| `hadron` | `-H` | `.hepmc`, `.hepmc.gz` | Showered events (Pythia8), FastJet available |
| `reco` | `-R` | `.lhco`, `.lhco.gz`, `.root` | Detector-simulated events (Delphes) |

**Level must match the event file format**:
- LHE files require `--level parton`
- HepMC files require `--level hadron`
- LHCO/ROOT files require `--level reco`

## Event File Selection

When multiple event file types exist in a run directory, prefer higher-level formats (priority order):

1. `tag_*_delphes_events.lhco.gz` — reco-level, LHCO (most common for Delphes)
2. `tag_*_delphes_events.root` — reco-level, ROOT
3. `tag_*_pythia8_events.hepmc.gz` — hadron-level
4. `unweighted_events.lhe.gz` — parton-level (always available)

## MA5 Script Quick Reference

### Importing events

```
import {EVENTS_DIR}/Events/run_01/unweighted_events.lhe.gz as signal
import {EVENTS_DIR}/Events/run_01/tag_1_delphes_events.lhco.gz as bkg
```

### Configuring datasets

```
set signal.type = signal
set bkg.type = background
set bkg.xsection = 831.76          # cross section in pb
set main.lumi = 100                # luminosity in fb^-1
set main.normalize = lumi
set main.stacking_method = stack
```

### Plotting observables

```
plot PT(mu[1]) 50 0 500            # leading muon pT
plot MET 50 0 500                  # missing ET
plot M(mu+ mu-) 100 0 200         # dimuon invariant mass
plot N(j) 15 0 15                  # jet multiplicity
plot DELTAR(e-[1], j[1]) 40 0 10  # deltaR(leading electron, leading jet)
```

Particle ranking: `mu[1]` = leading (highest-pT) muon, `mu[2]` = sub-leading.

### Applying selections

```
select N(mu) >= 2                  # require >= 2 muons
select PT(mu[1]) > 25              # leading muon pT > 25 GeV
reject MET < 50                    # reject events with MET < 50
select 80 < M(mu+ mu-) < 100      # mass window
```

Selections are applied sequentially (AND logic). Order matters for cut-flow tables.

## Reference Documentation

- See [references/madanalysis_reference.md](references/madanalysis_reference.md) for the complete MA5 command reference, including all observables, selection syntax, output format, and analysis examples
