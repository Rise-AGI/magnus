# UFO Format Reference

This document explains the structure of UFO (Universal FeynRules Output) model directories and how to read them for use with MadGraph5.

## UFO Directory Structure

A UFO model directory contains these key files:

```
MyModel_UFO/
├── __init__.py           # Package initialization
├── particles.py          # Particle definitions
├── parameters.py         # Parameter definitions
├── vertices.py           # Interaction vertices
├── couplings.py          # Coupling definitions
├── lorentz.py            # Lorentz structures
├── orders.py             # Interaction order definitions
├── coupling_orders.py    # Coupling order constraints
├── function_library.py   # Mathematical functions
├── object_library.py     # Base classes
├── write_param_card.py   # Parameter card writer
└── param_card.dat        # Default parameter card (if generated)
```

## Reading particles.py

This file defines all particles in the model. Each particle is a `Particle` object:

```python
# Example from particles.py
Snew = Particle(
    pdg_code = 9000001,        # PDG code (>9000000 for generic BSM; use established reservations where applicable)
    name = 'Snew',             # MG5 particle name (use this in generate commands!)
    antiname = 'Snew',         # Anti-particle name (same if self-conjugate)
    spin = 1,                  # 2*spin+1: 1=scalar, 2=fermion, 3=vector
    color = 1,                 # Color rep: 1=singlet, 3=triplet, 8=octet
    mass = Param.MSnew,        # Mass parameter reference
    width = Param.WSnew,       # Width parameter reference
    charge = 0,                # Electric charge
    texname = 'S_{new}',       # LaTeX name
    antitexname = 'S_{new}',
    GhostNumber = 0,
    LeptonNumber = 0,
    Y = 0                      # Hypercharge
)
```

**Key fields to extract**:
- `name` — the particle name to use in MG5 `generate` commands
- `pdg_code` — needed for `set param_card MASS <pdg> <value>`
- `mass` — identifies the mass parameter name
- `width` — identifies the width parameter name

## Reading parameters.py

This file defines all model parameters. Each parameter is a `Parameter` object:

```python
# External parameter (set via param_card)
MSnew = Parameter(
    name = 'MSnew',
    nature = 'external',
    type = 'real',
    value = 100.0,             # Default value
    texname = 'M_{S_{new}}',
    lhablock = 'MASS',         # SLHA block name
    lhacode = [9000001]        # Index in the block (usually PDG code for masses)
)

# Coupling parameter
yS = Parameter(
    name = 'yS',
    nature = 'external',
    type = 'real',
    value = 0.1,
    texname = 'y_S',
    lhablock = 'NPINPUTS',     # Block name for param_card
    lhacode = [1]              # Index in the block
)

# Internal (derived) parameter
aEW = Parameter(
    name = 'aEW',
    nature = 'internal',
    type = 'real',
    value = '1/aEWM1',         # Formula
    texname = '\\alpha_{EW}'
)
```

**Key fields to extract**:
- `lhablock` — the SLHA block name for `set param_card <block> <code> <value>`
- `lhacode` — the index(es) in the block
- `name` — parameter name
- `value` — default value

## Mapping to MadGraph5 Commands

### Setting Masses
```
# From particles.py: pdg_code = 9000001, mass = Param.MSnew
# From parameters.py: MSnew has lhablock='MASS', lhacode=[9000001]
set param_card MASS 9000001 150.0
```

### Setting Couplings
```
# From parameters.py: yS has lhablock='NPINPUTS', lhacode=[1]
set param_card NPINPUTS 1 0.5
```

### Setting Matrix Elements
```
# From parameters.py: YQLct has lhablock='YQLU', lhacode=[2,3]
set param_card YQLU 2 3 0.001
```

### Auto-Width
```
# From particles.py: pdg_code = 9000001
set param_card DECAY 9000001 Auto
```

### Mass Scan
```
# From particles.py: pdg_code = 9000001
set param_card MASS 9000001 scan:[20,40,60,80,100,120,140,160]
```

## Common Particle Name Mappings (SM)

| Physics | MG5 Name | Anti-particle | PDG |
|---------|----------|---------------|-----|
| top quark | `t` | `t~` | 6 |
| bottom quark | `b` | `b~` | 5 |
| electron | `e-` | `e+` | 11 |
| muon | `mu-` | `mu+` | 13 |
| tau | `ta-` | `ta+` | 15 |
| e-neutrino | `ve` | `ve~` | 12 |
| mu-neutrino | `vm` | `vm~` | 14 |
| W boson | `w+` | `w-` | 24 |
| Z boson | `z` | `z` | 23 |
| photon | `a` | `a` | 22 |
| gluon | `g` | `g` | 21 |
| Higgs | `h` | `h` | 25 |

## Tips

1. **Always read `particles.py` first** to get the MG5-compatible particle names
2. **Then read `parameters.py`** to understand how to set couplings and masses
3. BSM particle names in MG5 come from the `name` field in `particles.py`, not from the ClassName in the `.fr` file
4. For coupling matrices, `lhacode` gives the matrix indices (e.g., `[2, 3]` = row 2, column 3)
5. The `param_card.dat` file (if present) shows all parameters in SLHA format
