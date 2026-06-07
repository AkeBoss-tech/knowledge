# Seed note from Akash email — 2026-06-06

Source: personal email from Akash Dubey to the research mailbox.
Status: seeded, partially verified.

## Goal

The goal is dual-arm task-motion-scheduling: deciding what each arm should do, when actions should overlap, how to generate feasible motions, and how to replan when manipulation timing or geometry breaks the symbolic plan.

## Candidate benchmark tasks

- Entangled tabletop rearrangement
- Shared shelf organization
- Dual-arm block tower / shared construction zone
- Obstacle-clearing plus placement
- Dual-arm sorting with uncertain action durations
- Assembly with precedence constraints
- Container holding plus insertion
- Handoff benchmark
- Dynamic queue / continuous task stream

## High-level take

Long-horizon bimanual coordination is hard because symbolic task plans can look good while geometric feasibility fails late.

## Concepts / systems mentioned

- PDDLStream
- Behavior Trees
- Knowledge Graphs
- Task-motion-scheduling as a middle layer
- Action skeletons
- COAST
- Fast Downward
- Graphs of Convex Sets / IRIS / Drake

## Links from the email

### Videos
- Russ Tedrake — Motion Planning Around Obstacles with Graphs of Convex Sets
  https://youtu.be/KSCC7mVJzaw?si=Wt8sgkxEfjNlqgeJ
- Russ Tedrake — Planning with Graphs of Convex Sets (newer lecture)
  https://youtu.be/JZokn4Pc-YY?si=pNR53zSaCV9lst3a

### Packages / docs
- PDDLStream
  https://github.com/caelan/pddlstream
- Drake IRIS / planning geometry docs
  https://drake.mit.edu/doxygen_cxx/group__planning__iris.html
- Fast Downward
  https://github.com/aibasel/downward
- RobotLocomotion gcs-science-robotics
  https://github.com/RobotLocomotion/gcs-science-robotics

### Papers
- Integrating Symbolic Planners and Blackbox Samplers via Optimistic Adaptive Planning (PDDLStream)
  https://arxiv.org/abs/1802.08705
- Behavior Tree Generation for Robotic Tasks with Lightweight LLMs
  https://arxiv.org/abs/2403.12761
- COAST: Constraints and Streams for Task and Motion Planning
  https://branvu.github.io/coast.github.io/
- Policy-Guided Lazy Search with Feedback for Task and Motion Planning
  https://arxiv.org/abs/2210.14055
- Task and Motion Planning in Hierarchical 3D Scene Graphs
  https://arxiv.org/abs/2403.08094
- High-Performance Dual-Arm Task and Motion Planning for Tabletop Rearrangement (SDAR)
  https://arxiv.org/abs/2512.08206
- Certified Polyhedral Decompositions of Collision-Free Configuration Space
  https://alexandreamice.github.io/publication/dai-2023-certified/dai-2023-certified.pdf
- Robotics-to-chemistry application paper
  https://link.springer.com/article/10.1007/s10514-023-10136-2

### Chat continuations mentioned in the email
- https://chatgpt.com/share/6a1e5b9d-65e0-8333-8abd-a0ce766125c8
- https://chatgpt.com/share/6a1e5bb1-0610-8333-8a4e-0401914986f2
