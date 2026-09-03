"""Embeds every seeded product and upserts it into Qdrant. Layer 4 (integrations) helper script.

Run with: python -m scripts.index_catalog
Idempotent: point IDs are derived deterministically from the product UUID (uuid5), so re-running
overwrites rather than duplicates.
"""

import asyncio

from sqlalchemy import select

from app.db.session import async_session_factory
from app.integrations.vector_store import VectorStore
from app.logging_config import configure_logging, get_logger
from app.models.product import Product

logger = get_logger("index_catalog")


async def index_all() -> None:
    store = VectorStore()
    await store.create_collection()

    async with async_session_factory() as session:
        products = (await session.execute(select(Product))).scalars().all()

        for product in products:
            text = f"{product.name}. {product.description}. Category: {product.category}."
            embedding_id = await store.upsert(
                product.id,
                text,
                metadata={
                    "sku": product.sku,
                    "name": product.name,
                    "category": product.category,
                    "price_paise": product.price_paise,
                    "stock": product.stock,
                },
            )
            product.embedding_id = embedding_id

        await session.commit()
        logger.info("index_catalog.done", indexed=len(products))


if __name__ == "__main__":
    configure_logging()
    asyncio.run(index_all())
