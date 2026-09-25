# Hybrid Graph Mesh

A **virtual hybrid graph layer** that bridges RDF (SPARQL) and Labelled Property Graph (Cypher)
stores. Nothing is persisted here: the mesh holds only schema mappings and connections, translates
one backend-neutral query into each store's native language, executes it where the data lives, and
stitches the answers back together — including joins that span an RDF triple store and a Neo4j graph.

Natural-language questions are parsed into the same intermediate representation, so
`"friends of Alice who work at Acme"` becomes Cypher for the social graph, SPARQL for the org
knowledge graph, and a single merged result set.

```
   "friends of Alice who work at Acme and are older than 30"
                    │  NL → Query IR (schema-driven grammar, optional LLM fallback)
                    ▼
        ┌──────────────────────┐
        │     Virtual Mesh     │   plan: pushdown or federated
        │  (no data stored)    │   align entities on identity property (e.g. name / owl:sameAs)
        └──────┬────────┬──────┘
   SPARQL      │        │      Cypher
               ▼        ▼
     RDF store(s)     LPG store(s)
   rdflib / any       Neo4j / Bolt /
   SPARQL endpoint    in-memory JSON
```

## Quick start

```bash
pip install -e ".[dev]"        # add ".[neo4j]" for a Bolt backend

hgm ask "friends of Alice who work at Acme"
hgm compile "employees of Acme older than 30"   # see the SPARQL + Cypher without running
hgm schema
hgm serve                                        # http://127.0.0.1:8000  (web console + JSON API)
pytest
```

Without `--config` the CLI loads the bundled demo: an RDF org chart (`demo/organizations.ttl`,
schema.org vocabulary) federated with an LPG social graph (`demo/social.json`). They overlap only on
`Person.name`, which is what the mesh uses to align entities.

```
$ hgm ask "friends of Alice who work at Acme"
interpretation: (n1)-[:KNOWS]->(n2) ; answer = n2 ; (n2)-[:WORKS_AT]->(n3) ; return n2

[orgs-rdf | federated | sparql]
SELECT DISTINCT ?n2 ?n3
WHERE {
  ?n3 <http://schema.org/name> "Acme" .
  ?n2 <http://schema.org/worksFor> ?n3 .
}

[social-lpg | federated | cypher]
MATCH (n1 {`name`: "Alice"})-[:`KNOWS`]-(n2)
RETURN DISTINCT n1 AS `n1`, n2 AS `n2`

2 row(s):
  n2: (Person name='Bob', age=28, jobTitle='Designer') <orgs-rdf,social-lpg>
  n2: (Person name='Erin', age=31, jobTitle='Engineer') <orgs-rdf,social-lpg>
```

## How it works

| Layer | Module | Role |
|---|---|---|
| Query IR | `ir.py` | `Query(nodes, edges, filters, returns, aggregate, order_by, limit)` — the lingua franca. Serialisable to/from JSON. |
| Compilers | `compilers/cypher.py`, `compilers/sparql.py` | Render IR to Cypher, or to SPARQL via a `SchemaMapping` (label → `rdf:type` class, property → predicate, relationship → predicate, incl. inverse predicates and symmetric paths). |
| Backends | `backends/` | `RdfBackend` (rdflib graph or remote SPARQL endpoint), `Neo4jBackend` (Bolt), `InMemoryLpgBackend` (evaluates IR directly; handy for tests/demo). All expose the same `labels() / relationships() / properties() / execute()` surface. |
| Mesh | `mesh.py` | **Pushdown** when one backend covers the whole pattern (results unioned and entity-merged). **Federated** otherwise: each edge is routed to a backend that owns its relationship type, filters/projections on properties only another store knows become node lookups, sub-results are hash-joined on shared variables using each backend's identity property, and count/order/limit are applied to the stitched result. |
| NL | `nl/` | `Vocabulary` is generated from the live mesh schema (+ optional synonyms), so the grammar only knows words that exist in your graphs. `RuleBasedTranslator` parses a *subject* clause plus any number of *qualifier* clauses. `LLMTranslator` (any `callable(prompt) -> str`) is an optional fallback via `HybridTranslator`. |
| Surface | `cli.py`, `api.py`, `console.html` | CLI, FastAPI (`/ask`, `/translate`, `/query`, `/sparql`, `/cypher`, `/schema`), and a small web console showing the plan for each backend. |

### Questions the grammar understands (schema-driven, examples from the demo)

```
find all people                        who works at Acme                 employees of Acme
friends of Alice                       who knows Bob                     where does Alice work
people older than 30                   companies founded before 2000     people whose job title is Engineer
people whose name contains "ar"        how many companies are there      how many people work at Acme
names of employees of Acme             people sorted by age descending   top 2 people sorted by age
friends of Alice who work at Acme      employees of Acme older than 30   people who know Alice and work at Globex
```

## Plugging in your own graphs

```jsonc
// mesh.json
{
  "backends": [
    {"name": "wikidata", "type": "rdf", "endpoint": "https://query.wikidata.org/sparql",
     "mapping": {
       "namespaces": {"wd": "http://www.wikidata.org/entity/", "wdt": "http://www.wikidata.org/prop/direct/"},
       "labels": {"Human": "wd:Q5"}, "properties": {"name": "http://www.w3.org/2000/01/rdf-schema#label"},
       "relationships": {"EMPLOYER": "wdt:P108"}
     }},
    {"name": "crm", "type": "neo4j", "uri": "bolt://localhost:7687", "user": "neo4j", "password": "${NEO4J_PASSWORD}"},
    {"name": "local", "type": "lpg-json", "file": "graph.json"}
  ],
  "synonyms": {"Human": ["person", "people"], "EMPLOYER": ["works for", "employed by"]},
  "inverse_synonyms": {"EMPLOYER": ["employees of"]},
  "symmetric": ["KNOWS"]
}
```

```bash
hgm --config mesh.json ask "who works for Acme"
```

Or in Python:

```python
from hybrid_graph_mesh import HybridGraph, RdfBackend, Neo4jBackend, SchemaMapping

g = HybridGraph([
    RdfBackend("kg", SchemaMapping(...), endpoint="http://triplestore:7200/repositories/kg"),
    Neo4jBackend("graph", "bolt://neo4j:7687", ("neo4j", "secret")),
], synonyms={...}, llm=my_llm_callable)

answer = g.ask("customers in Austin who bought products made by Acme")
answer.translation.query      # the IR
answer.results.plan           # SPARQL/Cypher per backend + strategy
answer.results.rows           # stitched rows; nodes carry `source="kg,graph"` when merged
```

## Design notes

* **Virtual by construction.** Backends are the only place data lives; the mesh keeps mappings,
  vocabulary and connections. Adding a store is adding a `Backend`.
* **One IR, many dialects.** Cypher and SPARQL are just renderers of the IR, so anything that produces
  IR (rules, LLM, a future GQL/Gremlin parser) automatically runs everywhere.
* **Entity alignment is pluggable.** Each backend declares an `identity_property`; the join uses it
  (falling back to node id). Swap in `owl:sameAs`-materialised keys or a resolution service by
  overriding `VirtualMesh._identity`.
* **Deterministic NL first.** The grammar is transparent (`translation.explanation` shows every
  clause it understood) and fails loudly with the known vocabulary; the LLM fallback is opt-in.

## Limitations / roadmap

* The in-memory LPG engine has no Cypher parser (`/cypher` needs a Neo4j backend).
* Federated joins are hash joins on identity; no cost-based ordering or bind-join pushdown yet.
* The rule grammar handles one answer variable per question (chained qualifiers, filters,
  aggregation, ordering). Multi-hop paths (`friends of friends`) and OPTIONAL/UNION are next.
