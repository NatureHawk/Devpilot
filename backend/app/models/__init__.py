"""SQLAlchemy models.

Imported as a package so that ``Base.metadata`` is fully populated before
Alembic autogenerate or ``create_all`` runs.
"""

from app.models.conversation import Conversation, Message, MessageRole
from app.models.embedding import EMBEDDING_DIMENSIONS, ChunkEmbedding
from app.models.repository import IndexingStatus, Repository, RepositoryVisibility
from app.models.source import ChunkType, CodeChunk, SourceFile
from app.models.user import User

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "ChunkEmbedding",
    "ChunkType",
    "CodeChunk",
    "Conversation",
    "IndexingStatus",
    "Message",
    "MessageRole",
    "Repository",
    "RepositoryVisibility",
    "SourceFile",
    "User",
]
