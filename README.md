# codex-team 0.3.0

[![PyPI Downloads](https://static.pepy.tech/personalized-badge/codex-team?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/codex-team)
Local executor for Codex teams with tmux, Git submodules and durable task queues.
Requires Python 3.10+, Git, tmux and a configured Codex CLI on a POSIX system.
The Python package has no runtime dependencies beyond the standard library.

## Install from source

```sh
python3 -m pip install .
codex-team --version
codex-team --root ./new-team start --no-attach
```

For system-managed Python installations, use a venv or pipx and keep its bin/
directory on PATH. There is no need to bypass Python package manager protections.
`codex-team-start --root ./new-team --no-attach` is an equivalent shortcut.
The first start requires a new or empty directory. Then use ./team in the created root.

## Included cost-saving settings

- A new session per deliverable; `--continue ID` resumes only the same deliverable.
- Default effort is low; the model is inherited from Codex, with no automatic switch or paid retry.
- `--context-file` supplies a summary of up to 8000 characters without an extra model call.
- Compact JSON; status includes active tasks and counters; compact result/wait output.
- Pending results show only ID/state; reports are bounded, with explicit --full output.
- Reports of up to 100 words, contracts/evidence in files and grouped tasks.
- Usage is measured by cumulative session deltas, separating metrics without double counting.
- Idle agents wait on a FIFO without model calls.
- Idle terminals are cleared and display the waiting-for-tasks message; logs/results persist.
- Already migrated schemas are queried without an unnecessary write lock.

```sh
./team config --effort low
./team task backend --file task.md --context-file context.md
./team task backend --continue ID --message 'Correction to the same deliverable'
./team status
./team wait ID --timeout 50
./team result ID
./team usage --task ID
```

No percentage savings are guaranteed: compare equivalent usage measurements.
Cached tokens are part of input; reasoning tokens are part of output. Do not add them again.
The generic instructions preserve operational authorization, Node isolation and each
project's required checks; reducing context does not waive validation.

## Existing teams

Installing this package updates the installation CLI. Each existing team uses its
own copy of tools/team_executor.py, which installation preserves. Updating another
root requires preserving its state/instructions, finishing deliveries with ./team stop
and coordinating the update to its copy before start.
The package contains selected sources, tests and generic rules; data, sessions,
consumer repositories and private configuration are excluded from build inputs.

## Offline source package tests

```sh
PYTHONPATH=src python3 -B -m unittest discover -s tests
```

Tests use temporary databases and simulated events; they do not call tmux, models or the network.
PROVENANCE.json records actual hashes of the source, packaged executor and instructions.
Only CONTROLLER_RULES was adapted to the current generic template during extraction;
the rest of the executor AST is identical to the source used for that extraction.

## Maintenance and ownership

This repository maintains the executor, distribution, tests and generic templates.
Each consumer maintains its controller, instructions, configuration and runtime.
Updates here do not move sources or change running teams.
The consumer's controller coordinates its gitlinks and agent registry.
No license was added: the selected sources had no license of their own.

The reviewed extraction can be reproduced without a build or release:

```sh
python3 tools/package_codex_team.py --source-root /path/to/source --output /new/directory
```

The exporter reads only the two explicitly selected Python sources, removes the
consumer-specific canvas section and checks executor AST equality outside
CONTROLLER_RULES. PROVENANCE.json records the hashes.
