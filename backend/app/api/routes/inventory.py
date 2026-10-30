import uuid
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import Auth, DbSession, Paging
from app.api.serializers import batches_out, movements_out, tenant_today
from app.models.catalog import Product, Supplier
from app.models.enums import ExpiryDateStatus, ExpiryStatus
from app.models.inventory import InventoryBatch, InventoryMovement
from app.schemas.common import Page
from app.schemas.inventory import (
    BatchCreate,
    BatchOut,
    BatchUpdate,
    MovementOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
    StockAdjustment,
    SupplierOut,
)
from app.services import inventory_service as inv
from app.services.audit_service import record_audit
from app.services.expiry_status import expiry_status_sql

router = APIRouter(tags=["inventory"])


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def _product_out(db, ctx, products: list[Product]) -> list[ProductOut]:
    summary = inv.product_stock_summary(db, ctx.business_id, [p.id for p in products])
    out = []
    for p in products:
        item = ProductOut.model_validate(p)
        item.active_batches, item.total_quantity = summary.get(p.id, (0, 0))
        out.append(item)
    return out


@router.post("/api/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, ctx: Auth, db: DbSession) -> ProductOut:
    product = inv.create_product(db, ctx.business_id, payload)
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="product.created",
        entity_type="product",
        entity_id=product.id,
    )
    db.commit()
    return _product_out(db, ctx, [product])[0]


@router.get("/api/products", response_model=Page[ProductOut])
def list_products(
    ctx: Auth,
    db: DbSession,
    paging: Paging,
    q: Annotated[str | None, Query(max_length=100)] = None,
    barcode: Annotated[str | None, Query(max_length=64)] = None,
) -> Page[ProductOut]:
    stmt = select(Product).where(Product.business_id == ctx.business_id)
    if q:
        stmt = stmt.where(or_(Product.name.ilike(f"%{q}%"), Product.brand.ilike(f"%{q}%")))
    if barcode:
        stmt = stmt.where(Product.barcode == barcode)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    products = db.scalars(stmt.order_by(Product.name).offset(paging.offset).limit(paging.page_size)).all()
    return Page(items=_product_out(db, ctx, list(products)), total=total, page=paging.page, page_size=paging.page_size)


@router.get("/api/products/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, ctx: Auth, db: DbSession) -> ProductOut:
    return _product_out(db, ctx, [inv.get_owned(db, Product, product_id, ctx.business_id)])[0]


@router.patch("/api/products/{product_id}", response_model=ProductOut)
def update_product(product_id: uuid.UUID, payload: ProductUpdate, ctx: Auth, db: DbSession) -> ProductOut:
    product = inv.get_owned(db, Product, product_id, ctx.business_id)
    changes = payload.model_dump(exclude_unset=True)
    inv.update_product(db, product, changes)
    record_audit(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        action="product.updated",
        entity_type="product",
        entity_id=product.id,
        details={k: str(v) for k, v in changes.items()},
    )
    db.commit()
    return _product_out(db, ctx, [product])[0]


@router.get("/api/suppliers", response_model=list[SupplierOut])
def list_suppliers(ctx: Auth, db: DbSession) -> list[SupplierOut]:
    rows = db.scalars(select(Supplier).where(Supplier.business_id == ctx.business_id).order_by(Supplier.name)).all()
    return [SupplierOut.model_validate(s) for s in rows]


@router.get("/api/categories", response_model=list[str])
def list_categories(ctx: Auth, db: DbSession) -> list[str]:
    rows = db.scalars(
        select(Product.category)
        .where(Product.business_id == ctx.business_id, Product.category.is_not(None))
        .distinct()
        .order_by(Product.category)
    ).all()
    return [r for r in rows if r]


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

StatusFilter = Literal[
    "active",
    "all",
    "depleted",
    "no_expiry_date",
    "requires_confirmation",
    "expired",
    "expiring_today",
    "expiring_soon",
    "fresh",
]
SortOption = Literal["expiry_asc", "expiry_desc", "received_desc", "name_asc", "quantity_asc"]


@router.get("/api/inventory/batches", response_model=Page[BatchOut])
def list_batches(
    ctx: Auth,
    db: DbSession,
    paging: Paging,
    q: Annotated[str | None, Query(max_length=100, description="Search product name, brand or batch number")] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    supplier_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "active",
    expires_within_days: Annotated[int | None, Query(ge=0, le=365, description="Confirmed dates within N days")] = None,
    sort: SortOption = "expiry_asc",
) -> Page[BatchOut]:
    today, soon_days = tenant_today(db, ctx.business)
    status_expr = expiry_status_sql(today, soon_days)
    stmt = (
        select(InventoryBatch)
        .join(Product, Product.id == InventoryBatch.product_id)
        .where(InventoryBatch.business_id == ctx.business_id)
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Product.name.ilike(like), Product.brand.ilike(like), InventoryBatch.batch_number.ilike(like))
        )
    if category:
        stmt = stmt.where(Product.category == category)
    if supplier_id:
        stmt = stmt.where(InventoryBatch.supplier_id == supplier_id)
    if product_id:
        stmt = stmt.where(InventoryBatch.product_id == product_id)
    if invoice_id:
        stmt = stmt.where(InventoryBatch.invoice_id == invoice_id)
    if status_filter == "active":
        stmt = stmt.where(InventoryBatch.current_quantity > 0)
    elif status_filter != "all":
        stmt = stmt.where(status_expr == status_filter)
    if expires_within_days is not None:
        stmt = stmt.where(
            InventoryBatch.current_quantity > 0,
            InventoryBatch.expiry_date_status == ExpiryDateStatus.CONFIRMED,
            InventoryBatch.expiry_date >= today,
            InventoryBatch.expiry_date <= today + timedelta(days=expires_within_days),
        )

    total = db.scalar(select(func.count()).select_from(stmt.with_only_columns(InventoryBatch.id).subquery())) or 0
    order = {
        "expiry_asc": [InventoryBatch.expiry_date.asc().nulls_last(), Product.name],
        "expiry_desc": [InventoryBatch.expiry_date.desc().nulls_last(), Product.name],
        "received_desc": [InventoryBatch.received_date.desc(), InventoryBatch.created_at.desc()],
        "name_asc": [Product.name, InventoryBatch.expiry_date.asc().nulls_last()],
        "quantity_asc": [InventoryBatch.current_quantity.asc(), Product.name],
    }[sort]
    batches = (
        db.scalars(stmt.order_by(*order, InventoryBatch.id).offset(paging.offset).limit(paging.page_size))
        .unique()
        .all()
    )
    return Page(
        items=batches_out(db, list(batches), today, soon_days),
        total=total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.post("/api/inventory/batches", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(payload: BatchCreate, ctx: Auth, db: DbSession) -> BatchOut:
    if payload.product_id:
        product = inv.get_owned(db, Product, payload.product_id, ctx.business_id)
    else:
        assert payload.new_product is not None
        product = inv.create_product(db, ctx.business_id, payload.new_product, reuse_existing=True)
    batch = inv.create_batch(
        db,
        business_id=ctx.business_id,
        user_id=ctx.user_id,
        product=product,
        quantity=payload.quantity,
        unit=payload.unit,
        supplier=inv.get_or_create_supplier(db, ctx.business_id, payload.supplier_name),
        pack_size=payload.pack_size,
        pack_unit=payload.pack_unit,
        batch_number=payload.batch_number,
        manufacturing_date=payload.manufacturing_date,
        expiry_date=payload.expiry_date,
        expiry_type=payload.expiry_type,
        expiry_confirmed=payload.expiry_confirmed,
        purchase_price=payload.purchase_price,
        received_date=payload.received_date,
        notes=payload.notes,
        reason="Added manually",
    )
    db.commit()
    today, soon_days = tenant_today(db, ctx.business)
    return batches_out(db, [batch], today, soon_days)[0]


@router.get("/api/inventory/batches/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: uuid.UUID, ctx: Auth, db: DbSession) -> BatchOut:
    batch = inv.get_owned(db, InventoryBatch, batch_id, ctx.business_id)
    today, soon_days = tenant_today(db, ctx.business)
    return batches_out(db, [batch], today, soon_days)[0]


@router.patch("/api/inventory/batches/{batch_id}", response_model=BatchOut)
def update_batch(batch_id: uuid.UUID, payload: BatchUpdate, ctx: Auth, db: DbSession) -> BatchOut:
    batch = inv.update_batch(db, business_id=ctx.business_id, user_id=ctx.user_id, batch_id=batch_id, data=payload)
    db.commit()
    db.refresh(batch)
    today, soon_days = tenant_today(db, ctx.business)
    return batches_out(db, [batch], today, soon_days)[0]


@router.post("/api/inventory/batches/{batch_id}/adjust-stock", response_model=BatchOut)
def adjust_stock(batch_id: uuid.UUID, payload: StockAdjustment, ctx: Auth, db: DbSession) -> BatchOut:
    """Record consumed / sold / discarded / returned / received stock, or a counted correction."""
    inv.adjust_stock(db, business_id=ctx.business_id, user_id=ctx.user_id, batch_id=batch_id, adjustment=payload)
    db.commit()
    batch = inv.get_owned(db, InventoryBatch, batch_id, ctx.business_id)
    db.refresh(batch)
    today, soon_days = tenant_today(db, ctx.business)
    return batches_out(db, [batch], today, soon_days)[0]


@router.get("/api/inventory/batches/{batch_id}/movements", response_model=Page[MovementOut])
def batch_movements(batch_id: uuid.UUID, ctx: Auth, db: DbSession, paging: Paging) -> Page[MovementOut]:
    inv.get_owned(db, InventoryBatch, batch_id, ctx.business_id)
    stmt = select(InventoryMovement).where(
        InventoryMovement.batch_id == batch_id, InventoryMovement.business_id == ctx.business_id
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(InventoryMovement.created_at.desc()).offset(paging.offset).limit(paging.page_size)
    ).all()
    return Page(items=movements_out(db, list(rows)), total=total, page=paging.page, page_size=paging.page_size)


@router.get("/api/inventory/movements", response_model=Page[MovementOut])
def recent_movements(ctx: Auth, db: DbSession, paging: Paging) -> Page[MovementOut]:
    stmt = select(InventoryMovement).where(InventoryMovement.business_id == ctx.business_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(InventoryMovement.created_at.desc()).offset(paging.offset).limit(paging.page_size)
    ).all()
    return Page(items=movements_out(db, list(rows)), total=total, page=paging.page, page_size=paging.page_size)


ALL_STATUSES = [s.value for s in ExpiryStatus]
