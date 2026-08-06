"""Tests for domain-wide PBL command context classification."""

from custom_components.presence_based_lighting.command_context import (
    CommandOrigin,
    PresenceCommandContextRegistry,
)
from tests.conftest import MockContext


def test_classifies_own_and_sibling_exact_contexts():
    registry = PresenceCommandContextRegistry()
    registry.register("command", "entry_a", "light.shared", "on")
    context = MockContext("command")

    assert registry.classify(
        "entry_a",
        "light.shared",
        context,
        include_parent=False,
    ) == CommandOrigin.OWN
    assert registry.classify(
        "entry_b",
        "light.shared",
        context,
        include_parent=False,
    ) == CommandOrigin.SIBLING


def test_service_classification_follows_direct_parent_context():
    registry = PresenceCommandContextRegistry()
    registry.register("command", "entry_a", "light.shared", "on")
    child = MockContext("child", parent_id="command")

    assert registry.classify(
        "entry_b",
        "light.shared",
        child,
        include_parent=True,
    ) == CommandOrigin.SIBLING


def test_state_classification_follows_direct_parent_context():
    registry = PresenceCommandContextRegistry()
    registry.register("command", "entry_a", "light.shared", "on")
    child = MockContext("child", parent_id="command")

    assert registry.classify(
        "entry_b",
        "light.shared",
        child,
        include_parent=True,
    ) == CommandOrigin.SIBLING


def test_context_for_different_entity_is_external():
    registry = PresenceCommandContextRegistry()
    registry.register("command", "entry_a", "light.first", "on")

    assert registry.classify(
        "entry_b",
        "light.second",
        MockContext("command"),
        include_parent=True,
    ) == CommandOrigin.EXTERNAL


def test_registry_evicts_oldest_context_at_capacity():
    registry = PresenceCommandContextRegistry(max_contexts=2)
    registry.register("first", "entry_a", "light.shared", "on")
    registry.register("second", "entry_a", "light.shared", "on")
    registry.register("third", "entry_a", "light.shared", "on")

    assert registry.classify(
        "entry_a",
        "light.shared",
        MockContext("first"),
        include_parent=False,
    ) == CommandOrigin.EXTERNAL
    assert registry.classify(
        "entry_a",
        "light.shared",
        MockContext("third"),
        include_parent=False,
    ) == CommandOrigin.OWN
