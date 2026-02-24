import json
import re
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.afdian_service import afdian_service
from core.config import settings
from core.dependencies import get_db, hash_password, verify_password
from core.logging_config import logger
from db.models import PaymentOrder, User

router = APIRouter()


SUPPORTED_ACTIONS = {"renew_1m", "boost_1m"}
DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


class PaymentLinkRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    action: str = Field("renew_1m")
    custom_price: Optional[float] = None


class ActivateRequest(BaseModel):
    remark: Optional[str] = None
    custom_id: Optional[str] = None
    out_trade_no: Optional[str] = None


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _extend_date(current: Optional[datetime], days: int) -> datetime:
    now_utc = _utc_now_naive()
    base = _normalize_naive_utc(current) or now_utc
    if base < now_utc:
        base = now_utc
    return base + timedelta(days=max(1, days))


def _safe_parse_amount(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
        return amount if amount >= 0 else None
    if not isinstance(value, str):
        return None

    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None

    try:
        amount = float(cleaned)
        return amount if amount >= 0 else None
    except ValueError:
        match = NUMBER_PATTERN.search(cleaned)
        if not match:
            return None
        amount = float(match.group(0))
        return amount if amount >= 0 else None


def _extract_order_amount(order: dict) -> Optional[float]:
    for key in ("show_amount", "total_amount", "amount", "pay_amount"):
        amount = _safe_parse_amount(order.get(key))
        if amount is not None:
            return amount
    return None


def _extract_daily_limit(limit_rule: Optional[str]) -> Optional[int]:
    if not limit_rule:
        return None
    for part in (segment.strip() for segment in limit_rule.split(";") if segment.strip()):
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched:
            return int(matched.group(1))
    return None


def _build_boost_limit_rule(base_rule: Optional[str], daily_increment: int) -> str:
    increment = max(0, int(daily_increment))
    parts = [segment.strip() for segment in (base_rule or "").split(";") if segment.strip()]

    if not parts:
        return f"{increment}/day"

    updated_parts = []
    day_replaced = False
    for part in parts:
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched and not day_replaced:
            current_daily = int(matched.group(1))
            updated_parts.append(f"{current_daily + increment}/day")
            day_replaced = True
        else:
            updated_parts.append(part)

    if not day_replaced:
        updated_parts.insert(0, f"{increment}/day")

    return ";".join(updated_parts)


def _calculate_boost_quota(amount: float) -> Tuple[int, int]:
    unit_price = max(0.01, float(settings.AFDIAN_BOOST_PRICE_STEP))
    unit_increment = max(1, int(settings.AFDIAN_BOOST_DAILY_INCREMENT))

    amount_decimal = Decimal(str(amount))
    unit_decimal = Decimal(str(unit_price))
    units = int((amount_decimal / unit_decimal).to_integral_value(rounding=ROUND_FLOOR))
    daily_increment = max(0, units) * unit_increment
    return units, daily_increment


def _find_local_order(
    db: Session,
    out_trade_no: Optional[str],
    order_no: Optional[str],
    custom_id: Optional[str],
) -> Optional[PaymentOrder]:
    if out_trade_no:
        order = db.query(PaymentOrder).filter(PaymentOrder.out_trade_no == out_trade_no).first()
        if order:
            return order
    if order_no:
        order = db.query(PaymentOrder).filter(PaymentOrder.order_no == order_no).first()
        if order:
            return order
    if custom_id:
        return db.query(PaymentOrder).filter(PaymentOrder.custom_id == custom_id).first()
    return None


def _ensure_order_record(
    db: Session,
    order_no: Optional[str],
    out_trade_no: Optional[str],
    custom_id: Optional[str],
) -> PaymentOrder:
    record = _find_local_order(db, out_trade_no, order_no, custom_id)
    if record is None:
        record = PaymentOrder(
            order_no=order_no or None,
            out_trade_no=out_trade_no or None,
            custom_id=custom_id or None,
            remark=custom_id or None,
            status="pending",
            created_at=_utc_now_naive(),
            updated_at=_utc_now_naive(),
        )
        db.add(record)
    return record


@router.post("/payment-link")
async def create_payment_link(payload: PaymentLinkRequest, db: Session = Depends(get_db)):
    action = (payload.action or "renew_1m").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")

    donation_amount = (
        float(settings.AFDIAN_CUSTOM_PRICE)
        if payload.custom_price is None
        else float(payload.custom_price)
    )
    if donation_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_price must be positive")
    if action == "boost_1m" and donation_amount < float(settings.AFDIAN_BOOST_PRICE_STEP):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"boost_1m 最低金额为 {float(settings.AFDIAN_BOOST_PRICE_STEP):.2f}",
        )

    username = payload.username.strip()
    password = payload.password
    user = db.query(User).filter(User.username == username).first()
    if user and not verify_password(password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    custom_id = afdian_service.build_custom_id(username, password, action)
    donation_url = afdian_service.build_donation_url(custom_id, donation_amount)

    pending = _ensure_order_record(db, order_no=None, out_trade_no=None, custom_id=custom_id)
    pending.remark = custom_id
    pending.username = username
    pending.action = action
    pending.amount = donation_amount
    pending.status = "pending"
    pending.updated_at = _utc_now_naive()
    db.commit()

    logger.info("[USER-CENTER] payment-link generated. username=%s action=%s", username, action)
    return {
        "donation_url": donation_url,
        "custom_id": custom_id,
        "remark": custom_id,
        "action": action,
    }


@router.post("/activate")
async def activate_order(payload: ActivateRequest, db: Session = Depends(get_db)):
    out_trade_no = (payload.out_trade_no or "").strip() or None
    remark_or_custom_id = (payload.custom_id or payload.remark or "").strip() or None
    if not out_trade_no and not remark_or_custom_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少订单备注/custom_id 或商家订单号")

    order, raw_query_payload = await afdian_service.find_order(out_trade_no, remark_or_custom_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未查询到对应订单")

    order_no = afdian_service.get_order_no(order) or None
    out_trade_no = afdian_service.get_out_trade_no(order) or out_trade_no
    custom_id = afdian_service.get_custom_id(order) or remark_or_custom_id
    if not custom_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单缺少custom_id/remark，无法激活")

    local_order = _ensure_order_record(db, order_no, out_trade_no, custom_id)
    if local_order.status == "activated":
        return {
            "message": "订单已激活，无需重复提交",
            "idempotent": True,
            "username": local_order.username,
            "out_trade_no": local_order.out_trade_no,
            "order_no": local_order.order_no,
            "action": local_order.action,
        }

    try:
        custom_payload = afdian_service.parse_custom_id(custom_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if afdian_service.is_custom_id_expired(custom_payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id 已过期，请重新生成发电链接")

    username = str(custom_payload.get("username") or "").strip()
    password = str(custom_payload.get("password") or "")
    action = str(custom_payload.get("action") or "").strip().lower()
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id 信息不完整")
    if action not in SUPPORTED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id action 不支持")

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        user = User(
            username=username,
            password=hash_password(password),
            is_admin=False,
            is_active=True,
            limit_rule=settings.AFDIAN_DEFAULT_LIMIT_RULE,
        )
        db.add(user)
    elif not verify_password(password, user.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id 用户密码校验失败")

    amount = _extract_order_amount(order)
    if amount is None:
        amount = _safe_parse_amount(getattr(local_order, "amount", None))

    boost_units: Optional[int] = None
    boost_daily_increment: Optional[int] = None

    if action == "renew_1m":
        user.expiration_date = _extend_date(user.expiration_date, int(settings.AFDIAN_RENEW_DAYS))
        user.is_active = True
    elif action == "boost_1m":
        if amount is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单金额解析失败，无法计算提额档位")

        boost_units, boost_daily_increment = _calculate_boost_quota(amount)
        if not boost_daily_increment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"订单金额不足 {float(settings.AFDIAN_BOOST_PRICE_STEP):.2f}，无法提升日请求上限",
            )

        user.boost_expiration_date = _extend_date(
            getattr(user, "boost_expiration_date", None),
            int(settings.AFDIAN_BOOST_DAYS),
        )
        if settings.AFDIAN_BOOST_LIMIT_RULE:
            user.boost_limit_rule = settings.AFDIAN_BOOST_LIMIT_RULE
        else:
            base_rule = (user.limit_rule or settings.AFDIAN_DEFAULT_LIMIT_RULE or "").strip()
            calculated_rule = _build_boost_limit_rule(base_rule, boost_daily_increment)
            current_boost_daily = _extract_daily_limit(user.boost_limit_rule)
            calculated_daily = _extract_daily_limit(calculated_rule)
            if not (
                current_boost_daily is not None
                and calculated_daily is not None
                and current_boost_daily > calculated_daily
            ):
                user.boost_limit_rule = calculated_rule
        user.is_active = True

    local_order.order_no = order_no
    local_order.out_trade_no = out_trade_no
    local_order.custom_id = custom_id
    local_order.remark = custom_id
    local_order.username = username
    local_order.action = action
    local_order.amount = amount
    local_order.status = "activated"
    local_order.activated_at = _utc_now_naive()
    local_order.updated_at = _utc_now_naive()
    local_order.raw_payload = json_dumps_safe(
        {
            "order": order,
            "query_payload": raw_query_payload,
            "custom_payload": custom_payload,
            "boost_units": boost_units,
            "boost_daily_increment": boost_daily_increment,
            "boost_limit_rule": getattr(user, "boost_limit_rule", None),
        }
    )

    db.commit()

    logger.info(
        "[USER-CENTER] order activated. username=%s action=%s out_trade_no=%s",
        username,
        action,
        out_trade_no,
    )
    return {
        "message": "激活成功",
        "idempotent": False,
        "username": username,
        "out_trade_no": out_trade_no,
        "order_no": order_no,
        "action": action,
        "amount": amount,
        "boost_units": boost_units,
        "boost_daily_increment": boost_daily_increment,
        "boost_limit_rule": getattr(user, "boost_limit_rule", None),
        "new_expiration_utc": _to_utc_iso(user.expiration_date),
        "boost_expiration_utc": _to_utc_iso(getattr(user, "boost_expiration_date", None)),
    }


def json_dumps_safe(payload: object) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"
