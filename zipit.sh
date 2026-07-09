#!/usr/bin/env bash
set -euo pipefail

zip -r Kinora.zip kinora \
    -x "kinora/deps/*" \
    -x "kinora/deps.deleted-*/*" \
    -x "kinora/tests/*" \
    -x "kinora/examples/040_l020_g1_rf_h-.h5" \
    -x "kinora/examples/trajectory_export.h5" \
    -x "kinora/examples/t_junction_*.s3d" \
    -x "kinora/examples/t_junction_*.s3d.sz" \
    -x "kinora/__pycache__/*" \
    -x "kinora/**/__pycache__/*" \
    -x "*.pyc" \
    -x "*~" \
    -x "*.~undo-tree~" \
    -x "*.DS_Store"
