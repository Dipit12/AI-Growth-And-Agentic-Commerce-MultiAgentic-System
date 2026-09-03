"""Qdrant client for product-catalog semantic search. Layer 4 (integrations).

Embedding model choice: Anthropic does not currently serve an embeddings endpoint, so this project
uses Voyage AI's `voyage-3` model (Anthropic's recommended embeddings partner, callable with the
same `ANTHROPIC_API_KEY`-adjacent `VOYAGE_API_KEY` pattern) via the `voyageai` client. If no Voyage
key is configured, this falls back to Ollama's local `nomic-embed-text` model so the demo still runs
fully offline. Only `discovery.py` and `recommender.py` read from this module; it never writes money.
"""

import uuid
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("vector_store")

COLLECTION_NAME = "products"
EMBEDDING_DIM = 1024  # voyage-3 output dimension; nomic-embed-text is truncated/padded to match


class VectorStore:
    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self._client = AsyncQdrantClient(url=url or settings.QDRANT_URL)

    async def create_collection(self) -> None:
        exists = await self._client.collection_exists(COLLECTION_NAME)
        if exists:
            return
        await self._client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qmodels.VectorParams(size=EMBEDDING_DIM, distance=qmodels.Distance.COSINE),
        )
        logger.info("vector_store.collection_created", collection=COLLECTION_NAME)

    async def upsert(self, product_id: uuid.UUID, text: str, metadata: dict[str, Any]) -> uuid.UUID:
        vector = await embed_text(text)
        point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"product:{product_id}")
        await self._client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                qmodels.PointStruct(
                    id=str(point_id),
                    vector=vector,
                    payload={"product_id": str(product_id), **metadata},
                )
            ],
        )
        return point_id

    async def search(
        self, query: str, filters: dict[str, Any] | None = None, k: int = 5
    ) -> list[dict[str, Any]]:
        vector = await embed_text(query)
        qdrant_filter = None
        if filters:
            qdrant_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))
                    for key, value in filters.items()
                ]
            )
        results = await self._client.search(
            collection_name=COLLECTION_NAME,
            query_vector=vector,
            query_filter=qdrant_filter,
            limit=k,
        )
        return [
            {"product_id": hit.payload.get("product_id"), "score": hit.score, **(hit.payload or {})}
            for hit in results
        ]


async def embed_text(text: str) -> list[float]:
    """Embeds a single string. Tries Voyage AI first, falls back to a local Ollama embedding model."""
    settings = get_settings()
    voyage_key = getattr(settings, "VOYAGE_API_KEY", "") or ""

    if voyage_key:
        import voyageai

        client = voyageai.AsyncClient(api_key=voyage_key)
        result = await client.embed([text], model="voyage-3", input_type="document")
        return list(result.embeddings[0])

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        response = await http_client.post(
            "http://localhost:11434/api/embeddings",
            json={"model": "nomic-embed-text", "prompt": text},
        )
        response.raise_for_status()
        embedding: list[float] = response.json()["embedding"]
        if len(embedding) < EMBEDDING_DIM:
            embedding = embedding + [0.0] * (EMBEDDING_DIM - len(embedding))
        return embedding[:EMBEDDING_DIM]
