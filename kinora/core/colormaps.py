"""Colour maps for the background-image overlay, baked from matplotlib offline.

Runtime has no matplotlib (it does not play well inside Blender), so each colour
map is stored here as a small set of sRGB stops and applied to a Blender
``ColorRamp`` node in the shader.  The overlay image carries the raw scalar
field; the ColorRamp turns value into colour, so switching colour map or
interpolation never re-reads the data.

Data contract: the overlay assumes the image values are already normalised to
the range [0, 1] by the upstream data processing and simply clips at 1.  No
min/max rescaling happens in the addon (see ``core/overlay.py``).
"""

# Auto-generated sRGB colour-map stops (position, (r, g, b)). Do not hand-edit;
# regenerate from matplotlib with tools/dump_hdf5_images-style sampling.
_STOPS = {
    "GRAY": ((0.0, (0.0, 0.0, 0.0)), (1.0, (1.0, 1.0, 1.0))),
    "VIRIDIS": (
        (0.0, (0.267, 0.0049, 0.3294)),
        (0.0625, (0.2823, 0.095, 0.4173)),
        (0.125, (0.2788, 0.1755, 0.4834)),
        (0.1875, (0.259, 0.2515, 0.5247)),
        (0.25, (0.2297, 0.3224, 0.5457)),
        (0.3125, (0.1994, 0.3876, 0.5546)),
        (0.375, (0.1727, 0.4488, 0.5579)),
        (0.4375, (0.149, 0.5081, 0.5573)),
        (0.5, (0.1276, 0.5669, 0.5506)),
        (0.5625, (0.1206, 0.6258, 0.5335)),
        (0.625, (0.1579, 0.6838, 0.5017)),
        (0.6875, (0.2461, 0.7389, 0.452)),
        (0.75, (0.3692, 0.7889, 0.3829)),
        (0.8125, (0.516, 0.8312, 0.2943)),
        (0.875, (0.6785, 0.8637, 0.1895)),
        (0.9375, (0.8456, 0.8873, 0.0997)),
        (1.0, (0.9932, 0.9062, 0.1439)),
    ),
    "INFERNO": (
        (0.0, (0.0015, 0.0005, 0.0139)),
        (0.0625, (0.0423, 0.0281, 0.1411)),
        (0.125, (0.1293, 0.0473, 0.2908)),
        (0.1875, (0.2383, 0.0366, 0.3964)),
        (0.25, (0.3415, 0.0623, 0.4294)),
        (0.3125, (0.4412, 0.0993, 0.4316)),
        (0.375, (0.5409, 0.1347, 0.4151)),
        (0.4375, (0.6401, 0.1714, 0.3811)),
        (0.5, (0.7357, 0.2159, 0.3302)),
        (0.5625, (0.8224, 0.2752, 0.2661)),
        (0.625, (0.8943, 0.3534, 0.1936)),
        (0.6875, (0.947, 0.4492, 0.1153)),
        (0.75, (0.9784, 0.5579, 0.0349)),
        (0.8125, (0.9879, 0.6753, 0.0653)),
        (0.875, (0.9746, 0.7977, 0.2063)),
        (0.9375, (0.9476, 0.9174, 0.4107)),
        (1.0, (0.9884, 0.9984, 0.6449)),
    ),
    "MAGMA": (
        (0.0, (0.0015, 0.0005, 0.0139)),
        (0.0625, (0.0396, 0.0311, 0.1335)),
        (0.125, (0.1131, 0.0655, 0.2768)),
        (0.1875, (0.2117, 0.062, 0.4186)),
        (0.25, (0.3167, 0.0717, 0.4854)),
        (0.3125, (0.4147, 0.1104, 0.5047)),
        (0.375, (0.5128, 0.1482, 0.5076)),
        (0.4375, (0.6136, 0.1818, 0.4985)),
        (0.5, (0.7164, 0.215, 0.4753)),
        (0.5625, (0.8169, 0.2559, 0.4365)),
        (0.625, (0.9043, 0.3196, 0.3881)),
        (0.6875, (0.9609, 0.4183, 0.3596)),
        (0.75, (0.9867, 0.5356, 0.3822)),
        (0.8125, (0.9961, 0.6537, 0.4462)),
        (0.875, (0.9969, 0.7696, 0.5349)),
        (0.9375, (0.9924, 0.8843, 0.6401)),
        (1.0, (0.9871, 0.9914, 0.7495)),
    ),
    "TURBO": (
        (0.0, (0.19, 0.0718, 0.2322)),
        (0.0625, (0.2511, 0.2524, 0.6337)),
        (0.125, (0.2763, 0.4212, 0.8912)),
        (0.1875, (0.2586, 0.5796, 0.9988)),
        (0.25, (0.1584, 0.7355, 0.9231)),
        (0.3125, (0.0927, 0.8655, 0.7623)),
        (0.375, (0.1966, 0.949, 0.5947)),
        (0.4375, (0.4278, 0.9942, 0.3857)),
        (0.5, (0.6436, 0.99, 0.2336)),
        (0.5625, (0.8047, 0.9245, 0.2046)),
        (0.625, (0.933, 0.8124, 0.2267)),
        (0.6875, (0.9931, 0.6741, 0.2035)),
        (0.75, (0.9836, 0.4929, 0.1285)),
        (0.8125, (0.9211, 0.3149, 0.0548)),
        (0.875, (0.8161, 0.1846, 0.0181)),
        (0.9375, (0.6645, 0.0844, 0.0042)),
        (1.0, (0.4796, 0.0158, 0.0106)),
    ),
    "HOT": (
        (0.0, (0.0416, 0.0, 0.0)),
        (0.0625, (0.2063, 0.0, 0.0)),
        (0.125, (0.371, 0.0, 0.0)),
        (0.1875, (0.5358, 0.0, 0.0)),
        (0.25, (0.7005, 0.0, 0.0)),
        (0.3125, (0.8652, 0.0, 0.0)),
        (0.375, (1.0, 0.0299, 0.0)),
        (0.4375, (1.0, 0.1946, 0.0)),
        (0.5, (1.0, 0.3593, 0.0)),
        (0.5625, (1.0, 0.524, 0.0)),
        (0.625, (1.0, 0.6887, 0.0)),
        (0.6875, (1.0, 0.8534, 0.0)),
        (0.75, (1.0, 1.0, 0.0272)),
        (0.8125, (1.0, 1.0, 0.2743)),
        (0.875, (1.0, 1.0, 0.5213)),
        (0.9375, (1.0, 1.0, 0.7684)),
        (1.0, (1.0, 1.0, 1.0)),
    ),
    "CIVIDIS": (
        (0.0, (0.0, 0.1351, 0.3048)),
        (0.0625, (0.0, 0.1788, 0.4148)),
        (0.125, (0.1034, 0.2204, 0.4358)),
        (0.1875, (0.1951, 0.2644, 0.4259)),
        (0.25, (0.2637, 0.3078, 0.4228)),
        (0.3125, (0.3242, 0.3513, 0.4263)),
        (0.375, (0.3808, 0.3952, 0.4357)),
        (0.4375, (0.4352, 0.4398, 0.4511)),
        (0.5, (0.4887, 0.4853, 0.471)),
        (0.5625, (0.5478, 0.5319, 0.4717)),
        (0.625, (0.6091, 0.5798, 0.4636)),
        (0.6875, (0.672, 0.6293, 0.448)),
        (0.75, (0.7365, 0.6806, 0.424)),
        (0.8125, (0.8027, 0.734, 0.3902)),
        (0.875, (0.8707, 0.7896, 0.3433)),
        (0.9375, (0.9411, 0.8475, 0.2758)),
        (1.0, (0.9957, 0.9093, 0.2178)),
    ),
}

# Enum items (identifier, label, description) for the colour-scheme selector.
COLORMAP_ITEMS = [
    ("VIRIDIS", "Viridis", "Perceptually uniform blue-green-yellow"),
    ("INFERNO", "Inferno", "Perceptually uniform black-red-yellow"),
    ("MAGMA", "Magma", "Perceptually uniform black-purple-white"),
    ("TURBO", "Turbo", "High-contrast rainbow (improved jet)"),
    ("HOT", "Hot", "Black-red-yellow-white heat scale"),
    ("CIVIDIS", "Cividis", "Colour-vision-deficiency friendly"),
    ("GRAY", "Grayscale", "Black to white"),
]

# Enum items for the interpolation selector.  Identifiers match Blender's
# ShaderNodeTexImage.interpolation values so they can be assigned directly.
INTERPOLATION_ITEMS = [
    ("Linear", "Linear (smooth)", "Bilinear smoothing between grid cells"),
    ("Closest", "Closest (blocky)", "Show raw grid cells without smoothing"),
    ("Cubic", "Cubic (smoothest)", "Cubic smoothing between grid cells"),
]

DEFAULT_COLORMAP = "VIRIDIS"
DEFAULT_INTERPOLATION = "Linear"


def _srgb_to_linear(channel):
    """Convert one sRGB channel (0..1) to scene-linear."""
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def apply_colormap(color_ramp, name):
    """Configure a Blender ColorRamp's stops to match colour map *name*.

    Stop colours are converted sRGB -> scene-linear so they display correctly
    under a Standard/sRGB view transform.  (Blender's default AgX/Filmic view
    transform will still tone-map them; switch to Standard for exact colours.)
    """
    stops = _STOPS.get(name) or _STOPS[DEFAULT_COLORMAP]
    elements = color_ramp.elements
    # A ColorRamp must keep at least one element; trim down then rebuild.
    while len(elements) > 1:
        elements.remove(elements[-1])
    first_position, first_rgb = stops[0]
    elements[0].position = first_position
    elements[0].color = (*(_srgb_to_linear(c) for c in first_rgb), 1.0)
    for position, rgb in stops[1:]:
        element = elements.new(position)
        element.color = (*(_srgb_to_linear(c) for c in rgb), 1.0)
    color_ramp.color_mode = "RGB"
    color_ramp.interpolation = "LINEAR"
