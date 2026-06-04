"""Gym-vocabulary aliases → canonical concept labels.

These are SKOS-style altLabels: the surface forms a coach actually types
("pecs", "delts", "hammies") mapped to the canonical node names in the graph.
Stored on the nodes as `alt_labels` and matched by the resolver's exact/fuzzy
passes, so domain jargon resolves deterministically instead of relying on the
embedding model to know that "pec" means "chest".
"""

MUSCLE_ALIASES = {
    "chest": ["pec", "pecs", "pectoral", "pectorals"],
    "deltoids": ["delts", "delt", "shoulder muscle"],
    "quads": ["quad", "quadriceps", "quadricep", "thigh"],
    "hamstrings": ["hammies", "hams", "hamstring"],
    "glutes": ["glute", "gluteus", "butt", "booty"],
    "lats": ["lat", "latissimus"],
    "biceps": ["bis", "bicep", "guns"],
    "triceps": ["tris", "tricep"],
    "core": ["abs", "ab", "abdominals", "six pack", "midsection"],
    "calves": ["calf"],
    "traps": ["trap", "trapezius"],
    "forearms": ["forearm"],
    "obliques": ["oblique", "side abs"],
    "hip flexors": ["hip flexor"],
    "hip adductors": ["adductors", "inner thigh", "groin"],
    "rotator cuff": ["cuff"],
    "lower back": ["erectors", "spinal erectors"],
    "middle back": ["mid back", "rhomboids"],
    "upper back": ["upper traps"],
}

JOINT_ALIASES = {
    "knee": ["knees", "patella", "kneecap"],
    "hip": ["hips"],
    "shoulder": ["shoulders", "glenohumeral"],
    "ankle": ["ankles"],
    "elbow": ["elbows"],
    "wrist": ["wrists"],
    "lumbar spine": ["lumbar", "low back", "lower spine"],
    "cervical spine": ["neck", "cervical"],
    "thoracic spine": ["thoracic", "mid spine", "upper spine"],
}

ALIASES = {"Muscle": MUSCLE_ALIASES, "Joint": JOINT_ALIASES}
