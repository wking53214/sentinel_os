"""The gateway no longer opts out of the kernel's root guard on its own.

`ConservationGateway` built its own kernel with `allow_unguarded_roots=True` whenever none was passed, so the opt-out
was a default nobody could see at the call site. Building a gateway that registers roots with no guard now has to say
so (`allow_unguarded_roots=True`), the way the kernel itself asks. Behaviour for a caller that says so is unchanged.
"""
import pytest
from conservation.transport import ConservationGateway, InvalidContract
from conservation_kernel import ConservationKernel, EvidenceRegistry


def test_a_gateway_that_builds_its_own_kernel_must_say_it_registers_roots_unguarded():
    with pytest.raises(InvalidContract, match="allow_unguarded_roots"):
        ConservationGateway(registry=EvidenceRegistry())
    with pytest.raises(InvalidContract, match="allow_unguarded_roots"):
        ConservationGateway()


def test_saying_so_builds_the_same_unguarded_gateway_as_before():
    gateway = ConservationGateway(registry=EvidenceRegistry(), allow_unguarded_roots=True)
    assert gateway.kernel.has_root_guard is False


def test_a_gateway_given_a_kernel_needs_no_flag_and_keeps_that_kernels_guard():
    kernel = ConservationKernel(registry=EvidenceRegistry(), root_guard=lambda artifact: False)
    gateway = ConservationGateway(kernel=kernel)
    assert gateway.kernel is kernel and gateway.kernel.has_root_guard is True


def test_the_flag_with_a_kernel_is_a_contradiction_and_is_refused():
    kernel = ConservationKernel(registry=EvidenceRegistry(), root_guard=lambda artifact: False)
    with pytest.raises(InvalidContract, match="allow_unguarded_roots"):
        ConservationGateway(kernel=kernel, allow_unguarded_roots=True)
