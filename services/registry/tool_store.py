"""
Tool Store - In-memory storage for tools and dependencies.
Version: 1.0

Single responsibility: Store and retrieve UnifiedToolDefinition objects.
"""

import logging
from typing import Dict, List, Optional, Set

from services.tool_contracts import UnifiedToolDefinition, DependencyGraph

logger = logging.getLogger(__name__)


class ToolStore:
    """
    In-memory storage for tools and their metadata.

    Responsibilities:
    - Store UnifiedToolDefinition objects by operation_id
    - Maintain categorization (retrieval vs mutation)
    - Store dependency graph
    - Provide lookup methods
    """

    def __init__(self):
        """Initialize empty store."""
        self.tools: Dict[str, UnifiedToolDefinition] = {}
        self.embeddings: Dict[str, List[float]] = {}
        self.dependency_graph: Dict[str, DependencyGraph] = {}

        # Categorization sets
        self.retrieval_tools: Set[str] = set()
        self.mutation_tools: Set[str] = set()

        logger.debug("ToolStore initialized")

    def add_tool(self, tool: UnifiedToolDefinition) -> None:
        """
        Add tool to store and categorize it.

        Args:
            tool: UnifiedToolDefinition to add
        """
        self.tools[tool.operation_id] = tool

        if tool.is_retrieval:
            self.retrieval_tools.add(tool.operation_id)
        if tool.is_mutation:
            self.mutation_tools.add(tool.operation_id)

    def get_tool(self, operation_id: str) -> Optional[UnifiedToolDefinition]:
        """Get tool by operation ID."""
        return self.tools.get(operation_id)

    def has_tool(self, operation_id: str) -> bool:
        """Check if tool exists."""
        return operation_id in self.tools

    def list_tools(self) -> List[str]:
        """List all tool operation IDs."""
        return list(self.tools.keys())

    def get_all_tools(self) -> Dict[str, UnifiedToolDefinition]:
        """Get all tools."""
        return self.tools

    def count(self) -> int:
        """Get total number of tools."""
        return len(self.tools)

    def add_embedding(self, operation_id: str, embedding: List[float]) -> None:
        """Add embedding for a tool."""
        self.embeddings[operation_id] = embedding

    def get_embedding(self, operation_id: str) -> Optional[List[float]]:
        """Get embedding for a tool."""
        return self.embeddings.get(operation_id)

    def has_embedding(self, operation_id: str) -> bool:
        """Check if embedding exists for tool."""
        return operation_id in self.embeddings

    def get_missing_embeddings(self) -> List[str]:
        """Get list of tools without embeddings."""
        return [
            op_id for op_id in self.tools
            if op_id not in self.embeddings
        ]

    def add_dependency(self, dep: DependencyGraph) -> None:
        """Add dependency graph entry."""
        self.dependency_graph[dep.tool_id] = dep

    def get_dependency(self, tool_id: str) -> Optional[DependencyGraph]:
        """Get dependency graph for a tool."""
        return self.dependency_graph.get(tool_id)

    def clear(self) -> None:
        """Clear all stored data."""
        self.tools.clear()
        self.embeddings.clear()
        self.dependency_graph.clear()
        self.retrieval_tools.clear()
        self.mutation_tools.clear()
        logger.debug("ToolStore cleared")

    def get_stats(self) -> Dict[str, int]:
        """Get store statistics."""
        return {
            "total_tools": len(self.tools),
            "retrieval_tools": len(self.retrieval_tools),
            "mutation_tools": len(self.mutation_tools),
            "embeddings": len(self.embeddings),
            "dependencies": len(self.dependency_graph)
        }
