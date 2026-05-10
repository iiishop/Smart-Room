# Lift Pitch PPT Extension Plan (Mar 25)

## 0) Professor requirement (verbatim)

Students present their more developed ideas, including a timeline for the build, user testing/experiments and write up.

This deck extension directly maps to that requirement with three explicit sections:

1. More developed idea (technical + research direction)
2. Build timeline (with key course dates)
3. User testing / experiments + dissertation write-up plan

---

## 1) Recommended new slides to add (after your current dashboard image slide)

### Slide A - From MVP Pipeline to Research Prototype

**Title**: From Streaming MVP to Spatial Intelligence Prototype

**Core message**:
- Already validated: Quest 3 -> Unity -> FastAPI -> Dashboard RGB/depth stream
- Now extending from "data transport" to "semantic understanding + interaction"
- Target outcome: a usable spatial-computing pipeline for smart-room context awareness

**Talk track (30-40s)**:
"In the first stage I proved the end-to-end stream works. In this stage, I move from transport infrastructure to intelligence: semantic scene reasoning and human-centered validation."

---

### Slide B - Developed Idea: System Architecture v2

**Title**: Developed Idea - Architecture v2

**Layout suggestion**: left-to-right pipeline over your existing dashboard screenshot

**Blocks**:
1. Quest 3 Sensing Layer
   - RGB stream
   - Depth stream
   - Calibration/alignment (RGB-depth mapping)
2. Edge Backend Layer (Python/FastAPI)
   - WebSocket ingest
   - Frame buffering + synchronization
   - Health/latency monitoring
3. Reasoning Layer (new)
   - Semantic visual reasoning model (scene/state inference)
   - MURK-inspired uncertainty/risk-aware module (for ambiguous or low-confidence perception)
4. Interface Layer
   - Live dashboard (RGB/depth/status)
   - Decision explanation panel (what the model inferred and why)

**Talk track (40-50s)**:
"The key extension is the reasoning layer. I am not only showing pixels and depth maps; I am adding interpretable semantic inference with uncertainty handling so downstream actions are safer and explainable."

---

### Slide C - Research Questions and Hypotheses

**Title**: Research Questions and Hypotheses

**RQ1**: Can RGB+depth fusion improve semantic scene understanding quality over RGB-only baseline in this setup?

**RQ2**: Does uncertainty-aware reasoning (MURK-like strategy) reduce unsafe/incorrect decisions in noisy or partial observations?

**RQ3**: Does an explainable dashboard improve user trust and task performance?

**Hypotheses**:
- H1: RGB+depth > RGB-only in scene-state classification accuracy
- H2: uncertainty-aware gating reduces high-risk false positives
- H3: explanation-enhanced interface improves SUS/usability and trust scores

---

### Slide D - User Testing and Experiment Design

**Title**: User Testing and Experiments

**Participants (proposed)**:
- n = 8-12 (students/research peers)
- within-subject comparison when possible

**Conditions**:
1. Baseline dashboard (stream + raw status)
2. Extended dashboard (semantic reasoning + confidence/explanations)

**Tasks**:
- Interpret room state and identify anomalies
- Decide whether to trigger a suggested action
- Compare confidence under partial/noisy perception

**Metrics**:
- Objective: task completion time, decision accuracy, error count
- Model: precision/recall/F1 for scene-state labels; calibration/confidence reliability
- UX: SUS, perceived trust, NASA-TLX (optional)

**Outputs**:
- Quantitative comparison charts
- Qualitative feedback themes (confusion points, trust drivers)

---

### Slide E - Build Timeline (aligned to course milestones)

**Title**: Build Timeline and Deliverables

Use this as a Gantt-style table:

| Date / Window | Milestone | Planned output |
|---|---|---|
| 25 Mar | Individual Lift Pitch | Developed idea + architecture v2 + timeline |
| 27 Mar | Draft Proposal | Refined RQs, method, experiment protocol draft |
| 28 Mar - 12 Apr | Build Sprint 1 | Stable RGB/depth + reasoning prototype integration |
| 13 Apr | Ethics Screening Form | Participant flow, consent, data handling plan |
| 14 Apr - 29 Apr | Build Sprint 2 + pilot tests | Pilot user study + metric sanity checks |
| 29 Apr | Low/High Risk application (if needed) | Formal ethics/risk submission |
| May - early Jun | Main experiments | Full user testing + ablation runs |
| 12 Jun | Exhibition Form | Demo concept + setup requirements |
| Jun - Jul | Analysis + writing Sprint 1 | Results analysis, dissertation outline drafts |
| July | Dissertation Outline Review | Full outline with methods/results structure |
| 13-15 Jul | Exhibition Set Up | Install and validate demo pipeline |
| mid Jul - 2 Aug | Writing Sprint 2 | Complete near-final dissertation draft |
| 3 Aug | Exhibition Take Down | Post-exhibition refinement |
| 4 Aug - 20 Aug | Final polishing | Final edits, figures, references, appendix |
| 21 Aug | Final Submission | Dissertation + final project package |

---

### Slide F - Write-up Plan (explicitly answering professor requirement)

**Title**: Dissertation Write-up Plan

**Chapter plan**:
1. Introduction & motivation (MR sensing -> semantic room understanding)
2. Related work (Quest/MR sensing, semantic visual reasoning, uncertainty-aware inference)
3. System design (pipeline, architecture v2, implementation decisions)
4. Methodology (experiment design, participants, metrics, ethics)
5. Results (model + user-study outcomes)
6. Discussion (limitations, threats to validity, practical implications)
7. Conclusion and future work

**Writing schedule**:
- Apr: Introduction + Method draft
- May: System + Implementation draft
- Jun: Results skeleton + figure templates
- Jul: Full draft consolidation + supervisor feedback loop
- Aug: Final revision and formatting

---

### Slide G - Risk and Mitigation (optional but strong)

**Title**: Risks, Ethics, and Mitigation

**Technical risks**:
- Sensor stability and frame sync drift
- Depth noise in reflective/low-texture scenes
- Model latency under real-time constraints

**Mitigation**:
- Graceful fallback and reconnect logic
- confidence thresholds and uncertainty flags
- log-based performance monitoring + ablations

**Ethics**:
- informed consent, anonymized logs, minimal personal data retention
- transparent explanation when model confidence is low

---

## 2) One-slide "compliance checklist" (quick win)

If you want a direct "professor ask covered" slide, add this:

**Title**: Requirement Coverage

- More developed ideas -> Architecture v2 + semantic reasoning + uncertainty module
- Timeline for build -> detailed milestone plan from Mar to Aug
- User testing/experiments -> participant design, metrics, pilot + main study
- Write up -> chapter structure + month-by-month writing schedule

---

## 3) Suggested visual style for new slides

- Keep your existing dashboard screenshot as anchor visual
- Use one dark "concept" slide (A/B), then light content slides (C-F)
- Prefer diagrams, timeline bars, and metric cards over dense bullets
- On experiment slide, show a 2-column condition comparison to avoid text overload

---

## 4) 90-second narration template (can be read almost verbatim)

"Since the last pitch, I moved from a streaming MVP to a more developed research prototype. The core extension is a semantic reasoning layer on top of Quest 3 RGB and depth streams, with uncertainty-aware logic inspired by MURK-style risk handling. My build plan is staged from late March to August, aligned with proposal, ethics, exhibition, and final submission milestones. Methodologically, I will run a user study comparing a baseline dashboard and an explainable reasoning dashboard, with both objective performance metrics and usability/trust measures. In parallel, I have a structured dissertation plan covering system design, methodology, results, and critical discussion."
