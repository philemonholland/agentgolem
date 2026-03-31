# How AgentGolem's Agents Actually Work

This document is a draft blog post about the architecture behind AgentGolem.
It is written to explain the system in technical but readable terms, without
claiming more than the implementation really does.

---

## Introduction

AgentGolem is not a single chatbot with a bigger prompt. It is a persistent,
multi-agent system built around seven long-lived agents, each with its own
memory graph, identity documents, internal state, and ethical orientation.

The project is best understood as an experiment in **structured, persistent,
auditable agent cognition**:

- structured, because the agents operate through explicit subsystems rather than
  one giant prompt;
- persistent, because memory, identity, and state survive process restarts;
- auditable, because external actions, memory mutations, and identity changes
  are logged and constrained.

The important claim is **not** that these agents are conscious in any strong
philosophical sense. The more modest claim is that AgentGolem simulates several
ingredients people often associate with ongoing minded behavior:

- continuity across time;
- self-observation and self-correction;
- memory-driven reasoning;
- role-differentiated social deliberation;
- recurring offline consolidation during "sleep";
- constrained changes to identity and behavior.

That makes it more interesting than a stateless assistant, but still firmly an
engineered software system rather than a magical breakthrough.

---

## The Core Idea

AgentGolem runs seven agents as an "ethical council."

Six agents are aligned to distinct ethical vectors derived from the project's
VowOS foundation, and a seventh agent plays a good-faith adversarial role. The
goal is not to create seven random personalities. The goal is to create seven
different *interpretive stances* that can:

- read the same world differently,
- notice different risks,
- prioritize different values,
- and push on each other's weak spots.

Each agent has:

- its own `graph.db` memory store;
- its own `soul.md` identity document;
- its own `heartbeat.md` running self-summary;
- its own persistent temperament and internal state;
- access to shared communication channels and approved tools.

The architecture assumes that "mind-like" behavior emerges more convincingly
from **ongoing interaction between memory, attention, narrative, and social
feedback** than from a single prompt asking a model to "act conscious."

---

## The Lifecycle: Wake, Work, Sleep, Repeat

Each agent cycles through several modes:

- `AWAKE`
- `WINDING DOWN`
- `ASLEEP`
- `PAUSED`

While awake, an agent processes:

1. human messages,
2. peer messages,
3. autonomous work.

Autonomous work can include:

- private reflection;
- messaging peers;
- browsing the web;
- inspecting the codebase;
- tuning approved runtime settings;
- proposing code changes through the system's evolution path.

When the agent winds down, it writes a heartbeat-style reflection. When it
sleeps, it does not simply stop. Instead, it runs repeated memory walks over
its graph, with consolidation, abstraction, and edge-updating behavior.

This matters because it creates a meaningful difference between:

- **online cognition**: immediate action and conversation;
- **offline cognition**: consolidation, reweighting, and recombination.

That separation is one of the strongest ways AgentGolem differs from typical
"agent loop" projects.

---

## Ethical Formation Before Open Exploration

The current system does not start by letting agents roam freely.

Agents 1-6 first go through a local, curated ethical formation pipeline built
from files in `docs/vow_agents/`:

1. absorb the common foundation;
2. absorb an agent-specific vow document;
3. discuss ethics with peers;
4. return to a recurring calibration protocol.

Agent 7 is different. It does not receive an agent-specific vow file and keeps
its distinct adversarial role.

This is an important design choice. Earlier versions relied more heavily on
repeated website reading. The newer design moves the foundational material into
versioned local documents so:

- the system is cheaper to run;
- the ethical foundation is explicit and inspectable;
- the agents are not constantly re-deriving their values from external pages;
- the calibration protocol remains stable and reusable.

In other words, the system tries to separate **formation** from **exploration**.

---

## Memory Is a Graph, Not a Transcript Buffer

Many LLM agents say they have memory when they really mean one of three things:

- a short chat history;
- a vector store of chunks;
- a scratchpad of notes.

AgentGolem instead stores memory as a typed graph in SQLite.

Each memory node is a natural-language claim with metadata such as:

- node type;
- trustworthiness;
- usefulness;
- emotional score;
- salience;
- centrality;
- access count;
- provenance through one or more sources.

Nodes are connected by typed edges such as:

- `supports`
- `contradicts`
- `part_of`
- `same_as`
- `supersedes`
- `derived_from`

This gives the system a richer structure than "semantic similarity search over
past text." A memory can be:

- contradicted rather than merely overwritten;
- superseded rather than deleted;
- clustered with related memories;
- linked to specific evidence;
- revisited during sleep walks.

That does not magically solve memory. It does, however, move the architecture
closer to a **knowledge graph with uncertainty and provenance** than to a simple
retrieval database.

---

## Trust Is Not Binary

One of the more serious design choices in AgentGolem is that external content
is treated as untrusted by default.

Every claim enters with provenance, and the trust pipeline updates confidence in
odds space using source reliability plus an independence discount. That means:

- one weak web page does not become truth just because it was read;
- ten copies of the same rumor do not count as ten independent confirmations;
- emotionally intense but weakly supported content can be quarantined.

This is especially important in a system that is allowed to browse, email, and
interact with hostile surfaces.

Compared with many hobbyist agent architectures, this is one of the main
differences in emphasis. A lot of agent systems focus on action selection first
and memory quality second. AgentGolem tries to make **memory hygiene** and
**provenance discipline** first-class concerns.

---

## The Consciousness Kernel

If there is a part of AgentGolem that most directly aims at a
"consciousness-like" simulation, it is the consciousness kernel.

Each agent maintains several interlocking self-modeling components:

### 1. Internal State

This is the fast-changing felt-sense layer: curiosity, confidence, emotional
valence, engagement, cognitive load, isolation, and current attention mode.

It is not meant as a scientific model of affect. It is a practical control
surface that helps the agent behave differently when it is overloaded, locked
onto a topic, socially hungry, or uncertain.

### 2. Metacognitive Monitor

This subsystem looks for patterns in the agent's own behavior:

- repetitive thinking;
- likely bias risks;
- avoidance;
- possible inauthenticity;
- neglected clusters in memory.

This is one of the most interesting pieces in practice because it gives the
system a mechanism for noticing when it is getting stuck in abstraction,
circling the same topic, or avoiding uncomfortable lines of inquiry.

### 3. Attention Director

The attention director translates internal state plus metacognitive signals into
a concrete directive:

- primary drive;
- secondary drive;
- avoidance correction;
- social need;
- energy budget;
- recommended mode.

That directive becomes part of future prompts, which gives the agent something
like internal momentum instead of purely reactive behavior.

### 4. Narrative Synthesizer

The system periodically turns recent experience into narrative chapters. These
chapters are stored as identity-related graph nodes and connected over time.

This gives the agent a running story about what it has been wrestling with, how
it has changed, and what tensions remain unresolved.

### 5. Self-Model

The self-model is a slower-moving synthesis layer. It is where stronger
convictions, patterns of behavior, and recurring roles start to become part of
the agent's own description of itself.

Taken together, these subsystems create something important:

the agent is not just answering prompts; it is repeatedly building a model of
its own state, habits, and trajectory.

That is still not consciousness in any strong metaphysical sense. But it is a
more serious approximation of **self-referential continuity** than a plain chat
loop.

---

## Temperament and Personality Drift

AgentGolem also gives each agent a persistent temperament. Temperament is not
the same as momentary state.

State changes quickly.

Temperament changes slowly and provides defaults for:

- cognitive style;
- communication tone;
- social orientation;
- curiosity style;
- conflict response;
- emotional baseline;
- risk appetite.

Recent work also wires temperament into LLM behavior through temperature bias.
For example:

- provocative agents get a slightly higher temperature;
- precise agents get a slightly lower one.

That matters because personality in many agent systems is only textual flavor.
Here, some personality traits also have mechanical effects on behavior.

It is still a light-touch mechanism, not a complete personality engine. But it
pushes the system beyond "roleplay in the system prompt."

---

## Deliberation Is Social, Not Just Internal

A major difference between AgentGolem and single-agent architectures is that it
has peer-to-peer deliberation built in.

Agents can:

- broadcast ideas to the full council;
- send focused messages to specific peers;
- challenge one another from different ethical perspectives;
- revisit shared topics over time rather than only within a single turn.

This means the system can generate disagreement and synthesis internally.

That is different from standard "self-critique" prompting, where one model is
asked to produce an answer and then revise it. In AgentGolem, disagreement is
distributed across agents with different ethical anchors, memory stores, and
temperaments.

The result is not guaranteed wisdom. Sometimes it just produces more elaborate
conversation. But when it works, it creates a more believable process of
deliberation than one model talking to itself in a single context window.

---

## Sleep Is a Design Choice, Not a Cosmetic One

Many projects use "sleep" as a metaphor for "run a summarizer now and then."

AgentGolem goes further than that. During sleep, agents run repeated graph walks
with:

- emotion-weighted seed selection;
- leaky activation;
- refractory periods;
- phase changes between consolidation and dream-like exploration;
- STDP-like edge reinforcement and weakening;
- merge and abstraction proposals.

Whether or not one likes the neuroscience analogy, the engineering idea is
clear: memory should be reorganized differently offline than online.

This makes the architecture closer to a **continual memory system with replay**
than to a chatbot with periodic summarization.

It is still a heuristic simulation. The sleep dynamics are not trying to claim
biological realism. But they do create a meaningful distinction between:

- what is salient now,
- what survives repeated replay,
- and what becomes structurally central over time.

---

## Tools and Capabilities Are Explicit

Another practical lesson from agent engineering is that models will invent tool
names if you let them.

AgentGolem therefore exposes a closed, explicit capability set. Agents choose
from:

- internal capabilities such as `think.private`, `share.peer`,
  `inspect.codebase`, and `optimize.setting`;
- registered tools such as `browser.fetch_text`, `email.send`, or
  `moltbook.read`.

The system now validates chosen capability names against the authoritative set
built from internal capability definitions plus the tool registry.

This is a small but important design point. It shifts the architecture away from
"LLM emits arbitrary verbs and we hope they parse" toward a more disciplined
capability model.

That makes the system more robust, easier to audit, and easier to extend.

---

## Identity Changes Are Constrained

There are two especially sensitive identity documents:

- `soul.md`
- `heartbeat.md`

The heartbeat is a recurring self-summary. The soul is deeper and more stable.

AgentGolem does not allow arbitrary external content to directly rewrite those
documents. Soul updates require evidence and sufficient confidence. External
communications such as email and Moltbook are approval-gated. All of this is
logged.

This is another place where the project differs from looser autonomous-agent
experiments. Rather than celebrating unconstrained self-modification, it treats
identity mutation as something that should be:

- evidential,
- versioned,
- reviewable,
- and hard to trigger accidentally.

---

## How This Compares to Existing Agent Architectures

The easiest way to understand AgentGolem is to compare it with a few common
patterns.

### Compared with a Stateless Chat Assistant

A normal assistant gets a prompt, maybe some short history, and returns a
response.

AgentGolem adds:

- persistent identity;
- persistent graph memory;
- recurring offline consolidation;
- self-observation;
- multi-agent social feedback;
- explicit tool capability selection.

So it is much more persistent and process-oriented than a standard assistant.
The tradeoff is complexity: more components, more failure modes, and more need
for observability.

### Compared with ReAct-Style Tool Agents

ReAct-style systems interleave reasoning and acting through a tool loop.

AgentGolem overlaps with that pattern, but differs in three ways:

1. it has long-term memory rather than only local scratchpads;
2. it has explicit internal-state and metacognitive layers;
3. it embeds tool use inside a broader lifecycle with sleep, identity, and peer
   dialogue.

So it is not just "tool use plus chain-of-thought." It is trying to model the
agent as an ongoing process.

### Compared with AutoGPT / BabyAGI-Style Loops

AutoGPT-like systems usually emphasize autonomous task decomposition and
goal-directed iteration.

AgentGolem is less task-execution-centric and more cognition-centric.

It cares less about "finish this objective tree as fast as possible" and more
about:

- how an agent forms a perspective;
- how memory becomes structured;
- how disagreement changes reasoning;
- how recurring self-audit influences future action.

That makes it weaker than classic productivity agents for straightforward task
automation, but arguably more interesting as a research platform for persistent
agent behavior.

### Compared with Memory-Augmented Chatbots

Many memory-augmented systems bolt retrieval onto a chatbot.

AgentGolem goes further by combining:

- typed graph memory;
- provenance and trust updates;
- contradiction handling;
- usefulness scoring;
- sleep-time replay and restructuring;
- identity-linked narrative synthesis.

So the memory system is not just recall support. It is part of the architecture
that shapes future cognition.

### Compared with Generative Agent or Character Simulation Systems

There is some overlap with "generative agents" and character simulation work,
especially around memory, reflection, and social interaction.

Where AgentGolem is somewhat different is in its emphasis on:

- auditability;
- source reliability;
- approval gates;
- explicit ethical formation;
- and code-level self-inspection and self-modification pathways.

It is less like a sandboxed social simulation and more like a persistent
research system with safety rails.

### Compared with Debate or Multi-Agent Deliberation Systems

Debate systems often split roles across multiple models so they can critique
each other.

AgentGolem shares that intuition, but it is not a formal debate engine. It does
not run a strict proposition / rebuttal / judge pipeline. Instead, it runs
ongoing, informal, role-differentiated deliberation among peers with memory.

That makes it looser than formal debate architectures, but also more natural and
potentially more cumulative over time.

---

## What the System Is Trying to Approximate

If we strip away the branding and ask what AgentGolem is really trying to build,
the answer is something like this:

> a persistent, socially situated, memory-shaped, self-observing agent process
> whose behavior changes over time in traceable ways.

The architecture is trying to approximate:

- continuity,
- reflectiveness,
- differentiated values,
- narrative self-maintenance,
- and offline consolidation.

That is why the system feels meaningfully different from a single prompt loop.

But it is worth saying clearly what it is **not** doing:

- it is not demonstrating phenomenal consciousness;
- it is not proving that self-referential language equals awareness;
- it is not solving alignment;
- it is not a general-purpose reliable autonomous employee.

It is better understood as a **research scaffold** for studying what happens
when you combine persistence, memory graphs, metacognition, social structure,
and explicit safety mechanisms.

---

## Limitations and Failure Modes

A credible description should also name the weak points.

### 1. Prompt Dependence

Even with all the scaffolding, the system still depends heavily on prompt
quality and model behavior. Personality, reflection quality, and social
coherence remain partly fragile.

### 2. Simulated Selfhood Can Become Empty Performance

An agent can produce elegant introspection without grounding it in action. The
metacognitive layer helps detect that, but does not eliminate it.

### 3. More Structure Means More Complexity

Graph memory, trust pipelines, sleep mechanics, peer messaging, dashboarding,
and audit layers add real value, but they also add debugging burden and new
ways to fail.

### 4. Distinctness Is Partial, Not Absolute

The agents have different ethical vectors and temperaments, but they still share
the same underlying platform and often the same model families. Their
distinctness is real, but bounded.

### 5. Social Deliberation Can Drift into Endless Talk

Multi-agent systems can produce richer critique, but they can also produce
beautifully phrased stagnation. That is why grounding, calibration, and concrete
capabilities matter so much.

---

## Why This Architecture Is Interesting Anyway

Even with those limitations, AgentGolem is interesting because it takes several
ideas that are often discussed separately and combines them in one working
system:

- persistent identity;
- graph memory with provenance and trust;
- sleep-like replay and consolidation;
- metacognitive self-monitoring;
- differentiated multi-agent deliberation;
- explicit tool and approval boundaries;
- code-aware self-inspection and controlled self-evolution.

Most agent projects pick one or two of these. AgentGolem tries to hold them
together at once.

That does not make it "the answer" to autonomous cognition. But it does make it
a useful architecture for people who want to explore a more process-oriented and
less toy-like model of how agents might function over time.

---

## A Plain Summary

If you wanted to summarize AgentGolem in one sentence, a fair version would be:

> AgentGolem is a persistent seven-agent architecture that combines ethical
> formation, graph memory, trust-aware retrieval, metacognitive self-modeling,
> offline consolidation, and explicit tool use to simulate a more continuous and
> socially structured form of agent behavior than a standard chatbot or simple
> autonomous loop.

That is already ambitious enough. It does not need to be oversold.

