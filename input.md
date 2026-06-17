**Dependency Graph Retrieval**

*Smarter Context for Smarter Code*

A Novel Architecture for AI-Assisted Codebase Navigation

**Key Insight**

*\"Every file visible. Only what matters loaded.\"*

**1. The Problem**

AI coding assistants like Claude Code and GitHub Copilot have a
fundamental limitation in how they handle large codebases. Understanding
this problem is the first step toward solving it.

**1.1 How AI Assistants Load Context Today**

When you ask an AI assistant a question about your project, it does not
load all files upfront --- but it still struggles with knowing which
files to read. The AI discovers files reactively, reading them one by
one as it explores. This creates a cascading set of problems:

-   Each file read adds tokens to the context window, filling it up
    rapidly over a long session

-   As the context window fills, the quality of responses degrades
    significantly

-   The AI often misses important files because it does not know they
    exist until told explicitly

-   System instructions, tool descriptions and config files are
    auto-loaded before you even type, consuming valuable token budget

-   No understanding of how files relate to each other, so the AI cannot
    make smart decisions about what to load next

![Figure 1: The Problem --- All files loaded, 96% of tokens
wasted](./eac03d56198d55b1775f39da55efff06be91663f.png "Figure 1: The Problem — All files loaded, 96% of tokens wasted"){width="6.041666666666667in"
height="3.0208333333333335in"}

*Figure 1: The Problem --- All files loaded, 96% of tokens wasted*

**1.2 The Lost in the Middle Problem**

Research has shown that transformer-based LLMs perform significantly
worse at retrieving information buried in the middle of a long context
window. They tend to:

-   Remember information at the start of context well

-   Remember information at the end of context well

-   Forget or mishandle information buried in the middle

+-----------------------------------------------------------------------+
| **Real Impact**                                                       |
|                                                                       |
| In a project with 50 files averaging 500 tokens each, loading         |
| everything consumes 25,000 tokens. If your query only needed 2 files  |
| (1,000 tokens), you have wasted 96% of your context budget ---        |
| slowing responses, increasing costs, and reducing accuracy.           |
+-----------------------------------------------------------------------+

**2. The Idea: Dependency Graph Retrieval**

The core idea is to build a lightweight dependency graph of the entire
project before querying the AI. This graph acts as a map of the codebase
that the AI can traverse intelligently --- instead of blindly reading
files one by one.

**2.1 Graph Structure**

The graph has two components --- nodes representing files and edges
representing relationships between them.

![Figure 2: Dependency Graph Structure --- Nodes, Edges and
Weights](./430ed1af7744bd65891923d71cf1357b434f6b44.png "Figure 2: Dependency Graph Structure — Nodes, Edges and Weights"){width="6.041666666666667in"
height="3.3958333333333335in"}

*Figure 2: Dependency Graph Structure --- Nodes, Edges and Weights*

**Nodes --- What Each Node Contains**

-   File name --- the exact path and name of the file

-   One-line description --- a short AI-generated summary of what the
    file does

-   File type --- whether it is a controller, model, utility, config,
    test, etc.

-   Last modified timestamp --- helps prioritise recently changed files
    for bug-related queries

+-----------------------------------------------------------------------+
| **Important**                                                         |
|                                                                       |
| Nodes contain only metadata --- not the full file content. This is    |
| what keeps the graph lightweight. The full file content is only       |
| loaded when the AI decides that node is relevant to the query.        |
+-----------------------------------------------------------------------+

**Edges --- What Each Edge Represents**

-   Import relationships --- file A imports from file B

-   Function calls --- file A calls a function defined in file B

-   Class inheritance --- file A extends a class from file B

-   Configuration dependencies --- file A is configured by file B

**Edge Weights --- How Strongly Are Files Connected?**

Each edge carries a weight from 1 to 10 representing the strength of
dependency:

-   Weight 8-10 --- heavily dependent, almost always need both files
    together

-   Weight 4-7 --- moderately dependent, often relevant together

-   Weight 1-3 --- loosely connected, rarely need to load both

**3. How It Works**

**3.1 Query Traversal**

When a developer submits a query the AI enters the graph and traverses
it using metadata and edge weights --- not full file contents. Here is
the step-by-step flow:

![Figure 3: Query Traversal --- AI follows weighted edges to find only
relevant
files](./2e6c72f9b80ed6f4be837dbe607bdc8798c2770c.png "Figure 3: Query Traversal — AI follows weighted edges to find only relevant files"){width="6.041666666666667in"
height="3.0208333333333335in"}

*Figure 3: Query Traversal --- AI follows weighted edges to find only
relevant files*

1.  Query received --- developer asks a question about the codebase

2.  Graph entry --- AI reads only node metadata (not full files),
    scanning one-line descriptions

3.  Edge traversal --- AI follows thick edges (high weight) and skips
    thin edges (low weight)

4.  Node selection --- AI identifies the 2-5 most relevant files based
    on metadata and weights

5.  Focused load --- only selected files are fully loaded into the
    context window

**3.2 Solving the Missing File Problem**

This is one of the most important advantages of this approach. Because
every single file is represented as a node in the graph --- even before
any file is loaded --- the AI always knows the full picture of the
codebase.

+-----------------------------------------------------------------------+
| **Problem Solved**                                                    |
|                                                                       |
| In current AI assistants, the AI sometimes misses important files     |
| simply because it never explored that part of the codebase. With the  |
| dependency graph, every file is visible through metadata from the     |
| very beginning. The AI can see auth_middleware.py exists and what it  |
| does --- even if it has not read a single line of it yet.             |
+-----------------------------------------------------------------------+

**4. Comparison With Existing Approaches**

Here is how Dependency Graph Retrieval compares against the two most
common approaches used today:

![Figure 4: Feature Comparison --- Full Load vs RAG vs Dependency
Graph](./43c80ceca4b98e9d8261511957e0fab9277f4b2a.png "Figure 4: Feature Comparison — Full Load vs RAG vs Dependency Graph"){width="6.041666666666667in"
height="2.5520833333333335in"}

*Figure 4: Feature Comparison --- Full Load vs RAG vs Dependency Graph*

  --------------------- --------------- --------------- -------------------
  **Feature**           **Full Context  **RAG**         **Dependency
                        Load**                          Graph**

  Knows all files exist **❌ No**       **❌ No**       **✅ Yes**

  Token efficient       **❌ No**       **✅ Good**     **✅ Better**

  Understands file      **❌ No**       **❌ No**       **✅ Yes**
  structure                                             

  Misses related files  **❌ Often**    ⚠ Sometimes     **✅ Never**

  Works for large       **❌ No**       ⚠ Partially     **✅ Yes**
  projects                                              

  Setup complexity      **✅ Low**      ⚠ Medium        ⚠ Medium

  Cost per query        **❌ High**     **✅ Low**      **✅ Lowest**
  --------------------- --------------- --------------- -------------------

**5. What This Solves**

-   **AI always knows every file exists through graph metadata --- the
    missing file problem is eliminated**

-   Only relevant files are loaded, keeping the context window small and
    focused

-   Token usage drops dramatically --- graph metadata is a fraction of
    full file content

-   Faster responses because the AI is not wading through irrelevant
    code

-   More accurate answers because context is focused, not diluted

-   Lower API costs since fewer tokens are consumed per query

-   Context stays clean throughout the entire session regardless of
    project size

**6. Open Challenges**

No solution is without tradeoffs. Here are the honest challenges that
need to be worked through:

**6.1 Graph Construction**

Building the graph requires static analysis of the codebase. This works
well for statically typed languages like Java or TypeScript but is
harder for dynamic languages like Python where imports and calls can
happen at runtime.

**6.2 Graph Staleness**

Every time a file changes, edges may need to be updated. A naive full
rebuild on every change would be slow. Incremental updates are needed
--- only re-analysing files that changed and their direct neighbours.

**6.3 Traversal Depth**

How deep should the AI traverse? File A imports B, B imports C, C
imports D. Loading all of them may bring back the same token problem.
Edge weights can act as a cutoff threshold --- stop traversal when
cumulative weight drops below a threshold.

**6.4 Cross-cutting Files**

Config files, environment variables and constants affect almost
everything but are not always explicitly imported. These need to be
handled as high-weight nodes connected to everything, or flagged
separately as always-include files.

**7. Future Improvements**

-   Semantic metadata --- use AI to generate richer one-paragraph
    summaries per node, not just one-liners

-   Query-aware traversal --- weight traversal differently based on
    query type (bug fix vs feature vs refactor)

-   Bidirectional edges --- track both directions (A imports B and B is
    imported by A)

-   Confidence scoring --- AI assigns a relevance confidence before
    deciding to load a file fully

-   Incremental graph updates --- rebuild only affected subgraphs on
    file changes

-   Integration with LSP --- use Language Server Protocol data to build
    more accurate edges

**8. Summary**

Dependency Graph Retrieval is a purpose-built solution for the specific
problem of context management in AI-assisted software development. It
sits at the intersection of static code analysis and LLM context
management --- an area that is currently underexplored.

+-----------------------------------------------------------------------+
| **One Line Summary**                                                  |
|                                                                       |
| Build a weighted dependency graph of your codebase where nodes hold   |
| file metadata and edges hold relationship weights --- so the AI can   |
| traverse the graph to load only what it needs, while always knowing   |
| everything that exists.                                               |
+-----------------------------------------------------------------------+

**Dependency Graph Retrieval --- Smarter Context for Smarter Code**

*\"Every file visible. Only what matters loaded.\"*
