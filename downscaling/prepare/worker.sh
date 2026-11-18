#!/bin/bash -l
set -euo pipefail
python=$1
cd "$2"
export TMPDIR="$6" XDG_CACHE_HOME="$6/cache" PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS="${NSLOTS:-1}"
# On SCC, initialize the same Python and R modules used for preparation.
if type module >/dev/null 2>&1; then
    module load python3/3.12.4 R/4.4.3
fi
exec "$python" -B -m downscaling.prepare.cluster "$3" "$4" "$5" "$SGE_TASK_ID"
