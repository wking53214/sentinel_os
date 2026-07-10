"""
array_ops.py

Single seam for array/vector math in Sentinel OS.

The two RL engines need real array operations -- weight
initialization, softmax routing, clipping, log-probabilities. Both
import from this module instead of importing numpy directly. If
numpy changes an API again, or Sentinel ever needs to swap the
underlying array library, this is the one file that needs to change,
instead of every file that happens to touch a vector.

Deliberately NOT used by code that only needs simple scalar math
(factorial, ceiling, etc.) -- that code uses Python's own stdlib
`math` module directly. It never actually needed numpy, so routing
it through this seam would just be a second, pointless layer.
"""

import numpy as _np

# Types
ndarray = _np.ndarray

# Random number generation. Both the legacy RandomState API and the
# newer Generator API (default_rng) are in use across the two RL
# engines today; the whole submodule is re-exported unchanged so
# neither engine's behavior shifts.
random = _np.random

# Array construction / manipulation
zeros = _np.zeros
array = _np.array
concatenate = _np.concatenate

# Reductions
max = _np.max
sum = _np.sum
mean = _np.mean
std = _np.std
argmax = _np.argmax

# Elementwise math
exp = _np.exp
log = _np.log
clip = _np.clip
isclose = _np.isclose
