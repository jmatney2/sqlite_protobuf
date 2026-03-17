"""
Helpers for creating random Person protobuf messages without requiring
protoc-generated Python stubs.  Uses the descriptor-based API from
google.protobuf directly.
"""

import random
import time
from functools import lru_cache
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorSet
from google.protobuf.descriptor_pool import DescriptorPool
from google.protobuf.message_factory import GetMessageClass

_DESCRIPTOR_PATH = Path(__file__).parent / "descriptors" / "test.pb"

# status enum values matching test.proto
STATUS_UNKNOWN = 0
STATUS_ACTIVE = 1
STATUS_INACTIVE = 2

_FIRST_NAMES = [
    "Alice", "Bob", "Carol", "David", "Eve", "Frank", "Grace",
    "Henry", "Iris", "Jack", "Kate", "Leo", "Mia", "Nina",
    "Oscar", "Pam", "Quinn", "Ray", "Sam", "Tina",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones",
    "Garcia", "Miller", "Davis", "Wilson", "Taylor",
]
_STREETS = [
    "Main St", "Oak Ave", "Elm Dr", "Park Blvd",
    "Lake Rd", "Hill St", "Forest Ave", "River Ln",
]
_CITIES = [
    "Springfield", "Shelbyville", "Capital City",
    "Ogdenville", "North Haverbrook", "Brockway",
]
_COUNTRIES = ["US", "UK", "CA", "AU", "DE", "FR", "JP"]
_TAGS = [
    "developer", "manager", "analyst", "designer",
    "engineer", "writer", "researcher", "consultant",
]
_NICKNAMES = [
    "Ace", "Buddy", "Chief", "Doc", "Flash",
    "Guru", "Hawk", "Jet", "Maverick", "Scout",
]

_LABELS_A = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
_LABELS_B = ["north", "south", "east", "west", "central", "outer", "inner", "upper"]
_CATEGORIES = ["news", "tech", "sports", "arts", "science", "politics", "finance", "health"]


@lru_cache(maxsize=1)
def _get_classes() -> dict:
    fds = FileDescriptorSet()
    fds.ParseFromString(_DESCRIPTOR_PATH.read_bytes())
    pool = DescriptorPool()
    for file_proto in fds.file:
        pool.Add(file_proto)
    return {
        "Person": GetMessageClass(pool.FindMessageTypeByName("test.Person")),
        "Address": GetMessageClass(pool.FindMessageTypeByName("test.Address")),
        "Record": GetMessageClass(pool.FindMessageTypeByName("test.Record")),
        "BranchA": GetMessageClass(pool.FindMessageTypeByName("test.BranchA")),
        "BranchB": GetMessageClass(pool.FindMessageTypeByName("test.BranchB")),
    }


def _random_address(classes):
    addr = classes["Address"]()
    addr.street = f"{random.randint(1, 999)} {random.choice(_STREETS)}"
    addr.city = random.choice(_CITIES)
    addr.zip_code = random.randint(10000, 99999)
    addr.country = random.choice(_COUNTRIES)
    return addr


def make_random_person() -> bytes:
    classes = _get_classes()
    Person = classes["Person"]

    p = Person()
    p.name = f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    p.age = random.randint(18, 75)
    p.score = round(random.uniform(1.0, 10.0), 2)
    p.temperature = round(random.uniform(36.0, 39.5), 1)
    p.active = random.random() > 0.3
    p.avatar = bytes(random.getrandbits(8) for _ in range(8))
    p.address.CopyFrom(_random_address(classes))
    p.status = random.choice([STATUS_UNKNOWN, STATUS_ACTIVE, STATUS_INACTIVE])
    p.created_at = int(time.time()) - random.randint(0, 365 * 24 * 3600)
    p.created_ts.seconds = int(time.time()) - random.randint(0, 365 * 24 * 3600)
    p.large_id = random.randint(0, 2**48)

    num_tags = random.randint(1, 4)
    p.tags.extend(random.sample(_TAGS, num_tags))

    p.metadata["views"] = random.randint(0, 10_000)
    p.metadata["likes"] = random.randint(0, 1_000)

    if random.random() > 0.5:
        p.nickname = random.choice(_NICKNAMES)

    num_prev = random.randint(0, 2)
    for _ in range(num_prev):
        p.previous_addresses.append(_random_address(classes))

    return p.SerializeToString()


def make_random_record() -> bytes:
    """
    Generate a random ``test.Record`` blob.

    Randomly sets either the ``branch_a`` or ``branch_b`` oneof member,
    demonstrating the polymorphic-row pattern.
    """
    classes = _get_classes()
    r = classes["Record"]()
    if random.random() > 0.5:
        a = classes["BranchA"]()
        a.label = random.choice(_LABELS_A)
        a.value = random.randint(1, 100)
        r.branch_a.CopyFrom(a)
    else:
        b = classes["BranchB"]()
        b.label = random.choice(_LABELS_B)
        b.category = random.choice(_CATEGORIES)
        r.branch_b.CopyFrom(b)
    return r.SerializeToString()
