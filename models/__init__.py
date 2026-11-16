#!/usr/bin/env python3
"""models - the vendored-weights package.

The captcha classifier loader (captcha_model._featurize) resolves its
adapter by name: `from models import FEATURIZER` and then calls
FEATURIZER.images_to_tensor(images). FEATURIZER is the adapter MODULE itself
(models/FEATURIZER.py), shipped beside the weights it matches - the
hcaptcha-challenger grid library (models/hcaptcha/*.onnx, git-ignored,
fetched + sha256-verified by install.sh against models/CHECKSUMS, disclosed
under GPL-3.0 local use in models/SOURCE) swaps files together with this
featurizer (one weights-family swap, see captcha_model.py's docstring).
"""
from . import FEATURIZER as FEATURIZER  # the adapter module is the export
from .FEATURIZER import images_to_tensor as images_to_tensor  # flat, too
