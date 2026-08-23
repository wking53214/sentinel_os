"""
Conservation Kernel integration for Sentinel OS.

Provides a mandatory conservation boundary ensuring that governed artifacts
passing from Sentinel to downstream systems (GSA-815) have been verified by
the Conservation Kernel.

This module implements PHASE 2-3 of the Sentinel → Conservation Kernel → GSA-815
integration, making conservation receipt a required envelope for artifact handoff.
"""

from .gateway import SentinelConservationGateway
from .receipt import ConservationReceipt
from .types import SentinelArtifact, ArtifactMetadata

__all__ = [
    "SentinelConservationGateway",
    "ConservationReceipt",
    "SentinelArtifact",
    "ArtifactMetadata",
]
