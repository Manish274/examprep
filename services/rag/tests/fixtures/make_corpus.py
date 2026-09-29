"""Generates a larger study document for evaluation.

The small fixtures are right for parser and chunker tests, but useless for
measuring retrieval: with eight chunks and top_k=10 every strategy retrieves
the whole corpus and every metric is trivially 1.0.

This builds a document with enough distinct sections that retrieval has to
discriminate, and with the properties that make retrieval hard in real study
material: sections that share vocabulary but answer different questions,
acronyms that only exact matching finds, and concepts a student would ask about
in words the text never uses.

Run: python tests/fixtures/make_corpus.py
"""

from __future__ import annotations

from pathlib import Path

import fitz

HERE = Path(__file__).parent

# (heading, body). Deliberately overlapping vocabulary across sections -- if
# every section were about a different subject, retrieval would be easy and the
# measurement would flatter the system.
SECTIONS: list[tuple[str, str]] = [
    (
        "Relational Model Basics",
        "A relation is a set of tuples sharing the same attributes. Each attribute "
        "draws values from a domain. The degree of a relation is its number of "
        "attributes and the cardinality is its number of tuples. Unlike a file, a "
        "relation has no inherent ordering of rows.",
    ),
    (
        "Keys and Constraints",
        "A superkey is any set of attributes that uniquely identifies a tuple. A "
        "candidate key is a minimal superkey. The primary key is the candidate key "
        "chosen by the designer. A foreign key references the primary key of "
        "another relation and enforces referential integrity.",
    ),
    (
        "Functional Dependencies",
        "A functional dependency X to Y holds when any two tuples agreeing on X "
        "also agree on Y. Armstrong axioms of reflexivity, augmentation and "
        "transitivity generate every dependency implied by a given set. The closure "
        "of an attribute set is every attribute it determines.",
    ),
    (
        "First Normal Form",
        "A relation is in first normal form when every attribute holds only atomic "
        "values. Repeating groups and nested relations are not permitted. This is "
        "the minimum requirement for any relational table and most systems enforce "
        "it structurally.",
    ),
    (
        "Second Normal Form",
        "A relation is in second normal form when it is in 1NF and every non-prime "
        "attribute is fully functionally dependent on the whole of every candidate "
        "key. Partial dependencies on part of a composite key are eliminated at "
        "this stage.",
    ),
    (
        "Third Normal Form",
        "A relation is in third normal form when it is in 2NF and no non-prime "
        "attribute is transitively dependent on any candidate key. A transitive "
        "dependency means an attribute depends on another non-prime attribute "
        "rather than directly on the key.",
    ),
    (
        "Boyce Codd Normal Form",
        "BCNF is stricter than 3NF. Every determinant must be a candidate key. "
        "Most relations in 3NF are also in BCNF, but exceptions arise when a "
        "relation has multiple overlapping candidate keys. Decomposition into BCNF "
        "is not always dependency preserving.",
    ),
    (
        "Denormalization",
        "Denormalization deliberately reintroduces redundancy to reduce join cost "
        "on read-heavy workloads. It trades write amplification and integrity risk "
        "for query speed, and should follow measurement rather than precede it.",
    ),
    (
        "B-Tree Indexes",
        "A B-tree index is a balanced tree giving logarithmic lookup. It answers "
        "equality predicates and range predicates alike, which is why it is the "
        "default index type in most relational engines. Leaf nodes are linked to "
        "support ordered scans.",
    ),
    (
        "Hash Indexes",
        "A hash index answers equality lookups in near constant time but cannot "
        "answer range predicates at all, because hashing destroys ordering. It is "
        "rarely the right default despite its lookup advantage.",
    ),
    (
        "Composite Indexes",
        "A composite index covers several columns in a defined order. The leftmost "
        "prefix rule governs which queries can use it: a query filtering only on "
        "the second column cannot use an index whose first column is something "
        "else.",
    ),
    (
        "Covering Indexes",
        "A covering index contains every column a query needs, so the engine "
        "answers from the index alone without visiting the heap. This is often the "
        "single largest win available for a hot read path.",
    ),
    (
        "Query Planning",
        "The planner estimates the cost of alternative execution strategies using "
        "table statistics and picks the cheapest. Stale statistics are a common "
        "cause of a plan that was fast last month and is slow today.",
    ),
    (
        "Join Algorithms",
        "Nested loop join suits a small outer relation with an indexed inner one. "
        "Hash join suits large unsorted inputs on an equality predicate. Merge join "
        "suits inputs already sorted on the join column.",
    ),
    (
        "Transactions and ACID",
        "Atomicity means all or nothing. Consistency means constraints hold before "
        "and after. Isolation means concurrent transactions do not observe each "
        "other partially. Durability means a committed change survives a crash.",
    ),
    (
        "Isolation Levels",
        "Read uncommitted permits dirty reads. Read committed prevents them but "
        "permits non-repeatable reads. Repeatable read prevents those but may "
        "permit phantoms. Serializable prevents all three at the highest cost.",
    ),
    (
        "Locking and Deadlock",
        "Two-phase locking acquires all locks before releasing any, which "
        "guarantees serializability. Deadlock arises when transactions wait on each "
        "other cyclically, and is resolved by detection and victim rollback.",
    ),
    (
        "Write Ahead Logging",
        "A change is written to the log before it reaches the data pages, so a "
        "crash can be recovered by replaying committed work and undoing "
        "uncommitted work. This is what makes durability affordable.",
    ),
    (
        "Sharding",
        "Sharding partitions rows across independent nodes by a shard key. It "
        "scales write throughput but makes cross-shard queries and transactions "
        "expensive, so the shard key choice dominates the design.",
    ),
    (
        "Replication",
        "Replication copies data to follower nodes for read scaling and failure "
        "tolerance. Synchronous replication costs write latency and preserves "
        "consistency; asynchronous replication is faster but can lose recent "
        "commits on failover.",
    ),
]


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for word in text.split():
        if current and length + 1 + len(word) > width:
            lines.append(" ".join(current))
            current, length = [word], len(word)
        else:
            current.append(word)
            length += (1 if length else 0) + len(word)
    if current:
        lines.append(" ".join(current))
    return lines


def build(path: Path) -> int:
    doc = fitz.open()
    margin, bottom_limit = 72.0, 740.0
    page_index, y = 0, margin
    doc.new_page()

    def ensure_space(needed: float) -> None:
        nonlocal page_index, y
        if y + needed > bottom_limit:
            doc.new_page()
            page_index += 1
            y = margin

    # A document title, one size larger, so heading detection has a level 1 to
    # find above the section headings.
    doc[0].insert_text(
        (margin, y),
        "Database Systems Study Guide",
        fontsize=20,
        fontname="helvetica-bold",
    )
    y += 34

    for heading, body in SECTIONS:
        wrapped = _wrap(body, 82)
        ensure_space(24 + len(wrapped) * 15 + 10)

        y += 10
        doc[page_index].insert_text(
            (margin, y), heading, fontsize=14, fontname="helvetica-bold"
        )
        y += 21

        for line in wrapped:
            doc[page_index].insert_text(
                (margin, y), line, fontsize=11, fontname="helvetica"
            )
            y += 14.85
        y += 6

    doc.save(path)
    pages = doc.page_count
    doc.close()
    return pages


if __name__ == "__main__":
    target = HERE / "study-guide.pdf"
    pages = build(target)
    print(
        f"  {target.name}: {target.stat().st_size:,} bytes, {pages} pages, "
        f"{len(SECTIONS)} sections"
    )
