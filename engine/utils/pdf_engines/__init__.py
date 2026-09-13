"""
ABOUTME: PDF generation engines with strategy pattern for extensibility
ABOUTME: Provides abstract interface and multiple rendering engine implementations
"""

from .base import EngineResult, PDFEngine, PDFGenerationOptions
from .factory import PDFEngineFactory, get_available_engines, get_recommended_engine

__all__ = [
    "PDFEngine",
    "PDFGenerationOptions",
    "EngineResult",
    "PDFEngineFactory",
    "get_available_engines",
    "get_recommended_engine",
]
