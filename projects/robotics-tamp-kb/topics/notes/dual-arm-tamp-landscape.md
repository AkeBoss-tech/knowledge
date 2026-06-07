# Dual-arm TAMP landscape — initial synthesis

Updated: 2026-06-06

## What seems to matter most

The interesting problem is not just task planning or motion planning alone. It is the seam between them:

1. how to assign work across two arms,
2. which actions can overlap safely,
3. when to test geometry early enough to avoid late failure,
4. and how to recover when execution invalidates a symbolic plan.

That makes **task-motion-scheduling** feel like the core abstraction gap.

## Core buckets

### 1) Symbolic + continuous bridge
- **PDDLStream** is the obvious foundation: it extends symbolic planning with black-box samplers for geometry, kinematics, visibility, and motion constraints.
- The main pain point is still obvious: if sampling/feasibility checks happen too weakly or too late, long-horizon plans can collapse after looking symbolically valid.

### 2) Better search over action skeletons
- **LAZY / Policy-Guided Lazy Search** is important because it tries to keep one integrated search and gradually inject geometric information rather than treating symbolic planning and motion checking as two separate worlds.
- **COAST** matters because it explicitly uses constraints to narrow the task-planning space before or during sampling, which is exactly the kind of pruning dual-arm settings need.

### 3) Geometry and feasibility structure
- **Graphs of Convex Sets**, **IRIS**, and Drake’s planning geometry stack look like the right tools when the question becomes: how do we represent feasible motion regions cleanly enough that high-level plans are not lying to us?
- This feels especially relevant if the eventual system wants a geometry-aware feasibility layer instead of blind stream sampling alone.

### 4) Execution layer
- **Behavior trees** are interesting less as a novelty and more as an execution/control interface. They may be a practical way to organize recovery, retries, handoffs, and partial replanning without building a giant brittle state machine.

### 5) Dual-arm-specific systems
- **SDAR** is the strongest direct seed from the email because it is explicitly about synchronous dual-arm rearrangement, long-horizon entanglement, dependency-driven task planning, and layered motion planning with GPU support.
- This is closer to the real target than generic single-arm TAMP papers.

## Candidate benchmark tasks worth tracking
- entangled tabletop rearrangement
- shared shelf organization
- block construction / dual-arm assembly
- handoff across workspaces
- obstacle-clearing for target retrieval
- queue / conveyor tasks with continuous arrivals
- container holding + insertion

## Practical opinion

If this project becomes a build path, the likely stack is not “just use an LLM planner.” It is more like:

- symbolic task abstraction,
- a scheduling layer for parallelism and resource locks,
- geometry-aware feasibility checks,
- fast motion planning backends,
- execution with recovery.

That feels much more like a systems problem than a prompting problem.

## Seeded verified sources
- PDDLStream paper: arXiv 1802.08705
- COAST project page
- Drake IRIS planning docs
- Behavior Tree Generation for Robotic Tasks with Lightweight LLMs: arXiv 2403.12761
- Policy-Guided Lazy Search with Feedback for TAMP: arXiv 2210.14055
- Task and Motion Planning in Hierarchical 3D Scene Graphs: arXiv 2403.08094
- SDAR dual-arm rearrangement: arXiv 2512.08206

## What to verify next
- whether the robotics-to-chemistry paper is actually central or just adjacent
- whether knowledge graphs are a useful planning primitive here or mostly metadata infrastructure
- whether SDAR-T is best understood as a behavior-tree-like task layer or something more specific
- what the cleanest open-source experiment path is: PDDLStream, Drake/GCS, or a hybrid
