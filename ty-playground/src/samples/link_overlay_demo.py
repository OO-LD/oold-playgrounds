"""Link resolution overlay demo.

Enable "link overlay" in the header, then press Run. The values shown inline
next to each dereference are fetched from the backend at that moment; they are
not written on those lines.
"""

from oold.backend.document_store import SimpleDictDocumentStore
from oold.backend.interface import SetResolverParam, set_resolver
from oold.model import LinkedBaseModel, LinkList, OoldField


class Person(LinkedBaseModel):
    id: str
    name: str | None = None
    type: str | None = "ex:Person"
    knows: LinkList["Person"] = OoldField()


Person.model_rebuild()

store = SimpleDictDocumentStore()
store.store_json_dicts({
    "ex:bob": {"id": "ex:bob", "name": "Bob", "type": "ex:Person"},
    "ex:carol": {"id": "ex:carol", "name": "Carol", "type": "ex:Person"},
    "ex:dave": {"id": "ex:dave", "name": "Dave", "type": "ex:Person"},
})
set_resolver(SetResolverParam(iri="ex", resolver=store))

alice = Person(id="ex:alice", name="Alice", knows=["ex:bob", "ex:carol", "ex:dave"])

# Three IRIs collapse into one backend call, so there is no N+1 fan-out.
print("first  ->", [person.name for person in alice.knows])

# Drop the per-instance cache. The Ref level cache still holds the objects, so
# this dereference reports zero backend calls.
del alice.__dict__["knows"]
print("second ->", [person.name for person in alice.knows])

# A second subject has its own refs, so its links are fetched again.
bob_fan = Person(id="ex:eve", name="Eve", knows=["ex:bob"])
print("third  ->", [person.name for person in bob_fan.knows])
