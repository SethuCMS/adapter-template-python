"""Talks to the real gRPC server over a local socket, the way the gateway does."""
import grpc
import pytest

from my_adapter.base import Adapter, AdapterError, Capabilities
from my_adapter.example import InMemoryAdapter
from my_adapter.generated import adapter_pb2 as pb
from my_adapter.generated import adapter_pb2_grpc as pb_grpc
from my_adapter.server import build_server
from my_adapter.servicer import to_dict, to_struct


@pytest.fixture()
def rig():
    server, port, service = build_server(InMemoryAdapter, "memory", "127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield pb_grpc.AdapterStub(channel), service
    channel.close()
    server.stop(0)
    service.close_all()


def connect(stub, read_only=False, connection_id="c1"):
    return stub.Connect(pb.ConnectRequest(connection_id=connection_id, adapter_type="memory", read_only=read_only,
                                          credentials=to_struct({"password": "s3cret"})))


def create(stub, **values):
    return stub.Create(pb.WriteRequest(connection_id="c1", collection="items", values=to_struct(values)))


def find(stub, query):
    return stub.Find(pb.FindRequest(connection_id="c1", query=to_struct({"collection": "items", **query})))


def test_connect_introspect_and_capabilities(rig):
    stub, _ = rig
    assert connect(stub).ok
    collections = stub.Introspect(pb.IntrospectRequest(connection_id="c1")).collections
    assert collections[0].native_name == "items"
    assert [f.native_name for f in collections[0].fields if f.is_primary_key] == ["id"]
    caps = stub.Capabilities(pb.CapabilitiesRequest(connection_id="c1"))
    assert caps.writes is True and caps.filter_or is True and caps.joins is False


def test_create_get_update_delete_round_trip(rig):
    stub, _ = rig
    connect(stub)
    created = to_dict(create(stub, title="First", score=1).record)
    assert created["title"] == "First" and created["id"] == 1
    got = stub.Get(pb.GetRequest(connection_id="c1", collection="items", id="1"))
    assert to_dict(got.record)["title"] == "First"
    updated = stub.Update(pb.WriteRequest(connection_id="c1", collection="items", id="1", values=to_struct({"score": 5})))
    assert to_dict(updated.record)["score"] == 5
    assert stub.Delete(pb.DeleteRequest(connection_id="c1", collection="items", id="1")).ok
    missing = stub.Get(pb.GetRequest(connection_id="c1", collection="items", id="1"))
    assert missing.error.code == "NOT_FOUND"
    assert stub.Delete(pb.DeleteRequest(connection_id="c1", collection="items", id="1")).error.code == "NOT_FOUND"


def test_pagination_returns_every_record_exactly_once(rig):
    stub, _ = rig
    connect(stub)
    for i in range(7):
        create(stub, title=f"t{i}", score=i)
    seen, cursor = [], ""
    for _ in range(10):
        response = find(stub, {"page": {"limit": 3, "cursor": cursor}})
        seen += [to_dict(r)["title"] for r in response.records]
        cursor = response.next_cursor
        if not cursor:
            break
    assert seen == [f"t{i}" for i in range(7)]


def test_filters_sorting_and_projection(rig):
    stub, _ = rig
    connect(stub)
    for title, score, status in [("a", 3, "x"), ("b", 1, "y"), ("c", 2, "x"), ("d", None, "y")]:
        create(stub, title=title, score=score, status=status)
    f = {"and": [{"field": "status", "op": "eq", "value": "x"}, {"field": "score", "op": "gte", "value": 2}]}
    assert [to_dict(r)["title"] for r in find(stub, {"filter": f, "sort": [{"field": "score", "dir": "desc"}]}).records] == ["a", "c"]
    either = {"or": [{"field": "title", "op": "eq", "value": "a"}, {"field": "title", "op": "startsWith", "value": "B"}]}
    assert [to_dict(r)["title"] for r in find(stub, {"filter": either}).records] == ["a", "b"]
    assert [to_dict(r)["title"] for r in find(stub, {"filter": {"not": {"field": "score", "op": "exists", "value": True}}}).records] == ["d"]
    projected = find(stub, {"projection": ["title"], "page": {"limit": 1}}).records[0]
    assert set(to_dict(projected)) == {"title"}


def test_unknown_names_are_refused_not_ignored(rig):
    stub, _ = rig
    connect(stub)
    bad_field = find(stub, {"filter": {"field": "nope", "op": "eq", "value": 1}})
    assert bad_field.error.code == "VALIDATION_FAILED"
    assert find(stub, {"sort": [{"field": "nope"}]}).error.code == "VALIDATION_FAILED"
    assert create(stub, title="x", nope=1).error.code == "VALIDATION_FAILED"
    other = stub.Find(pb.FindRequest(connection_id="c1", query=to_struct({"collection": "users"})))
    assert other.error.code == "VALIDATION_FAILED"
    assert find(stub, {"filter": {"field": "title", "op": "regex", "value": ".*"}}).error.code == "CAPABILITY_UNSUPPORTED"


def test_hostile_values_are_just_data(rig):
    stub, _ = rig
    connect(stub)
    create(stub, title="'; DROP TABLE items; --")
    hit = find(stub, {"filter": {"field": "title", "op": "contains", "value": "DROP TABLE"}})
    assert len(hit.records) == 1
    assert find(stub, {"filter": {"field": "title", "op": "eq", "value": {"$ne": None}}}).records == []


def test_read_only_connections_reject_every_write(rig):
    stub, _ = rig
    connect(stub, read_only=True)
    assert create(stub, title="x").error.code == "READ_ONLY"
    assert stub.Update(pb.WriteRequest(connection_id="c1", collection="items", id="1", values=to_struct({"title": "y"}))).error.code == "READ_ONLY"
    assert stub.Delete(pb.DeleteRequest(connection_id="c1", collection="items", id="1")).error.code == "READ_ONLY"
    assert stub.Capabilities(pb.CapabilitiesRequest(connection_id="c1")).writes is False


def test_validation_of_written_records(rig):
    stub, _ = rig
    connect(stub)
    assert create(stub).error.code == "VALIDATION_FAILED"                   # title required
    assert create(stub, title="x" * 201).error.code == "VALIDATION_FAILED"  # too long
    assert create(stub, title="x", id=5).error.code == "VALIDATION_FAILED"  # id is generated
    assert create(stub, title="x", score="high").error.code == "VALIDATION_FAILED"


def test_bad_cursors_and_ids_are_handled(rig):
    stub, _ = rig
    connect(stub)
    assert find(stub, {"page": {"cursor": "not-a-number"}}).error.code == "VALIDATION_FAILED"
    assert find(stub, {"page": {"cursor": "-5"}}).error.code == "VALIDATION_FAILED"
    assert stub.Get(pb.GetRequest(connection_id="c1", collection="items", id="1; DROP")).error.code == "NOT_FOUND"


def test_unexpected_failures_become_a_generic_internal_error(rig):
    stub, service = rig

    class Broken(InMemoryAdapter):
        def find(self, query):
            raise RuntimeError("password=hunter2 host=10.0.0.5")

    service._factory = Broken
    connect(stub)
    response = find(stub, {})
    assert response.error.code == "INTERNAL"
    assert "hunter2" not in response.error.message and "10.0.0.5" not in response.error.message


def test_calls_before_connect_and_after_close_fail_cleanly(rig):
    stub, _ = rig
    assert find(stub, {}).error.code == "CONNECTION_FAILED"
    connect(stub)
    assert stub.Close(pb.CloseRequest(connection_id="c1")).ok
    assert find(stub, {}).error.code == "CONNECTION_FAILED"


def test_wrong_adapter_type_is_refused(rig):
    stub, _ = rig
    response = stub.Connect(pb.ConnectRequest(connection_id="c1", adapter_type="postgres"))
    assert not response.ok and response.error.code == "VALIDATION_FAILED"


def test_connections_do_not_share_data(rig):
    stub, _ = rig
    connect(stub, connection_id="c1")
    connect(stub, connection_id="c2")
    create(stub, title="only in c1")
    other = stub.Find(pb.FindRequest(connection_id="c2", query=to_struct({"collection": "items"})))
    assert len(other.records) == 0


def test_batch_and_subscribe_are_declared_unsupported(rig):
    stub, _ = rig
    assert stub.Batch(pb.BatchRequest(connection_id="c1")).error.code == "CAPABILITY_UNSUPPORTED"
    with pytest.raises(grpc.RpcError) as caught:
        list(stub.Subscribe(pb.FindRequest(connection_id="c1")))
    assert caught.value.code() == grpc.StatusCode.UNIMPLEMENTED


def test_plain_text_server_refuses_public_addresses():
    with pytest.raises(ValueError):
        build_server(InMemoryAdapter, "memory", "0.0.0.0:50051")


def test_adapter_error_rejects_unknown_codes():
    with pytest.raises(ValueError):
        AdapterError("WHATEVER", "x")
